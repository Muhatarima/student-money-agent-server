import hashlib
import hmac

from contracts.models import VerifySignatureIn, VerifySignatureOut
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import verify_authentication_response
from webauthn.helpers import bytes_to_base64url

from .audit import audit
from .auth import require_internal_key
from .config import settings
from .db import get_db
from .models import Passkey, Signature, User

router = APIRouter(tags=["signatures"])


@router.post(
    "/internal/verify-signature",
    response_model=VerifySignatureOut,
    dependencies=[Depends(require_internal_key)],
)
async def verify_signature(
    payload: VerifySignatureIn,
    db: AsyncSession = Depends(get_db),
) -> VerifySignatureOut:
    """Cryptographically re-verifies a stored passkey assertion for auto-rule executions.

    Fails closed on any inconsistency.
    """
    # 1. Load signature record
    stmt = select(Signature).where(Signature.id == payload.signature_id)
    res = await db.execute(stmt)
    sig_row = res.scalar_one_or_none()

    if sig_row is None:
        await audit(
            db,
            actor="internal:money",
            action="signature.rejected",
            target=payload.user_id,
            details={"reason": "signature_not_found", "signature_id": str(payload.signature_id)},
        )
        await db.commit()
        return VerifySignatureOut(valid=False)

    # 2. Verify ownership
    if sig_row.user_id != payload.user_id:
        await audit(
            db,
            actor="internal:money",
            action="signature.rejected",
            target=payload.user_id,
            details={"reason": "user_id_mismatch"},
        )
        await db.commit()
        return VerifySignatureOut(valid=False)

    # 3. Constant-time payload_hash comparison
    if not hmac.compare_digest(sig_row.payload_hash, payload.payload_hash):
        await audit(
            db,
            actor="internal:money",
            action="signature.rejected",
            target=payload.user_id,
            details={"reason": "payload_hash_mismatch"},
        )
        await db.commit()
        return VerifySignatureOut(valid=False)

    # 4. Check user status (must not be frozen, must be linked)
    user_stmt = select(User).where(User.id == payload.user_id)
    user_res = await db.execute(user_stmt)
    user = user_res.scalar_one_or_none()

    if user is None or not user.linked or user.frozen:
        await audit(
            db,
            actor="internal:money",
            action="signature.rejected",
            target=payload.user_id,
            details={"reason": "user_frozen_or_unlinked"},
        )
        await db.commit()
        return VerifySignatureOut(valid=False)

    # 5. Check passkey still exists
    pk_stmt = select(Passkey).where(
        Passkey.credential_id == sig_row.credential_id,
        Passkey.user_id == payload.user_id,
    )
    pk_res = await db.execute(pk_stmt)
    passkey = pk_res.scalar_one_or_none()

    if passkey is None:
        await audit(
            db,
            actor="internal:money",
            action="signature.rejected",
            target=payload.user_id,
            details={"reason": "passkey_deleted"},
        )
        await db.commit()
        return VerifySignatureOut(valid=False)

    # 6. Cryptographically re-verify assertion
    expected_challenge = hashlib.sha256(
        (sig_row.payload_hash + sig_row.nonce).encode("utf-8")
    ).digest()
    credential_payload = {
        "id": bytes_to_base64url(sig_row.credential_id),
        "rawId": bytes_to_base64url(sig_row.credential_id),
        "response": {
            "authenticatorData": bytes_to_base64url(sig_row.authenticator_data),
            "clientDataJSON": bytes_to_base64url(sig_row.client_data_json),
            "signature": bytes_to_base64url(sig_row.signature),
        },
        "type": "public-key",
    }

    try:
        verify_authentication_response(
            credential=credential_payload,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.RP_ID,
            expected_origin=settings.PUBLIC_BASE_URL,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=0,  # Re-verification must not fail on counter
            require_user_verification=True,
        )
    except Exception as exc:
        await audit(
            db,
            actor="internal:money",
            action="signature.rejected",
            target=payload.user_id,
            details={"reason": f"crypto_verification_failed: {exc}"},
        )
        await db.commit()
        return VerifySignatureOut(valid=False)

    await audit(
        db,
        actor="internal:money",
        action="signature.verified",
        target=payload.user_id,
        details={"signature_id": str(payload.signature_id)},
    )
    await db.commit()
    return VerifySignatureOut(valid=True)
