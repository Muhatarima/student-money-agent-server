import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_str
from app.models import Passkey, Signature, User

INTERNAL_KEY = "test-internal-key"
HEADERS = {"X-Internal-Key": INTERNAL_KEY}


@pytest.mark.asyncio
async def test_verify_signature_scenarios(client: AsyncClient, db_session: AsyncSession):
    user_id = uuid.uuid4()
    wrong_user_id = uuid.uuid4()
    cred_id = b"test-cred-bytes-100"
    pub_key = b"test-pub-key-200"
    payload_hash = "h" * 64
    wrong_payload_hash = "w" * 64
    nonce = "n" * 32

    # Create users
    user = User(
        id=user_id,
        phone_hash="hash_sig_user" * 8,
        wa_id_encrypted=encrypt_str("8801700000000"),
        linked=True,
        frozen=False,
    )
    wrong_user = User(
        id=wrong_user_id,
        phone_hash="hash_wrong_user" * 8,
        wa_id_encrypted=encrypt_str("8801700000001"),
        linked=True,
        frozen=False,
    )
    # Create passkey
    passkey = Passkey(
        id=uuid.uuid4(),
        user_id=user_id,
        credential_id=cred_id,
        public_key=pub_key,
        sign_count=5,
    )
    # Create signature row
    sig_id = uuid.uuid4()
    signature_row = Signature(
        id=sig_id,
        user_id=user_id,
        approval_id=uuid.uuid4(),
        credential_id=cred_id,
        payload_hash=payload_hash,
        nonce=nonce,
        authenticator_data=b"auth_data_bytes",
        client_data_json=b"client_data_bytes",
        signature=b"sig_bytes_12345",
        created_at=datetime.now(UTC),
    )
    db_session.add_all([user, wrong_user, passkey, signature_row])
    await db_session.commit()

    base_req = {
        "user_id": str(user_id),
        "signature_id": str(sig_id),
        "payload_hash": payload_hash,
    }

    mock_verified = MagicMock()

    # 1. Happy path: valid signature
    with patch("app.signatures.verify_authentication_response", return_value=mock_verified):
        res = await client.post("/internal/verify-signature", json=base_req, headers=HEADERS)
        assert res.status_code == 200
        assert res.json() == {"valid": True}

    # 2. Wrong payload_hash -> valid is False
    with patch("app.signatures.verify_authentication_response", return_value=mock_verified):
        res = await client.post(
            "/internal/verify-signature",
            json={**base_req, "payload_hash": wrong_payload_hash},
            headers=HEADERS,
        )
        assert res.status_code == 200
        assert res.json() == {"valid": False}

    # 3. Wrong user_id -> valid is False
    with patch("app.signatures.verify_authentication_response", return_value=mock_verified):
        res = await client.post(
            "/internal/verify-signature",
            json={**base_req, "user_id": str(wrong_user_id)},
            headers=HEADERS,
        )
        assert res.status_code == 200
        assert res.json() == {"valid": False}

    # 4. User is frozen -> valid is False
    user.frozen = True
    await db_session.commit()
    with patch("app.signatures.verify_authentication_response", return_value=mock_verified):
        res = await client.post("/internal/verify-signature", json=base_req, headers=HEADERS)
        assert res.status_code == 200
        assert res.json() == {"valid": False}

    # Unfreeze user
    user.frozen = False
    await db_session.commit()

    # 5. Deleted passkey -> valid is False
    await db_session.delete(passkey)
    await db_session.commit()
    with patch("app.signatures.verify_authentication_response", return_value=mock_verified):
        res = await client.post("/internal/verify-signature", json=base_req, headers=HEADERS)
        assert res.status_code == 200
        assert res.json() == {"valid": False}
