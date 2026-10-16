import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import bytes_to_base64url

from app.crypto import encrypt_str, hash_token
from app.models import Approval, Passkey, Signature, User

INTERNAL_KEY = "test-internal-key"
HEADERS = {"X-Internal-Key": INTERNAL_KEY}


@pytest.mark.asyncio
async def test_create_approval_refuses_frozen_or_unlinked(
    client: AsyncClient, db_session: AsyncSession
):
    # 1. Unlinked user
    unlinked_user_id = uuid.uuid4()
    u1 = User(
        id=unlinked_user_id,
        phone_hash="hash1" * 12,
        wa_id_encrypted=encrypt_str("8801700000001"),
        linked=False,
    )
    # 2. Frozen user
    frozen_user_id = uuid.uuid4()
    u2 = User(
        id=frozen_user_id,
        phone_hash="hash2" * 12,
        wa_id_encrypted=encrypt_str("8801700000002"),
        linked=True,
        frozen=True,
    )
    db_session.add_all([u1, u2])
    await db_session.commit()

    base_payload = {
        "kind": "transfer",
        "subject_id": str(uuid.uuid4()),
        "payload_hash": "a" * 64,
        "summary": {"amount": "5000", "currency": "BDT"},
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }

    # Unlinked -> 409
    res1 = await client.post(
        "/internal/approvals",
        json={**base_payload, "user_id": str(unlinked_user_id)},
        headers=HEADERS,
    )
    assert res1.status_code == 409

    # Frozen -> 409
    res2 = await client.post(
        "/internal/approvals",
        json={**base_payload, "user_id": str(frozen_user_id)},
        headers=HEADERS,
    )
    assert res2.status_code == 409


@pytest.mark.asyncio
async def test_create_approval_happy_path(client: AsyncClient, db_session: AsyncSession):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone_hash="hash_valid" * 10,
        wa_id_encrypted=encrypt_str("8801700000003"),
        linked=True,
        frozen=False,
    )
    db_session.add(user)
    await db_session.commit()

    payload = {
        "user_id": str(user_id),
        "kind": "transfer",
        "subject_id": str(uuid.uuid4()),
        "payload_hash": "p" * 64,
        "summary": {"amount": "1000", "currency": "BDT"},
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }

    res = await client.post("/internal/approvals", json=payload, headers=HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert "approval_id" in data
    assert "/approve/" in data["url"]


@pytest.mark.asyncio
async def test_get_approve_page(client: AsyncClient, db_session: AsyncSession):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone_hash="hash_page" * 10,
        wa_id_encrypted="enc",
        linked=True,
        frozen=False,
    )
    approval_id = uuid.uuid4()
    raw_token = "approval-token-xyz"
    approval = Approval(
        id=approval_id,
        user_id=user_id,
        kind="transfer",
        subject_id=uuid.uuid4(),
        payload_hash="p" * 64,
        summary={"amount": "2500 BDT", "fee": "10 BDT"},
        token_hash=hash_token(raw_token),
        nonce="nonce123",
        expires_at=datetime.now(UTC) + timedelta(minutes=8),
        state="pending",
    )
    db_session.add_all([user, approval])
    await db_session.commit()

    # Valid pending approval
    res = await client.get(f"/approve/{approval_id}?t={raw_token}")
    assert res.status_code == 200
    assert "Confirm Transfer" in res.text
    assert "2500 BDT" in res.text

    # Expired token
    approval.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    res_exp = await client.get(f"/approve/{approval_id}?t={raw_token}")
    assert res_exp.status_code == 410


@pytest.mark.asyncio
async def test_approval_verification_and_money_callback(
    client: AsyncClient, db_session: AsyncSession
):
    user_id = uuid.uuid4()
    cred_id = b"approval-cred-bytes-1"
    pub_key = b"approval-pub-key-1"

    user = User(
        id=user_id,
        phone_hash="hash_verify" * 10,
        wa_id_encrypted="enc",
        linked=True,
    )
    passkey = Passkey(
        id=uuid.uuid4(),
        user_id=user_id,
        credential_id=cred_id,
        public_key=pub_key,
        sign_count=10,
    )
    approval_id = uuid.uuid4()
    raw_token = "app-tok-verify"
    payload_hash = "h" * 64
    nonce = "n" * 32

    approval = Approval(
        id=approval_id,
        user_id=user_id,
        kind="transfer",
        subject_id=uuid.uuid4(),
        payload_hash=payload_hash,
        summary={"amount": "500 USD"},
        token_hash=hash_token(raw_token),
        nonce=nonce,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        state="pending",
    )
    db_session.add_all([user, passkey, approval])
    await db_session.commit()

    # 1. POST /options -> gets challenge
    res_opt = await client.post(f"/approve/{approval_id}/options", json={"t": raw_token})
    assert res_opt.status_code == 200
    expected_challenge = hashlib.sha256((payload_hash + nonce).encode("utf-8")).digest()
    assert res_opt.json()["challenge"] == bytes_to_base64url(expected_challenge)

    # 2. Mock webauthn authentication verification & Engineer B money callback
    mock_auth_outcome = MagicMock()
    mock_auth_outcome.new_sign_count = 11

    fake_credential = {
        "id": bytes_to_base64url(cred_id),
        "response": {
            "authenticatorData": bytes_to_base64url(b"auth_data"),
            "clientDataJSON": bytes_to_base64url(b"client_data"),
            "signature": bytes_to_base64url(b"sig_bytes"),
        },
    }

    mock_money_resp = MagicMock()
    mock_money_resp.status_code = 200

    from unittest.mock import AsyncMock

    mock_instance = MagicMock()
    mock_instance.__aenter__.return_value = mock_instance
    mock_instance.__aexit__.return_value = None
    mock_instance.post = AsyncMock(return_value=mock_money_resp)

    with (
        patch("app.approvals.finish_auth", return_value=passkey),
        patch("app.approvals.httpx.AsyncClient", return_value=mock_instance),
    ):
        res_verify = await client.post(
            f"/approve/{approval_id}/verify",
            json={"t": raw_token, "credential": fake_credential},
        )
        assert res_verify.status_code == 200
        assert res_verify.json()["status"] == "ok"

        # Ensure call to Engineer B occurred
        mock_instance.post.assert_called_once()
        call_url = mock_instance.post.call_args[0][0]
        assert "/internal/approvals/confirmed" in call_url

    # Check DB state: approval is approved, signature created
    await db_session.refresh(approval)
    assert approval.state == "approved"

    stmt = select(Signature).where(Signature.approval_id == approval_id)
    sig_res = await db_session.execute(stmt)
    sig = sig_res.scalar_one()
    assert sig.user_id == user_id
    assert sig.payload_hash == payload_hash

    # Token reuse test: second verify must fail with 409 Conflict
    res_reuse = await client.post(
        f"/approve/{approval_id}/verify",
        json={"t": raw_token, "credential": fake_credential},
    )
    assert res_reuse.status_code == 409


@pytest.mark.asyncio
async def test_cancel_approval(client: AsyncClient, db_session: AsyncSession):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone_hash="hash_cancel" * 10,
        wa_id_encrypted="enc",
        linked=True,
    )
    approval_id = uuid.uuid4()
    raw_token = "tok-cancel"
    approval = Approval(
        id=approval_id,
        user_id=user_id,
        kind="rule",
        subject_id=uuid.uuid4(),
        payload_hash="p" * 64,
        summary={"target_rate": "130.5"},
        token_hash=hash_token(raw_token),
        nonce="nonce-c",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        state="pending",
    )
    db_session.add_all([user, approval])
    await db_session.commit()

    res = await client.post(f"/approve/{approval_id}/cancel", json={"t": raw_token})
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"

    await db_session.refresh(approval)
    assert approval.state == "rejected"
