import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.audit import verify_chain
from app.crypto import decrypt_str, hash_token
from app.models import Approval, ApprovalState, AuditEvent, LinkToken, User
from tests.conftest import KEY
from tests.factories import WA_ID, make_user, phone_hash

ROUTES = [
    ("GET", f"/internal/users/by-phone/{'a' * 64}", None),
    ("POST", "/internal/link-tokens", {"phone_hash": "a" * 64, "wa_id": WA_ID}),
    ("GET", f"/internal/users/{uuid.uuid4()}/wa-target", None),
    ("POST", f"/internal/users/{uuid.uuid4()}/freeze", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
@pytest.mark.parametrize("headers", [{}, {"X-Internal-Key": "wrong"}])
async def test_every_internal_route_needs_the_key(client, method, path, body, headers):
    response = await client.request(method, path, json=body, headers=headers)
    assert response.status_code == 401


# --- by-phone ---


async def test_by_phone_finds_the_user(client, session):
    user = await make_user(session, linked=True, frozen=False)
    response = await client.get(f"/internal/users/by-phone/{user.phone_hash}", headers=KEY)
    assert response.json() == {"user_id": str(user.id), "linked": True, "frozen": False}


async def test_by_phone_unknown_is_404(client):
    response = await client.get(f"/internal/users/by-phone/{phone_hash()}", headers=KEY)
    assert response.status_code == 404


# --- link tokens ---


async def test_link_token_stores_only_hash_and_encrypted_wa_id(client, session):
    ph = phone_hash()
    response = await client.post(
        "/internal/link-tokens", json={"phone_hash": ph, "wa_id": WA_ID}, headers=KEY
    )
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("http://localhost:8003/link?t=")
    token = body["url"].split("t=")[1]

    row = await session.scalar(select(LinkToken).where(LinkToken.phone_hash == ph))
    assert row.token_hash == hash_token(token)  # the token itself is never stored
    assert WA_ID not in row.wa_id_encrypted and decrypt_str(row.wa_id_encrypted) == WA_ID
    expires_in = (row.expires_at - datetime.now(UTC)).total_seconds()
    assert 9 * 60 < expires_in <= 10 * 60


@pytest.mark.parametrize(
    "body",
    [
        {"phone_hash": "8801700001234", "wa_id": WA_ID},  # a raw phone is not a hash
        {"phone_hash": "a" * 64, "wa_id": "not-a-number"},
        {"phone_hash": "a" * 64},
    ],
)
async def test_link_token_bad_input_is_422(client, body):
    response = await client.post("/internal/link-tokens", json=body, headers=KEY)
    assert response.status_code == 422


async def test_link_tokens_limited_to_5_per_hour_per_phone(client):
    body = {"phone_hash": phone_hash(), "wa_id": WA_ID}
    codes = [
        (await client.post("/internal/link-tokens", json=body, headers=KEY)).status_code
        for _ in range(6)
    ]
    assert codes == [200] * 5 + [429]
    other = {"phone_hash": phone_hash(), "wa_id": WA_ID}
    assert (await client.post("/internal/link-tokens", json=other, headers=KEY)).status_code == 200


# --- wa-target ---


async def test_wa_target_decrypts(client, session):
    user = await make_user(session)
    response = await client.get(f"/internal/users/{user.id}/wa-target", headers=KEY)
    assert response.json() == {"wa_id": WA_ID}


async def test_wa_target_unknown_user_is_404(client):
    response = await client.get(f"/internal/users/{uuid.uuid4()}/wa-target", headers=KEY)
    assert response.status_code == 404


# --- freeze ---


async def make_approval(session, user, state=ApprovalState.PENDING):
    approval = Approval(
        user_id=user.id,
        kind="transfer",
        subject_id=uuid.uuid4(),
        payload_hash="a" * 64,
        summary={},
        token_hash=phone_hash(),
        nonce="n",
        expires_at=datetime.now(UTC),
        state=state,
    )
    session.add(approval)
    await session.flush()
    return approval


async def test_freeze_blocks_user_expires_approvals_audits_and_publishes(client, session, redis):
    user = await make_user(session)
    pending = await make_approval(session, user)
    done = await make_approval(session, user, ApprovalState.APPROVED)

    response = await client.post(f"/internal/users/{user.id}/freeze", headers=KEY)
    assert response.json() == {"frozen": True}

    session.expunge_all()
    assert (await session.get(User, user.id)).frozen is True
    assert (await session.get(Approval, pending.id)).state == ApprovalState.EXPIRED
    assert (await session.get(Approval, done.id)).state == ApprovalState.APPROVED  # untouched

    last = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    assert last.action == "user.frozen" and str(user.id) not in json.dumps(last.details)
    assert await verify_chain(session)

    [(_, fields)] = await redis.xrange("events:notify")
    event = json.loads(fields["data"])
    assert (event["type"], event["user_id"]) == ("frozen", str(user.id))
    assert WA_ID not in fields["data"]


async def test_freeze_unknown_user_is_404(client):
    response = await client.post(f"/internal/users/{uuid.uuid4()}/freeze", headers=KEY)
    assert response.status_code == 404
