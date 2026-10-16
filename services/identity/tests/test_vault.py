import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update

from app import jobs, vault
from app.config import settings
from app.models import (
    Approval,
    ApprovalState,
    AuditEvent,
    Document,
    LinkToken,
    Passkey,
    UploadLink,
)
from app.storage import LocalStorage
from tests.conftest import KEY, ORIGIN
from tests.factories import make_user, phone_hash
from tests.fake_webauthn import challenge_of, make_credential

PDF = b"%PDF-1.7\n" + b"secret passport scan " * 50
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
EXE = b"MZ\x90\x00" + b"\x00" * 100  # a Windows program


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    local = LocalStorage(tmp_path)
    monkeypatch.setattr(vault, "storage", local)
    monkeypatch.setattr(jobs, "storage", local)
    return local


async def user_with_passkey(session):
    user = await make_user(session)
    cid = uuid.uuid4().bytes
    session.add(Passkey(user_id=user.id, credential_id=cid, public_key=b"pk-" + cid))
    await session.flush()
    return user, cid


async def new_upload_link(client, user):
    response = await client.post(
        "/internal/upload-links", json={"user_id": str(user.id)}, headers=KEY
    )
    assert response.status_code == 200, response.text
    url = response.json()["url"]
    return uuid.UUID(url.split("/upload/")[1].split("?")[0]), url.split("t=")[1]


async def login(client, link_id, token, cid):
    """Passkey check on the upload page. The client keeps the session cookie."""
    options = await client.post(f"/upload/{link_id}/options", json={"t": token}, headers=ORIGIN)
    credential = make_credential(challenge_of(options.json()), cid)
    body = {"t": token, "credential": credential}
    return await client.post(f"/upload/{link_id}/verify", json=body, headers=ORIGIN)


async def logged_in(client, session):
    user, cid = await user_with_passkey(session)
    link_id, token = await new_upload_link(client, user)
    assert (await login(client, link_id, token, cid)).status_code == 200
    page = (await client.get(f"/upload/{link_id}?t={token}")).text
    csrf = page.split('name="csrf" value="')[1].split('"')[0]
    return user, link_id, token, csrf


async def send_file(client, link_id, token, csrf, data=PDF, doc_type="passport", headers=ORIGIN):
    return await client.post(
        f"/upload/{link_id}",
        data={"t": token, "csrf": csrf, "doc_type": doc_type},
        files={"file": ("my passport.pdf", data, "application/pdf")},
        headers=headers,
    )


# --- Upload links ---


async def test_upload_link_needs_the_key(client, session):
    user = await make_user(session)
    response = await client.post("/internal/upload-links", json={"user_id": str(user.id)})
    assert response.status_code == 401


async def test_upload_link_refused_for_frozen_user(client, session):
    user = await make_user(session, frozen=True)
    response = await client.post(
        "/internal/upload-links", json={"user_id": str(user.id)}, headers=KEY
    )
    assert response.status_code == 409


async def test_page_first_asks_for_the_fingerprint(client, session):
    user, _ = await user_with_passkey(session)
    link_id, token = await new_upload_link(client, user)
    page = (await client.get(f"/upload/{link_id}?t={token}")).text
    assert "Confirm with fingerprint" in page and "<form" not in page


async def test_wrong_token_page_is_410(client, session):
    user, _ = await user_with_passkey(session)
    link_id, _ = await new_upload_link(client, user)
    assert (await client.get(f"/upload/{link_id}?t=wrong")).status_code == 410


# --- Passkey session ---


async def test_login_sets_a_strict_httponly_cookie(client, session):
    user, cid = await user_with_passkey(session)
    link_id, token = await new_upload_link(client, user)
    response = await login(client, link_id, token, cid)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=600" in cookie
    assert "Secure" not in cookie  # plain http://localhost in tests


async def test_login_with_someone_elses_passkey_fails(client, session):
    user, _ = await user_with_passkey(session)
    _, other_cid = await user_with_passkey(session)
    link_id, token = await new_upload_link(client, user)
    assert (await login(client, link_id, token, other_cid)).status_code == 400


async def test_upload_without_passkey_session_is_rejected(client, session):
    user, _ = await user_with_passkey(session)
    link_id, token = await new_upload_link(client, user)
    response = await send_file(client, link_id, token, "anything")
    assert response.status_code == 401


async def test_wrong_csrf_is_rejected(client, session):
    _, link_id, token, _ = await logged_in(client, session)
    assert (await send_file(client, link_id, token, "wrong")).status_code == 403


async def test_upload_from_another_site_is_rejected(client, session):
    _, link_id, token, csrf = await logged_in(client, session)
    evil = {"Origin": "https://evil.example"}
    assert (await send_file(client, link_id, token, csrf, headers=evil)).status_code == 403


# --- The upload ---


async def test_pdf_is_stored_encrypted_with_its_own_key(client, session, store):
    user, link_id, token, csrf = await logged_in(client, session)
    response = await send_file(client, link_id, token, csrf)
    assert response.status_code == 200 and "auto-deletes on" in response.text

    doc = await session.scalar(select(Document).where(Document.user_id == user.id))
    assert doc.doc_type == "passport" and doc.content_type == "application/pdf"
    assert "passport" not in doc.storage_key  # random id, never the file name
    stored = store.get(doc.storage_key)
    assert b"secret passport scan" not in stored  # encrypted at rest
    # Envelope: master key -> this document's key -> the file
    master = Fernet(settings.vault_master_key.get_secret_value())
    assert Fernet(master.decrypt(doc.wrapped_key)).decrypt(stored) == PDF
    keep = doc.delete_after - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < keep <= timedelta(days=7)

    event = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    assert event.action == "document.uploaded" and event.details == {"doc_type": "passport"}


async def test_png_is_accepted(client, session):
    _, link_id, token, csrf = await logged_in(client, session)
    assert (await send_file(client, link_id, token, csrf, data=PNG)).status_code == 200


async def test_exe_renamed_to_pdf_is_rejected(client, session, store):
    _, link_id, token, csrf = await logged_in(client, session)
    response = await send_file(client, link_id, token, csrf, data=EXE)
    assert response.status_code == 415
    assert list(store.root.iterdir()) == []  # nothing stored


async def test_too_large_is_rejected(client, session):
    _, link_id, token, csrf = await logged_in(client, session)
    big = b"%PDF-1.7\n" + b"0" * vault.MAX_BYTES
    assert (await send_file(client, link_id, token, csrf, data=big)).status_code == 413


async def test_unknown_doc_type_is_rejected(client, session):
    _, link_id, token, csrf = await logged_in(client, session)
    response = await send_file(client, link_id, token, csrf, doc_type="bank_password")
    assert response.status_code == 422


async def test_link_is_single_use(client, session):
    _, link_id, token, csrf = await logged_in(client, session)
    assert (await send_file(client, link_id, token, csrf)).status_code == 200
    assert (await send_file(client, link_id, token, csrf)).status_code == 410


# --- Purge job ---


async def test_purge_removes_file_and_wrapped_key(client, session, store):
    user, link_id, token, csrf = await logged_in(client, session)
    await send_file(client, link_id, token, csrf)
    doc = await session.scalar(select(Document).where(Document.user_id == user.id))
    await session.execute(
        update(Document).where(Document.id == doc.id).values(delete_after=datetime.now(UTC))
    )

    await jobs.purge(session)
    session.expunge_all()
    doc = await session.get(Document, doc.id)
    assert doc.wrapped_key is None and doc.deleted_at is not None
    assert list(store.root.iterdir()) == []
    actions = (await session.scalars(select(AuditEvent.action))).all()
    assert "document.purged" in actions


async def test_purge_keeps_documents_not_due_yet(client, session, store):
    user, link_id, token, csrf = await logged_in(client, session)
    await send_file(client, link_id, token, csrf)
    await jobs.purge(session)
    doc = await session.scalar(select(Document).where(Document.user_id == user.id))
    assert doc.wrapped_key is not None and len(list(store.root.iterdir())) == 1


async def test_purge_cleans_expired_links_and_approvals(session):
    user = await make_user(session)
    past = datetime.now(UTC) - timedelta(seconds=1)
    session.add(
        LinkToken(
            token_hash=phone_hash(), phone_hash=phone_hash(), wa_id_encrypted="x", expires_at=past
        )
    )
    session.add(UploadLink(user_id=user.id, token_hash=phone_hash(), expires_at=past))
    approval = Approval(
        user_id=user.id,
        kind="transfer",
        subject_id=uuid.uuid4(),
        payload_hash="a" * 64,
        summary={},
        token_hash=phone_hash(),
        nonce="n",
        expires_at=past,
    )
    session.add(approval)
    await session.flush()

    await jobs.purge(session)
    session.expunge_all()
    assert (
        await session.scalars(select(LinkToken).where(LinkToken.expires_at <= past))
    ).all() == []
    assert (
        await session.scalars(select(UploadLink).where(UploadLink.user_id == user.id))
    ).all() == []
    assert (await session.get(Approval, approval.id)).state == ApprovalState.EXPIRED
