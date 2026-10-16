import uuid
from datetime import UTC, datetime, timedelta

from contracts.models import LinkTokenOut, UserOut
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .audit import audit
from .auth import require_internal_key
from .config import settings
from .crypto import decrypt_str, encrypt_str, hash_token, new_token
from .db import get_db
from .events import publish_event
from .models import Approval, LinkToken, User
from .redis_client import redis_client

router = APIRouter(
    prefix="/internal", tags=["internal-users"], dependencies=[Depends(require_internal_key)]
)


class LinkTokenRequest(BaseModel):
    phone_hash: str = Field(min_length=16, max_length=64)
    wa_id: str = Field(min_length=5, max_length=32)


@router.get("/users/by-phone/{phone_hash}", response_model=UserOut)
async def get_user_by_phone(
    phone_hash: str,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    stmt = select(User).where(User.phone_hash == phone_hash)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return UserOut(
        user_id=user.id,
        linked=user.linked,
        frozen=user.frozen,
    )


@router.post("/link-tokens", response_model=LinkTokenOut)
async def create_link_token(
    payload: LinkTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> LinkTokenOut:
    # Rate limit: max 5 requests per hour per phone_hash (atomic pipeline \u2014 BUG-6 fix)
    rate_key = f"rate:link-token:{payload.phone_hash}"
    try:
        async with redis_client.pipeline(transaction=True) as pipe:
            await pipe.incr(rate_key)
            await pipe.expire(rate_key, 3600)
            results = await pipe.execute()
        count = results[0]
        if count > 5:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded: max 5 link tokens per hour",
            )
    except HTTPException:
        raise
    except Exception:
        # If Redis is unavailable in dev/test, proceed gracefully.
        pass

    raw_token = new_token()
    token_h = hash_token(raw_token)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    wa_encrypted = encrypt_str(payload.wa_id)

    link_token = LinkToken(
        token_hash=token_h,
        phone_hash=payload.phone_hash,
        wa_id_encrypted=wa_encrypted,
        expires_at=expires_at,
    )
    db.add(link_token)
    await audit(
        db,
        actor="internal:messaging",
        action="link_token.created",
        target=payload.phone_hash,
        details={"expires_in_seconds": 600},
    )
    await db.commit()

    return LinkTokenOut(
        url=f"{settings.PUBLIC_BASE_URL}/link?t={raw_token}",
        expires_at=expires_at,
    )


@router.get("/users/{user_id}/wa-target")
async def get_wa_target(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    wa_id = decrypt_str(user.wa_id_encrypted)
    return {"wa_id": wa_id}


@router.post("/users/{user_id}/freeze")
async def freeze_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.frozen = True

    # Expire all pending approvals for this user
    await db.execute(
        update(Approval)
        .where(Approval.user_id == user_id, Approval.state == "pending")
        .values(state="expired")
    )

    await audit(
        db,
        actor="internal",
        action="user.frozen",
        target=user_id,
        details={},
    )
    await db.commit()

    try:
        await publish_event("frozen", user_id=user_id, data={})
    except Exception:
        pass

    return {"status": "frozen", "user_id": str(user_id)}
