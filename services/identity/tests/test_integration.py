import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from contracts.hashing import payload_hash
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import bytes_to_base64url

from app.audit import verify_chain
from app.config import settings
from app.jobs import purge_expired_records
from app.models import Document, Passkey, Signature


@pytest.mark.asyncio
async def test_full_lifecycle_integration_flow(
    client: AsyncClient, db_session: AsyncSession
):
    """
    End-to-End Integration Test for Engineer C (Identity, Passkeys, Approvals, Vault, Jobs & Audit):
    1. A requests link token for student phone
    2. Student opens link and registers passkey
    3. User status confirmed as linked
    4. B creates transaction approval with payload_hash
    5. Student approves transaction; signature bound & B notified
    6. B verifies mandate signature via /internal/verify-signature
    7. User uploads passport to vault with envelope encryption
    8. Freeze stops all future signatures
    9. Purge worker cleans expired docs and zeros encryption keys
    10. Audit chain verifies 100% untampered
    """
    headers = {"X-Internal-Key": settings.INTERNAL_API_KEY}
    student_phone = "+8801700000001"
    phone_hash = hashlib.sha256(student_phone.encode()).hexdigest()
    wa_id = "8801700000001"

    # Step 1: Messaging requests link token
    link_resp = await client.post(
        "/internal/link-tokens",
        json={"phone_hash": phone_hash, "wa_id": wa_id},
        headers=headers,
    )
    assert link_resp.status_code == 200
    token_url = link_resp.json()["url"]
    token = token_url.split("t=")[1]

    # Step 2: Student opens link page
    page_resp = await client.get(f"/link?t={token}")
    assert page_resp.status_code == 200
    assert "Secure Your Account" in page_resp.text

    # Step 2b: Register passkey
    options_resp = await client.post(
        "/passkeys/register/options",
        json={"t": token},
    )
    assert options_resp.status_code == 200
    options = options_resp.json()
    assert "challenge" in options

    raw_cred_id = b"test_integration_cred_id_1"
    raw_public_key = b"test_integration_pub_key_1"

    with patch("app.passkeys.verify_registration_response") as mock_verify:
        mock_verify.return_value.credential_id = raw_cred_id
        mock_verify.return_value.credential_public_key = raw_public_key
        mock_verify.return_value.sign_count = 0

        verify_reg_resp = await client.post(
            "/passkeys/register/verify",
            json={
                "t": token,
                "credential": {
                    "id": bytes_to_base64url(raw_cred_id),
                    "rawId": bytes_to_base64url(raw_cred_id),
                    "response": {
                        "clientDataJSON": bytes_to_base64url(b'{"type":"webauthn.create"}'),
                        "attestationObject": bytes_to_base64url(b"dummy_attestation"),
                    },
                    "type": "public-key",
                },
            },
        )
        assert verify_reg_resp.status_code == 200
        assert verify_reg_resp.json()["status"] == "ok"

    # Step 3: Check user via internal endpoint
    user_resp = await client.get(
        f"/internal/users/by-phone/{phone_hash}", headers=headers
    )
    assert user_resp.status_code == 200
    user_data = user_resp.json()
    assert user_data["linked"] is True
    assert user_data["frozen"] is False
    user_id = user_data["user_id"]

    # Verify wa-target retrieval
    wa_resp = await client.get(f"/internal/users/{user_id}/wa-target", headers=headers)
    assert wa_resp.status_code == 200
    assert wa_resp.json()["wa_id"] == wa_id

    # Step 4: Money service requests approval creation
    tx_summary = {
        "amount": "1000",
        "from_currency": "BDT",
        "to_currency": "IDR",
        "recipient": "University Tuition",
    }
    tx_payload_hash = payload_hash(tx_summary)

    appr_resp = await client.post(
        "/internal/approvals",
        json={
            "user_id": user_id,
            "kind": "transfer",
            "subject_id": str(uuid.uuid4()),
            "payload_hash": tx_payload_hash,
            "summary": tx_summary,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        },
        headers=headers,
    )
    assert appr_resp.status_code == 200
    appr_data = appr_resp.json()
    approval_id = appr_data["approval_id"]
    approval_url = appr_data["url"]
    appr_token = approval_url.split("t=")[1]

    # Step 5: Student visits approval page
    appr_page_resp = await client.get(f"/approve/{approval_id}?t={appr_token}")
    assert appr_page_resp.status_code == 200
    assert "Authorize Approval" in appr_page_resp.text
    assert "1000" in appr_page_resp.text

    # Student requests WebAuthn auth options
    appr_opts_resp = await client.post(
        f"/approve/{approval_id}/options",
        json={"t": appr_token},
    )
    assert appr_opts_resp.status_code == 200
    appr_opts = appr_opts_resp.json()
    assert "challenge" in appr_opts

    # Retrieve registered passkey from DB
    pk_stmt = select(Passkey).where(Passkey.credential_id == raw_cred_id)
    pk_res = await db_session.execute(pk_stmt)
    passkey = pk_res.scalar_one()

    # Student biometric sign & Money confirmation callback
    mock_money_resp = MagicMock()
    mock_money_resp.status_code = 200

    mock_http_client = MagicMock()
    mock_http_client.__aenter__.return_value = mock_http_client
    mock_http_client.__aexit__.return_value = None
    mock_http_client.post = AsyncMock(return_value=mock_money_resp)

    fake_credential = {
        "id": bytes_to_base64url(raw_cred_id),
        "response": {
            "authenticatorData": bytes_to_base64url(b"auth_data_demo"),
            "clientDataJSON": bytes_to_base64url(b'{"type":"webauthn.get"}'),
            "signature": bytes_to_base64url(b"sig_bytes_demo"),
        },
    }

    with (
        patch("app.approvals.finish_auth", return_value=passkey),
        patch("app.approvals.httpx.AsyncClient", return_value=mock_http_client),
    ):
        verify_appr_resp = await client.post(
            f"/approve/{approval_id}/verify",
            json={"t": appr_token, "credential": fake_credential},
        )
        assert verify_appr_resp.status_code == 200
        assert verify_appr_resp.json()["status"] == "ok"
        mock_http_client.post.assert_called_once()

    # Query recorded signature
    sig_stmt = select(Signature).where(Signature.approval_id == uuid.UUID(approval_id))
    sig_res = await db_session.execute(sig_stmt)
    sig = sig_res.scalar_one()
    signature_id = str(sig.id)

    # Step 6: Money verifies signature via /internal/verify-signature
    with patch("app.signatures.verify_authentication_response"):
        ver_sig_resp = await client.post(
            "/internal/verify-signature",
            json={
                "user_id": user_id,
                "signature_id": signature_id,
                "payload_hash": tx_payload_hash,
            },
            headers=headers,
        )
        assert ver_sig_resp.status_code == 200
        assert ver_sig_resp.json()["valid"] is True

    # Step 7: Document Vault upload flow
    upload_link_resp = await client.post(
        "/internal/upload-links",
        json={"user_id": user_id},
        headers=headers,
    )
    assert upload_link_resp.status_code == 200
    upload_url = upload_link_resp.json()["url"]
    upload_token = upload_url.split("t=")[1]
    upload_id = upload_url.split("/upload/")[1].split("?")[0]

    # Open upload page & establish session
    up_page = await client.get(f"/upload/{upload_id}?t={upload_token}")
    assert up_page.status_code == 200
    assert "Document Vault" in up_page.text

    csrf_token = up_page.text.split('name="csrf_token" value="')[1].split('"')[0]

    # Upload valid PDF
    pdf_bytes = b"%PDF-1.4 sample passport content for testing student money agent"
    upload_file_resp = await client.post(
        f"/upload/{upload_id}",
        data={"doc_type": "passport", "csrf_token": csrf_token},
        files={"file": ("passport.pdf", pdf_bytes, "application/pdf")},
    )
    assert upload_file_resp.status_code == 200
    assert "Stored Securely" in upload_file_resp.text

    # Step 8: User freezes account
    freeze_resp = await client.post(
        f"/internal/users/{user_id}/freeze", headers=headers
    )
    assert freeze_resp.status_code == 200
    assert freeze_resp.json()["status"] == "frozen"

    # Verify signature fails closed when user is frozen
    ver_frozen_resp = await client.post(
        "/internal/verify-signature",
        json={
            "user_id": user_id,
            "signature_id": signature_id,
            "payload_hash": tx_payload_hash,
        },
        headers=headers,
    )
    assert ver_frozen_resp.status_code == 200
    assert ver_frozen_resp.json()["valid"] is False

    # Step 9: Jobs purge simulation
    # Artificially expire the document in database
    result = await db_session.execute(
        select(Document).where(Document.user_id == uuid.UUID(user_id))
    )
    doc = result.scalar_one()
    doc.delete_after = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()

    # Run purge worker
    await purge_expired_records(db_session)

    # Verify document key was zeroed and marked deleted
    await db_session.refresh(doc)
    assert doc.wrapped_key is None
    assert doc.deleted_at is not None

    # Step 10: Verify the complete audit chain
    chain_valid = await verify_chain(db_session)
    assert chain_valid is True, "Audit log chain must be 100% valid and untampered"
