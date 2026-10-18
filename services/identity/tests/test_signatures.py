import uuid

import pytest
from sqlalchemy import delete, select, update

from app.models import AuditEvent, Passkey, Signature, User
from tests.conftest import KEY
from tests.mocks import mock_money
from tests.test_approvals import create, linked_user, money, sign  # noqa: F401 (money fixture)


async def signed(client, session):
    """A user who approved something: returns what Money would later send us."""
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user, kind="rule")
    assert (await sign(client, approval_id, token, cid)).status_code == 200
    [confirmed] = mock_money.confirmed
    return user, {
        "user_id": str(user.id),
        "signature_id": str(confirmed.signature_id),
        "payload_hash": confirmed.payload_hash,
    }


async def verify(client, body):
    response = await client.post("/internal/verify-signature", json=body, headers=KEY)
    assert response.status_code == 200
    return response.json()["valid"]


async def last_action(session):
    return await session.scalar(select(AuditEvent.action).order_by(AuditEvent.id.desc()).limit(1))


async def test_needs_the_internal_key(client):
    body = {"user_id": str(uuid.uuid4()), "signature_id": str(uuid.uuid4()), "payload_hash": "a"}
    assert (await client.post("/internal/verify-signature", json=body)).status_code == 401


async def test_valid_signature(client, session):
    _, body = await signed(client, session)
    assert await verify(client, body) is True
    assert await last_action(session) == "signature.verified"
    assert await verify(client, body) is True  # checked again on every run: still fine


async def test_wrong_payload_hash(client, session):
    _, body = await signed(client, session)
    assert await verify(client, body | {"payload_hash": "f" * 64}) is False
    assert await last_action(session) == "signature.rejected"


async def test_frozen_user(client, session):
    user, body = await signed(client, session)
    await session.execute(update(User).where(User.id == user.id).values(frozen=True))
    assert await verify(client, body) is False


async def test_deleted_passkey(client, session):
    user, body = await signed(client, session)
    await session.execute(delete(Passkey).where(Passkey.user_id == user.id))
    assert await verify(client, body) is False


async def test_wrong_user(client, session):
    _, body = await signed(client, session)
    other, _ = await linked_user(session)
    assert await verify(client, body | {"user_id": str(other.id)}) is False


async def test_unknown_signature(client, session):
    _, body = await signed(client, session)
    assert await verify(client, body | {"signature_id": str(uuid.uuid4())}) is False


@pytest.mark.parametrize("column", ["signature", "client_data_json"])
async def test_tampered_stored_assertion(client, session, column):
    # The crypto is re-checked: a changed signature or signed data no longer verifies
    _, body = await signed(client, session)
    await session.execute(update(Signature).values({column: b"tampered"}))
    assert await verify(client, body) is False


async def test_crash_inside_check_is_invalid(client, session, monkeypatch):
    _, body = await signed(client, session)

    def boom(**kwargs):
        raise RuntimeError("unexpected")

    from app import signatures

    monkeypatch.setattr(signatures, "verify_authentication_response", boom)
    assert await verify(client, body) is False  # fail closed, not a 500
