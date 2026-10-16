"""POST /internal/verify-signature: Money asks this before EVERY auto-rule run.

We re-check the stored passkey assertion cryptographically, every time. Any problem at
all means {"valid": false}: the rule then pauses (fail closed).
"""

import hmac

from contracts.models import VerifySignatureIn, VerifySignatureOut
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import verify_authentication_response
from webauthn.helpers import bytes_to_base64url

from app.approvals import derive_challenge
from app.audit import audit
from app.auth import require_internal_key
from app.config import settings
from app.db import get_session
from app.models import Passkey, Signature, User

router = APIRouter(dependencies=[Depends(require_internal_key)])


async def _is_valid(session: AsyncSession, body: VerifySignatureIn) -> bool:
    signature = await session.get(Signature, body.signature_id)
    if signature is None or signature.user_id != body.user_id:
        return False
    if not hmac.compare_digest(signature.payload_hash, body.payload_hash):
        return False
    user = await session.get(User, body.user_id)
    if user is None or user.frozen:
        return False
    # The passkey must still exist (the user may have removed it)
    public_key = await session.scalar(
        select(Passkey.public_key).where(
            Passkey.credential_id == signature.credential_id, Passkey.user_id == body.user_id
        )
    )
    if public_key is None:
        return False

    credential_id = bytes_to_base64url(signature.credential_id)
    stored_assertion = {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {
            "authenticatorData": bytes_to_base64url(signature.authenticator_data),
            "clientDataJSON": bytes_to_base64url(signature.client_data_json),
            "signature": bytes_to_base64url(signature.signature),
        },
    }
    verify_authentication_response(  # raises if anything doesn't check out
        credential=stored_assertion,
        expected_challenge=derive_challenge(body.payload_hash, signature.nonce),
        expected_rp_id=settings.rp_id,
        expected_origin=settings.public_base_url,
        credential_public_key=public_key,
        credential_current_sign_count=0,  # an old assertion must not fail on the counter
        require_user_verification=True,
    )
    return True


@router.post("/internal/verify-signature")
async def verify_signature(
    body: VerifySignatureIn, session: AsyncSession = Depends(get_session)
) -> VerifySignatureOut:
    try:
        valid = await _is_valid(session, body)
    except Exception:
        valid = False  # fail closed
    action = "signature.verified" if valid else "signature.rejected"
    await audit(session, "money", action, str(body.signature_id), {})
    await session.commit()
    return VerifySignatureOut(valid=valid)
