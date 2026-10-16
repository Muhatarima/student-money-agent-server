import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .audit import audit
from .config import settings
from .crypto import ensure_utc, hash_token
from .db import get_db
from .events import publish_event
from .models import LinkToken, Passkey, User
from .redis_client import redis_client

router = APIRouter(tags=["passkeys"])
templates = Jinja2Templates(directory="templates")


class TokenRequest(BaseModel):
    t: str


class VerifyRegistrationRequest(BaseModel):
    t: str
    credential: dict[str, Any]


def _check_origin(origin: str | None) -> None:
    """Validate that the request Origin matches PUBLIC_BASE_URL (or localhost in dev)."""
    if not origin:
        return
    clean_origin = origin.rstrip("/")
    clean_base = settings.PUBLIC_BASE_URL.rstrip("/")
    if clean_origin == clean_base:
        return
    # In dev, allow localhost variations
    if settings.ENV != "production" and (
        "localhost" in clean_origin or "127.0.0.1" in clean_origin
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Cross-origin request rejected: Origin mismatch",
    )


@router.get("/link", response_class=HTMLResponse)
async def get_link_page(
    request: Request,
    t: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Render the biometric registration page if the token is valid and unexpired."""
    if not t:
        return HTMLResponse("<p>Invalid or missing token link.</p>", status_code=400)

    token_h = hash_token(t)
    stmt = select(LinkToken).where(LinkToken.token_hash == token_h)
    result = await db.execute(stmt)
    token_record = result.scalar_one_or_none()

    now = datetime.now(UTC)
    if (
        token_record is None
        or token_record.used_at is not None
        or ensure_utc(token_record.expires_at) < now
    ):
        return HTMLResponse(
            "<!DOCTYPE html><html>"
            "<body style='font-family:sans-serif;text-align:center;padding:50px;'>"
            "<h2>Link Expired or Already Used</h2>"
            "<p>This security link has expired or has already been used. "
            "Please request a new link in WhatsApp.</p>"
            "</body></html>",
            status_code=410,
        )

    return templates.TemplateResponse(
        request=request,
        name="link.html",
        context={"token": t},
    )


@router.get("/done", response_class=HTMLResponse)
async def get_done_page(request: Request):
    """Render the success page once passkey registration completes."""
    return templates.TemplateResponse(request=request, name="done.html")


@router.post("/passkeys/register/options")
async def get_registration_options(
    payload: TokenRequest,
    origin: str | None = Header(None, alias="Origin"),
    db: AsyncSession = Depends(get_db),
):
    _check_origin(origin)

    token_h = hash_token(payload.t)
    stmt = select(LinkToken).where(LinkToken.token_hash == token_h)
    result = await db.execute(stmt)
    token_record = result.scalar_one_or_none()

    now = datetime.now(UTC)
    if (
        token_record is None
        or token_record.used_at is not None
        or ensure_utc(token_record.expires_at) < now
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, used, or expired token.",
        )

    # Check if a user with this phone_hash already exists
    user_stmt = select(User).where(User.phone_hash == token_record.phone_hash)
    user_res = await db.execute(user_stmt)
    existing_user = user_res.scalar_one_or_none()

    user_id_bytes = existing_user.id.bytes if existing_user else uuid.uuid4().bytes

    options = generate_registration_options(
        rp_id=settings.RP_ID,
        rp_name=settings.RP_NAME,
        user_id=user_id_bytes,
        user_name="student",
        user_display_name="Student Money Agent",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )

    # Store challenge in Redis with 5 min TTL (challenge is bytes)
    redis_key = f"webauthn:reg:{token_h}"
    await redis_client.set(redis_key, options.challenge.hex(), ex=300)

    return Response(content=options_to_json(options), media_type="application/json")


@router.post("/passkeys/register/verify")
async def verify_registration(
    payload: VerifyRegistrationRequest,
    origin: str | None = Header(None, alias="Origin"),
    db: AsyncSession = Depends(get_db),
):
    _check_origin(origin)

    token_h = hash_token(payload.t)
    redis_key = f"webauthn:reg:{token_h}"

    # Retrieve and delete challenge in Redis (single use challenge)
    challenge_hex = await redis_client.get(redis_key)
    if not challenge_hex:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration challenge expired or not found. Please try again.",
        )
    await redis_client.delete(redis_key)
    expected_challenge = bytes.fromhex(challenge_hex)

    # Verify WebAuthn registration response
    try:
        verification = verify_registration_response(
            credential=payload.credential,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.RP_ID,
            expected_origin=origin or settings.PUBLIC_BASE_URL,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Passkey verification failed: {exc}",
        ) from exc

    # In ONE transaction: validate token, create/update user, save passkey, mark token used
    stmt = select(LinkToken).where(LinkToken.token_hash == token_h)
    result = await db.execute(stmt)
    token_record = result.scalar_one_or_none()

    now = datetime.now(UTC)
    if (
        token_record is None
        or token_record.used_at is not None
        or ensure_utc(token_record.expires_at) < now
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Link token has already been consumed or has expired.",
        )

    # Check existing user or create
    user_stmt = select(User).where(User.phone_hash == token_record.phone_hash)
    user_res = await db.execute(user_stmt)
    user = user_res.scalar_one_or_none()

    if user is None:
        user = User(
            id=uuid.uuid4(),
            phone_hash=token_record.phone_hash,
            wa_id_encrypted=token_record.wa_id_encrypted,
            linked=True,
            frozen=False,
        )
        db.add(user)
        await db.flush()
    else:
        user.linked = True

    # Save passkey
    transports_list = payload.credential.get("response", {}).get("transports") or []
    transports_str = ",".join(transports_list) if isinstance(transports_list, list) else None

    passkey = Passkey(
        id=uuid.uuid4(),
        user_id=user.id,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=transports_str,
        device_label="Primary Biometric Passkey",
        last_used_at=now,
    )
    db.add(passkey)

    token_record.used_at = now

    await audit(
        db,
        actor=str(user.id),
        action="passkey.registered",
        target=user.id,
        details={"sign_count": verification.sign_count},
    )
    await db.commit()

    try:
        await publish_event("account_linked", user_id=user.id, data={"phone_hash": user.phone_hash})
    except Exception:
        pass

    return {"status": "ok", "user_id": str(user.id)}


# ── Generic Authentication Helpers (Used by approvals and vault) ─────────────


async def start_auth(user_id: uuid.UUID, challenge: bytes, db: AsyncSession) -> dict[str, Any]:
    """Generate WebAuthn authentication options with REQUIRED user verification."""
    stmt = select(Passkey).where(Passkey.user_id == user_id)
    result = await db.execute(stmt)
    passkeys = result.scalars().all()

    if not passkeys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No registered passkeys found for user.",
        )

    allow_creds = [PublicKeyCredentialDescriptor(id=pk.credential_id) for pk in passkeys]

    options = generate_authentication_options(
        rp_id=settings.RP_ID,
        challenge=challenge,
        allow_credentials=allow_creds,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return json.loads(options_to_json(options))


async def finish_auth(
    user_id: uuid.UUID,
    credential: dict[str, Any],
    expected_challenge: bytes,
    db: AsyncSession,
    expected_origin: str | None = None,
) -> Passkey:
    """Verify an assertion with stored public key, update sign_count, and return the passkey."""
    cred_id_raw = credential.get("id")
    if not cred_id_raw:
        raise HTTPException(
            status_code=400, detail="Missing credential id in authentication response."
        )

    cred_id_bytes = base64url_to_bytes(cred_id_raw)

    stmt = select(Passkey).where(Passkey.credential_id == cred_id_bytes, Passkey.user_id == user_id)
    result = await db.execute(stmt)
    passkey = result.scalar_one_or_none()

    if passkey is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown passkey or credential does not belong to this user.",
        )

    verification = verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=settings.RP_ID,
        expected_origin=expected_origin or settings.PUBLIC_BASE_URL,
        credential_public_key=passkey.public_key,
        credential_current_sign_count=passkey.sign_count,
        require_user_verification=True,
    )

    passkey.sign_count = verification.new_sign_count
    passkey.last_used_at = datetime.now(UTC)
    return passkey
