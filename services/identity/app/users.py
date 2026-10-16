"""Internal user endpoints, called by Messaging (A). All need X-Internal-Key."""

import uuid
from datetime import UTC, datetime, timedelta

from contracts.models import LinkTokenOut, UserOut
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import events
from app.audit import audit
from app.auth import require_internal_key
from app.config import settings
from app.crypto import decrypt_str, encrypt_str, hash_token, new_token
from app.db import get_session
from app.models import Approval, ApprovalState, LinkToken, User
from app.redis_client import redis

router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal_key)])

LINK_TOKEN_TTL = timedelta(minutes=10)
LINK_TOKENS_PER_HOUR = 5

PhoneHash = Field(pattern=r"^[0-9a-f]{64}$")  # an HMAC-SHA256 hex, never a raw phone


class LinkTokenIn(BaseModel):
    phone_hash: str = PhoneHash
    wa_id: str = Field(pattern=r"^\d{6,20}$")  # WhatsApp id = digits only


async def _get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    return user


@router.get("/users/by-phone/{phone_hash}")
async def user_by_phone(phone_hash: str, session: AsyncSession = Depends(get_session)) -> UserOut:
    row = (
        await session.execute(
            select(User.id, User.linked, User.frozen).where(User.phone_hash == phone_hash)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "user not found")
    return UserOut(user_id=row.id, linked=row.linked, frozen=row.frozen)


@router.post("/link-tokens")
async def create_link_token(
    body: LinkTokenIn, session: AsyncSession = Depends(get_session)
) -> LinkTokenOut:
    # At most 5 links an hour per phone: stops someone spamming a student with links
    key = f"identity:rl:link:{body.phone_hash}:{int(datetime.now(UTC).timestamp() // 3600)}"
    count = await redis.incr(key)
    await redis.expire(key, 3600)
    if count > LINK_TOKENS_PER_HOUR:
        raise HTTPException(429, "too many link requests, try again later")

    token = new_token()
    expires_at = datetime.now(UTC) + LINK_TOKEN_TTL
    session.add(
        LinkToken(
            token_hash=hash_token(token),  # only the hash is stored
            phone_hash=body.phone_hash,
            wa_id_encrypted=encrypt_str(body.wa_id),
            expires_at=expires_at,
        )
    )
    await session.commit()
    return LinkTokenOut(url=f"{settings.public_base_url}/link?t={token}", expires_at=expires_at)


@router.get("/users/{user_id}/wa-target")
async def wa_target(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    user = await _get_user(session, user_id)
    return {"wa_id": decrypt_str(user.wa_id_encrypted)}


@router.post("/users/{user_id}/freeze")
async def freeze(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    """Blocks new approvals and expires every pending one. Money freezes money separately."""
    user = await _get_user(session, user_id)
    user.frozen = True
    await session.execute(
        update(Approval)
        .where(Approval.user_id == user_id, Approval.state == ApprovalState.PENDING)
        .values(state=ApprovalState.EXPIRED)
    )
    await audit(session, "messaging", "user.frozen", str(user_id), {})
    await session.commit()
    await events.publish("frozen", user_id)
    return {"frozen": True}
