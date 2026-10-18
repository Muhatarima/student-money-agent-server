import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from contracts.hashing import payload_hash
from sqlalchemy import select, update

from app import approvals, money_client
from app.audit import verify_chain
from app.models import Approval, ApprovalState, AuditEvent, Passkey, Signature, User
from tests.conftest import KEY, ORIGIN
from tests.factories import make_user
from tests.fake_webauthn import challenge_of, make_credential
from tests.mocks import mock_money

SUMMARY = {
    "from_currency": "BDT",
    "to_currency": "IDR",
    "amount": "1000.00",
    "rate": "135.00000000",
    "fee": "20.00",
    "amount_out": "132300.00",
    "expires_at": "2099-01-01T00:00:00+00:00",
    "provider": "mock",
}


@pytest.fixture(autouse=True)
def money(monkeypatch):
    mock_money.confirmed.clear()
    mock_money.answer["status"] = 200
    monkeypatch.setattr(money_client, "transport", httpx.ASGITransport(app=mock_money.app))
    return mock_money


async def linked_user(session, credential_id=None):
    user = await make_user(session)
    credential_id = credential_id or uuid.uuid4().bytes
    session.add(
        Passkey(user_id=user.id, credential_id=credential_id, public_key=b"pk-" + credential_id)
    )
    await session.flush()
    return user, credential_id


def create_body(user, summary=SUMMARY, kind="transfer", minutes=1):
    return {
        "user_id": str(user.id),
        "kind": kind,
        "subject_id": str(uuid.uuid4()),
        "payload_hash": payload_hash(summary),
        "summary": summary,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(),
    }


async def create(client, user, **kwargs):
    response = await client.post(
        "/internal/approvals", json=create_body(user, **kwargs), headers=KEY
    )
    assert response.status_code == 200, response.text
    url = response.json()["url"]
    approval_id = url.split("/approve/")[1].split("?")[0]
    return approval_id, url.split("t=")[1]


async def sign(client, approval_id, token, credential_id):
    """What the browser does: get options, touch the sensor, send the answer."""
    response = await client.post(
        f"/approve/{approval_id}/options", json={"t": token}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    credential = make_credential(challenge_of(response.json()), credential_id)
    body = {"t": token, "credential": credential}
    return await client.post(f"/approve/{approval_id}/verify", json=body, headers=ORIGIN)


# --- Creating ---


async def test_create_needs_the_internal_key(client, session):
    user, _ = await linked_user(session)
    response = await client.post("/internal/approvals", json=create_body(user))
    assert response.status_code == 401


async def test_create_stores_only_token_hash_and_caps_expiry(client, session):
    user, _ = await linked_user(session)
    approval_id, token = await create(client, user, minutes=60)
    approval = await session.get(Approval, uuid.UUID(approval_id))
    assert token not in approval.token_hash
    assert approval.expires_at <= datetime.now(UTC) + timedelta(minutes=10)  # capped at 10 min
    assert approval.nonce and approval.state == ApprovalState.PENDING


@pytest.mark.parametrize("change", [{"frozen": True}, {"linked": False}])
async def test_create_refused_for_frozen_or_unlinked_user(client, session, change):
    user = await make_user(session, **change)
    response = await client.post("/internal/approvals", json=create_body(user), headers=KEY)
    assert response.status_code == 409


async def test_create_unknown_user_is_409(client):
    body = create_body(User(id=uuid.uuid4()))
    assert (await client.post("/internal/approvals", json=body, headers=KEY)).status_code == 409


# --- The page ---


async def test_page_shows_exactly_the_summary(client, session):
    user, _ = await linked_user(session)
    approval_id, token = await create(client, user)
    response = await client.get(f"/approve/{approval_id}?t={token}")
    assert response.status_code == 200
    for value in ["1000.00", "132300.00", "135.00000000", "20.00", "mock", "BDT → IDR"]:
        assert value in response.text
    assert "Approve with fingerprint" in response.text and "data-expires-at" in response.text


async def test_rule_page_shows_rule_fields_and_warning(client, session):
    user, _ = await linked_user(session)
    rule = {
        "from_currency": "BDT",
        "to_currency": "IDR",
        "target_rate": "136",
        "amount_per_run": "500",
        "max_per_month": "5000",
        "valid_until": "2099-01-01T00:00:00+00:00",
    }
    approval_id, token = await create(client, user, summary=rule, kind="rule")
    text = (await client.get(f"/approve/{approval_id}?t={token}")).text
    assert "136" in text and "5000" in text
    assert "run automatically within these limits until you freeze it" in text


async def test_wrong_token_page_is_404(client, session):
    user, _ = await linked_user(session)
    approval_id, _ = await create(client, user)
    assert (await client.get(f"/approve/{approval_id}?t=wrong")).status_code == 404


async def test_expired_page(client, session):
    user, _ = await linked_user(session)
    approval_id, token = await create(client, user)
    await session.execute(update(Approval).values(expires_at=datetime.now(UTC)))
    response = await client.get(f"/approve/{approval_id}?t={token}")
    assert response.status_code == 410 and "expired" in response.text


# --- Approving ---


async def test_happy_path_signs_and_tells_money(client, session):
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user)
    response = await sign(client, approval_id, token, cid)
    assert response.json() == {"ok": True, "redirect": "/approved"}

    approval = await session.get(Approval, uuid.UUID(approval_id))
    assert approval.state == ApprovalState.APPROVED
    signature = await session.scalar(select(Signature).where(Signature.approval_id == approval.id))
    assert signature.payload_hash == approval.payload_hash and signature.nonce == approval.nonce
    assert signature.credential_id == cid

    [confirmed] = mock_money.confirmed
    assert confirmed.approval_id == approval.id and confirmed.subject_id == approval.subject_id
    assert confirmed.payload_hash == approval.payload_hash
    assert confirmed.signature_id == signature.id
    actions = (await session.scalars(select(AuditEvent.action))).all()
    assert "approval.created" in actions and "approval.approved" in actions
    assert await verify_chain(session)


async def test_challenge_is_bound_to_payload_hash_and_nonce(client, session):
    user, _ = await linked_user(session)
    approval_id, token = await create(client, user)
    options = await client.post(
        f"/approve/{approval_id}/options", json={"t": token}, headers=ORIGIN
    )
    approval = await session.get(Approval, uuid.UUID(approval_id))
    assert challenge_of(options.json()) == approvals.challenge_for(approval)
    assert (
        approvals.challenge_for(approval)
        == hashlib.sha256((approval.payload_hash + approval.nonce).encode()).digest()
    )


async def test_tampered_summary_in_db_cannot_be_approved(client, session):
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user)
    tampered = SUMMARY | {"amount_out": "999999.00"}
    await session.execute(update(Approval).values(summary=tampered))
    response = await client.post(
        f"/approve/{approval_id}/options", json={"t": token}, headers=ORIGIN
    )
    assert response.status_code == 409
    assert (await client.get(f"/approve/{approval_id}?t={token}")).status_code == 409
    assert mock_money.confirmed == []


async def test_signature_for_another_challenge_is_refused(client, session):
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user)
    credential = make_credential(b"a different payload", cid)
    body = {"t": token, "credential": credential}
    response = await client.post(f"/approve/{approval_id}/verify", json=body, headers=ORIGIN)
    assert response.status_code == 400
    assert mock_money.confirmed == []


async def test_token_reuse_is_409(client, session):
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user)
    assert (await sign(client, approval_id, token, cid)).status_code == 200
    options = await client.post(
        f"/approve/{approval_id}/options", json={"t": token}, headers=ORIGIN
    )
    assert options.status_code == 409
    assert len(mock_money.confirmed) == 1


async def test_expired_never_calls_money(client, session):
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user)
    await session.execute(update(Approval).values(expires_at=datetime.now(UTC)))
    credential = make_credential(b"x", cid)
    body = {"t": token, "credential": credential}
    response = await client.post(f"/approve/{approval_id}/verify", json=body, headers=ORIGIN)
    assert response.status_code == 410
    assert mock_money.confirmed == []


async def test_frozen_user_is_refused(client, session):
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user)
    await session.execute(update(User).where(User.id == user.id).values(frozen=True))
    response = await client.post(
        f"/approve/{approval_id}/options", json={"t": token}, headers=ORIGIN
    )
    assert response.status_code == 403
    assert mock_money.confirmed == []


async def test_another_users_passkey_is_refused(client, session):
    user, _ = await linked_user(session)
    _, other_cid = await linked_user(session)
    approval_id, token = await create(client, user)
    response = await sign(client, approval_id, token, other_cid)
    assert response.status_code == 400
    assert mock_money.confirmed == []


async def test_money_refusing_shows_failed_and_is_audited(client, session):
    mock_money.answer["status"] = 409
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user)
    response = await sign(client, approval_id, token, cid)
    assert response.json() == {"ok": False, "redirect": "/approve-failed"}
    actions = (await session.scalars(select(AuditEvent.action))).all()
    assert "approval.money_refused" in actions
    assert "no money was moved" in (await client.get("/approve-failed")).text


async def test_money_unreachable_counts_as_refused(client, session, monkeypatch):
    def down(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(money_client, "transport", httpx.MockTransport(down))
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user)
    assert (await sign(client, approval_id, token, cid)).json()["ok"] is False


# --- Cancel ---


async def test_cancel(client, session):
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user)
    response = await client.post(
        f"/approve/{approval_id}/cancel", json={"t": token}, headers=ORIGIN
    )
    assert response.json()["redirect"] == "/cancelled"
    approval = await session.get(Approval, uuid.UUID(approval_id))
    assert approval.state == ApprovalState.REJECTED
    # Cancelled can't be approved any more
    options = await client.post(
        f"/approve/{approval_id}/options", json={"t": token}, headers=ORIGIN
    )
    assert options.status_code == 409


async def test_cancel_with_wrong_token_is_409(client, session):
    user, _ = await linked_user(session)
    approval_id, _ = await create(client, user)
    response = await client.post(f"/approve/{approval_id}/cancel", json={"t": "x"}, headers=ORIGIN)
    assert response.status_code == 409
