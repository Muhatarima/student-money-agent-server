import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_str
from app.models import Approval, User

INTERNAL_KEY = "test-internal-key"
HEADERS = {"X-Internal-Key": INTERNAL_KEY}


@pytest.mark.asyncio
async def test_auth_header_required(client: AsyncClient):
    # Without header
    res = await client.get("/internal/users/by-phone/somehash")
    assert res.status_code == 401

    # With invalid header
    res = await client.get(
        "/internal/users/by-phone/somehash", headers={"X-Internal-Key": "wrong-key"}
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_get_user_by_phone(client: AsyncClient, db_session: AsyncSession):
    phone_h = "a" * 64
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone_hash=phone_h,
        wa_id_encrypted=encrypt_str("8801711111111"),
        linked=True,
        frozen=False,
    )
    db_session.add(user)
    await db_session.commit()

    # User found
    res = await client.get(f"/internal/users/by-phone/{phone_h}", headers=HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == str(user_id)
    assert data["linked"] is True
    assert data["frozen"] is False

    # User not found
    res = await client.get(f"/internal/users/by-phone/{'b' * 64}", headers=HEADERS)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_create_link_token_and_rate_limit(client: AsyncClient):
    payload = {"phone_hash": "c" * 64, "wa_id": "8801722222222"}

    # First 5 calls should succeed
    for _ in range(5):
        res = await client.post("/internal/link-tokens", json=payload, headers=HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert "/link?t=" in data["url"]
        assert "expires_at" in data

    # 6th call should trigger rate limit 429
    res = await client.post("/internal/link-tokens", json=payload, headers=HEADERS)
    assert res.status_code == 429


@pytest.mark.asyncio
async def test_get_wa_target(client: AsyncClient, db_session: AsyncSession):
    raw_wa_id = "8801733333333"
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone_hash="d" * 64,
        wa_id_encrypted=encrypt_str(raw_wa_id),
        linked=True,
    )
    db_session.add(user)
    await db_session.commit()

    res = await client.get(f"/internal/users/{user_id}/wa-target", headers=HEADERS)
    assert res.status_code == 200
    assert res.json() == {"wa_id": raw_wa_id}

    # 404 for unknown user
    res = await client.get(f"/internal/users/{uuid.uuid4()}/wa-target", headers=HEADERS)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_freeze_user(client: AsyncClient, db_session: AsyncSession):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone_hash="e" * 64,
        wa_id_encrypted=encrypt_str("8801744444444"),
        linked=True,
        frozen=False,
    )
    db_session.add(user)

    # Add a pending approval and an already approved approval
    app1 = Approval(
        id=uuid.uuid4(),
        user_id=user_id,
        kind="transfer",
        subject_id=uuid.uuid4(),
        payload_hash="p" * 64,
        summary={"amount": "100"},
        token_hash="tok1" * 16,
        nonce="n1",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        state="pending",
    )
    app2 = Approval(
        id=uuid.uuid4(),
        user_id=user_id,
        kind="transfer",
        subject_id=uuid.uuid4(),
        payload_hash="p2" * 64,
        summary={"amount": "200"},
        token_hash="tok2" * 16,
        nonce="n2",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        state="approved",
    )
    db_session.add_all([app1, app2])
    await db_session.commit()

    # Freeze the user
    res = await client.post(f"/internal/users/{user_id}/freeze", headers=HEADERS)
    assert res.status_code == 200
    assert res.json()["status"] == "frozen"

    # Refresh objects from DB
    await db_session.refresh(user)
    await db_session.refresh(app1)
    await db_session.refresh(app2)

    assert user.frozen is True
    assert app1.state == "expired"  # Pending approval was expired
    assert app2.state == "approved"  # Previously approved remains approved
