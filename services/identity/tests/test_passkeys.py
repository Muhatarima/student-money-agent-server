import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_str, hash_token
from app.models import LinkToken, Passkey, User
from app.passkeys import finish_auth, start_auth


@pytest.mark.asyncio
async def test_get_link_page_valid(client: AsyncClient, db_session: AsyncSession):
    raw_token = "valid-token-12345"
    token_h = hash_token(raw_token)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)

    lt = LinkToken(
        token_hash=token_h,
        phone_hash="phone" * 12,
        wa_id_encrypted=encrypt_str("8801700000000"),
        expires_at=expires_at,
    )
    db_session.add(lt)
    await db_session.commit()

    res = await client.get(f"/link?t={raw_token}")
    assert res.status_code == 200
    assert "Secure Your Account" in res.text


@pytest.mark.asyncio
async def test_get_link_page_expired_or_used(client: AsyncClient, db_session: AsyncSession):
    # Expired token
    t_exp = "expired-token-1"
    lt_exp = LinkToken(
        token_hash=hash_token(t_exp),
        phone_hash="phone1" * 10,
        wa_id_encrypted=encrypt_str("8801700000001"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    # Used token
    t_used = "used-token-2"
    lt_used = LinkToken(
        token_hash=hash_token(t_used),
        phone_hash="phone2" * 10,
        wa_id_encrypted=encrypt_str("8801700000002"),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        used_at=datetime.now(UTC),
    )
    db_session.add_all([lt_exp, lt_used])
    await db_session.commit()

    res_exp = await client.get(f"/link?t={t_exp}")
    assert res_exp.status_code == 410

    res_used = await client.get(f"/link?t={t_used}")
    assert res_used.status_code == 410

    res_none = await client.get("/link?t=nonexistent")
    assert res_none.status_code == 410


@pytest.mark.asyncio
async def test_get_done_page(client: AsyncClient):
    res = await client.get("/done")
    assert res.status_code == 200
    assert "All set!" in res.text


@pytest.mark.asyncio
async def test_passkey_registration_flow(client: AsyncClient, db_session: AsyncSession):
    raw_token = "reg-token-abc"
    token_h = hash_token(raw_token)
    phone_h = "phone_user_abc_" + "x" * 40
    wa_enc = encrypt_str("8801799999999")

    lt = LinkToken(
        token_hash=token_h,
        phone_hash=phone_h,
        wa_id_encrypted=wa_enc,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db_session.add(lt)
    await db_session.commit()

    # 1. Fetch options
    res_opts = await client.post("/passkeys/register/options", json={"t": raw_token})
    assert res_opts.status_code == 200
    opts_data = res_opts.json()
    assert "challenge" in opts_data
    assert opts_data["rp"]["name"] in ["Student Money Agent", "Test Agent"]

    # 2. Verify with mocked WebAuthn verify response
    fake_cred_id = b"credential-bytes-1234"
    fake_pub_key = b"public-key-bytes-5678"
    mock_outcome = MagicMock()
    mock_outcome.credential_id = fake_cred_id
    mock_outcome.credential_public_key = fake_pub_key
    mock_outcome.sign_count = 1

    fake_credential = {
        "id": "Y3JlZGVudGlhbC1ieXRlcy0xMjM0",
        "rawId": "Y3JlZGVudGlhbC1ieXRlcy0xMjM0",
        "response": {"transports": ["internal"]},
        "type": "public-key",
    }

    with patch("app.passkeys.verify_registration_response", return_value=mock_outcome):
        res_verify = await client.post(
            "/passkeys/register/verify",
            json={"t": raw_token, "credential": fake_credential},
        )
        assert res_verify.status_code == 200
        data = res_verify.json()
        assert data["status"] == "ok"
        user_id = uuid.UUID(data["user_id"])

        # Check DB user state
        stmt = select(User).where(User.id == user_id)
        u_res = await db_session.execute(stmt)
        user = u_res.scalar_one()
        assert user.linked is True
        assert user.phone_hash == phone_h

        # Check Passkey created
        pk_stmt = select(Passkey).where(Passkey.user_id == user_id)
        pk_res = await db_session.execute(pk_stmt)
        passkey = pk_res.scalar_one()
        assert passkey.credential_id == fake_cred_id
        assert passkey.public_key == fake_pub_key

    # Challenge reuse must fail (challenge was deleted in Redis)
    with patch("app.passkeys.verify_registration_response", return_value=mock_outcome):
        res_replay = await client.post(
            "/passkeys/register/verify",
            json={"t": raw_token, "credential": fake_credential},
        )
        assert res_replay.status_code == 400


@pytest.mark.asyncio
async def test_auth_helpers(db_session: AsyncSession):
    user_id = uuid.uuid4()
    cred_id = b"auth-credential-id-1"
    pub_key = b"auth-public-key-1"

    user = User(
        id=user_id,
        phone_hash="hash" * 16,
        wa_id_encrypted="enc",
        linked=True,
    )
    passkey = Passkey(
        id=uuid.uuid4(),
        user_id=user_id,
        credential_id=cred_id,
        public_key=pub_key,
        sign_count=5,
    )
    db_session.add_all([user, passkey])
    await db_session.commit()

    # 1. start_auth
    challenge = b"test-challenge-32-bytes-long-1234"
    options = await start_auth(user_id, challenge, db_session)
    assert "challenge" in options
    assert len(options["allowCredentials"]) == 1

    # 2. finish_auth with mocked verify
    mock_auth_outcome = MagicMock()
    mock_auth_outcome.new_sign_count = 6

    cred_payload = {
        "id": "YXV0aC1jcmVkZW50aWFsLWlkLTE",  # base64url of cred_id
        "response": {},
    }

    with patch("app.passkeys.verify_authentication_response", return_value=mock_auth_outcome):
        verified_pk = await finish_auth(user_id, cred_payload, challenge, db_session)
        assert verified_pk.sign_count == 6
        assert verified_pk.last_used_at is not None
