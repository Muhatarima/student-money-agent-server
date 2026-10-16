import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_str
from app.jobs import purge_expired_records
from app.models import Approval, Document, LinkToken, User
from app.storage import get_storage
from app.vault import generate_csrf_token, sign_vault_session

INTERNAL_KEY = "test-internal-key"
HEADERS = {"X-Internal-Key": INTERNAL_KEY}

# Minimal valid PDF header bytes
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<\n>>\nendobj\ntrailer\n<<\n>>\n%%EOF"
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
)
# Fake EXE binary (MZ header)
EXE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"


@pytest.mark.asyncio
async def test_security_headers_present(client: AsyncClient):
    res = await client.get("/health")
    assert res.status_code == 200
    assert "default-src 'self'" in res.headers["content-security-policy"]
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_create_upload_link(client: AsyncClient, db_session: AsyncSession):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone_hash="vault_user_hash" * 4,
        wa_id_encrypted=encrypt_str("8801700000099"),
        linked=True,
    )
    db_session.add(user)
    await db_session.commit()

    res = await client.post(
        "/internal/upload-links",
        json={"user_id": str(user_id)},
        headers=HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert "/upload/" in data["url"]


@pytest.mark.asyncio
async def test_upload_without_session_rejected(client: AsyncClient):
    upload_id = uuid.uuid4()
    files = {"file": ("test.pdf", io.BytesIO(PDF_BYTES), "application/pdf")}
    data = {"doc_type": "passport", "csrf_token": "some-csrf"}

    res = await client.post(f"/upload/{upload_id}", data=data, files=files)
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_upload_wrong_file_type_rejected(client: AsyncClient, db_session: AsyncSession):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone_hash="vault_user_type" * 4,
        wa_id_encrypted="enc",
        linked=True,
    )
    db_session.add(user)
    await db_session.commit()

    upload_id = uuid.uuid4()
    session_val = sign_vault_session(user_id)
    csrf = generate_csrf_token(session_val)
    client.cookies.set("vault_session", session_val)

    # Renamed EXE as .pdf (magic bytes detection must catch this!)
    files = {"file": ("malicious.pdf", io.BytesIO(EXE_BYTES), "application/pdf")}
    data = {"doc_type": "passport", "csrf_token": csrf}

    res = await client.post(f"/upload/{upload_id}", data=data, files=files)
    assert res.status_code == 400
    assert "Unsupported file type" in res.json()["detail"]


@pytest.mark.asyncio
async def test_upload_too_large_rejected(client: AsyncClient, db_session: AsyncSession):
    user_id = uuid.uuid4()
    session_val = sign_vault_session(user_id)
    csrf = generate_csrf_token(session_val)
    client.cookies.set("vault_session", session_val)

    # 11 MB fake payload
    huge_payload = PDF_BYTES + b"0" * (11 * 1024 * 1024)
    files = {"file": ("huge.pdf", io.BytesIO(huge_payload), "application/pdf")}
    data = {"doc_type": "passport", "csrf_token": csrf}

    res = await client.post(f"/upload/{uuid.uuid4()}", data=data, files=files)
    assert res.status_code == 413


@pytest.mark.asyncio
async def test_upload_success_and_envelope_encryption(
    client: AsyncClient, db_session: AsyncSession
):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone_hash="vault_user_good" * 4,
        wa_id_encrypted="enc",
        linked=True,
    )
    db_session.add(user)
    await db_session.commit()

    upload_id = uuid.uuid4()
    session_val = sign_vault_session(user_id)
    csrf = generate_csrf_token(session_val)
    client.cookies.set("vault_session", session_val)

    files = {"file": ("student_id.png", io.BytesIO(PNG_BYTES), "image/png")}
    data = {"doc_type": "passport", "csrf_token": csrf}

    res = await client.post(f"/upload/{upload_id}", data=data, files=files)
    assert res.status_code == 200
    assert "Document Stored Securely" in res.text

    # Verify Document persisted in DB
    stmt = select(Document).where(Document.user_id == user_id)
    res_db = await db_session.execute(stmt)
    doc = res_db.scalar_one()

    assert doc.doc_type == "passport"
    assert doc.wrapped_key is not None
    assert doc.storage_key is not None
    assert doc.size_bytes == len(PNG_BYTES)

    # Verify bytes stored in storage backend
    storage = get_storage()
    stored_bytes = await storage.get(doc.storage_key)
    # Stored bytes are encrypted (not plaintext PNG header)
    assert stored_bytes != PNG_BYTES


@pytest.mark.asyncio
async def test_purge_expired_records(db_session: AsyncSession):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone_hash="vault_user_purge" * 4,
        wa_id_encrypted="enc",
        linked=True,
    )
    db_session.add(user)

    now = datetime.now(UTC)
    storage = get_storage()
    storage_key = f"purge-test-{uuid.uuid4()}"
    await storage.put(storage_key, b"encrypted-document-payload")

    # Document past delete_after
    doc = Document(
        id=uuid.uuid4(),
        user_id=user_id,
        doc_type="visa",
        storage_key=storage_key,
        wrapped_key=b"sample-wrapped-key",
        content_type="application/pdf",
        size_bytes=100,
        delete_after=now - timedelta(days=1),
        created_at=now - timedelta(days=8),
    )

    # Pending approval past expires_at
    app_exp = Approval(
        id=uuid.uuid4(),
        user_id=user_id,
        kind="transfer",
        subject_id=uuid.uuid4(),
        payload_hash="p" * 64,
        summary={"amt": "100"},
        token_hash="tok_purge_1" * 5,
        nonce="n1",
        expires_at=now - timedelta(minutes=5),
        state="pending",
    )

    # Pending link token past expires_at
    lt_exp = LinkToken(
        id=uuid.uuid4(),
        token_hash="tok_purge_2" * 5,
        phone_hash="phone",
        wa_id_encrypted="enc",
        expires_at=now - timedelta(minutes=5),
    )

    db_session.add_all([doc, app_exp, lt_exp])
    await db_session.commit()

    # Run purge cycle
    counts = await purge_expired_records(db_session)
    assert counts["purged_documents"] >= 1
    assert counts["expired_approvals"] >= 1
    assert counts["expired_tokens"] >= 1

    # Check that wrapped_key is zeroed out and deleted_at is set
    await db_session.refresh(doc)
    assert doc.wrapped_key is None
    assert doc.deleted_at is not None

    # Check that approval state is now expired
    await db_session.refresh(app_exp)
    assert app_exp.state == "expired"

    # Check that storage file was deleted
    with pytest.raises(FileNotFoundError):
        await storage.get(storage_key)
