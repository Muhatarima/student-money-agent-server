"""Passkeys (fingerprint / Face ID) with py_webauthn.

Registration: the link page creates a passkey, which links the WhatsApp number to a user.
Login helpers (start_auth / finish_auth): used by approvals and the vault. The caller picks
the challenge, so an approval can bind it to exactly what the user saw.
"""

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app import events
from app.audit import audit
from app.config import settings
from app.crypto import hash_token
from app.db import get_session
from app.models import LinkToken, Passkey, User
from app.redis_client import redis
from app.web import limit_verify, message_page, require_browser_json, templates

router = APIRouter()

CHALLENGE_TTL = 300  # 5 minutes to touch the sensor


class AuthFailed(Exception):
    """Passkey check failed. Callers answer 400/403 and never say why in detail."""


class TokenIn(BaseModel):
    t: str


class CredentialIn(BaseModel):
    t: str
    credential: dict


# --- Link tokens ---


async def valid_link_token(
    session: AsyncSession, token: str, for_update: bool = False
) -> LinkToken | None:
    """The link token if it exists (by hash), is unused and not expired; else None."""
    query = select(LinkToken).where(LinkToken.token_hash == hash_token(token))
    if for_update:
        query = query.with_for_update()  # two clicks at once: only one can use it
    link = await session.scalar(query)
    if link is None or link.used_at is not None or link.expires_at <= datetime.now(UTC):
        return None
    return link


@router.get("/link")
async def link_page(request: Request, t: str = "", session: AsyncSession = Depends(get_session)):
    if await valid_link_token(session, t) is None:
        return message_page(
            request,
            "Link expired",
            "This link expired or was already used. Say hi in WhatsApp to get a new one.",
            410,
        )
    return templates.TemplateResponse(
        request, "link.html", {"title": "Secure your account", "token": t}
    )


@router.get("/done")
async def done_page(request: Request):
    return message_page(request, "All set ✅", "You can go back to WhatsApp.")


# --- Registration ---


def _reg_key(token: str) -> str:
    return f"webauthn:reg:{hash_token(token)}"


@router.post("/passkeys/register/options", dependencies=[Depends(require_browser_json)])
async def register_options(body: TokenIn, session: AsyncSession = Depends(get_session)):
    link = await valid_link_token(session, body.t)
    if link is None:
        raise HTTPException(410, "This link expired or was already used.")
    # The same phone gets the same user (e.g. a second device); else a new id
    user_id = await session.scalar(select(User.id).where(User.phone_hash == link.phone_hash))
    user_id = user_id or uuid.uuid4()

    options = generate_registration_options(
        rp_id=settings.rp_id,
        rp_name=settings.rp_name,
        user_id=user_id.bytes,
        user_name="student",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    state = {"challenge": bytes_to_base64url(options.challenge), "user_id": str(user_id)}
    await redis.set(_reg_key(body.t), json.dumps(state), ex=CHALLENGE_TTL)
    return json.loads(options_to_json(options))


@router.post("/passkeys/register/verify", dependencies=[Depends(require_browser_json)])
async def register_verify(
    request: Request, body: CredentialIn, session: AsyncSession = Depends(get_session)
):
    await limit_verify(request, body.t)
    link = await valid_link_token(session, body.t, for_update=True)
    if link is None:
        raise HTTPException(410, "This link expired or was already used.")
    # GETDEL: a challenge works once only
    state = await redis.getdel(_reg_key(body.t))
    if state is None:
        raise HTTPException(400, "Please start again.")
    state = json.loads(state)

    try:
        verified = verify_registration_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(state["challenge"]),
            expected_origin=settings.public_base_url,
            expected_rp_id=settings.rp_id,
            require_user_verification=True,
        )
    except Exception:
        raise HTTPException(400, "Fingerprint check failed. Please try again.") from None

    # One transaction: user + passkey + token used + audit
    user = await session.scalar(select(User).where(User.phone_hash == link.phone_hash))
    if user is None:
        user = User(id=uuid.UUID(state["user_id"]), phone_hash=link.phone_hash)
        session.add(user)
    user.wa_id_encrypted = link.wa_id_encrypted
    user.linked = True
    transports = body.credential.get("response", {}).get("transports")
    session.add(
        Passkey(
            user=user,
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=json.dumps(transports) if transports else None,
        )
    )
    link.used_at = datetime.now(UTC)
    await session.flush()
    await audit(session, "user", "passkey.registered", str(user.id), {})
    await session.commit()
    await events.publish("account_linked", user.id)
    return {"ok": True, "redirect": "/done"}


# --- Login helpers (approvals, vault) ---


async def start_auth(session: AsyncSession, user_id: uuid.UUID, challenge: bytes) -> dict:
    """Options for the browser: only this user's passkeys, fingerprint required."""
    credential_ids = await session.scalars(
        select(Passkey.credential_id).where(Passkey.user_id == user_id)
    )
    options = generate_authentication_options(
        rp_id=settings.rp_id,
        challenge=challenge,
        allow_credentials=[PublicKeyCredentialDescriptor(id=cid) for cid in credential_ids],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return json.loads(options_to_json(options))


async def finish_auth(
    session: AsyncSession, user_id: uuid.UUID, credential: dict, expected_challenge: bytes
) -> Passkey:
    """Check a signed challenge. Raises AuthFailed. The caller commits."""
    try:
        credential_id = base64url_to_bytes(credential["rawId"])
    except (KeyError, TypeError, ValueError):
        raise AuthFailed("bad credential") from None
    # Must be a passkey of THIS user: someone else's passkey never approves for you
    passkey = await session.scalar(
        select(Passkey)
        .where(Passkey.credential_id == credential_id, Passkey.user_id == user_id)
        .with_for_update()
    )
    if passkey is None:
        raise AuthFailed("unknown passkey")
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.rp_id,
            expected_origin=settings.public_base_url,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception:
        raise AuthFailed("signature check failed") from None
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = datetime.now(UTC)
    return passkey
