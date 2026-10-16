import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from contracts.models import ApprovalConfirmed, ApprovalCreate, ApprovalCreated
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import base64url_to_bytes

from .audit import audit
from .auth import require_internal_key
from .config import settings
from .crypto import ensure_utc, hash_token, new_token
from .db import get_db
from .models import Approval, Signature, User
from .passkeys import finish_auth, start_auth

router = APIRouter(tags=["approvals"])
templates = Jinja2Templates(directory="templates")


class TokenRequest(BaseModel):
    t: str


class VerifyApprovalRequest(BaseModel):
    t: str
    credential: dict[str, Any]


def derive_approval_challenge(payload_hash: str, nonce: str) -> bytes:
    """Derive 32-byte cryptographic challenge = sha256(payload_hash + nonce)."""
    return hashlib.sha256((payload_hash + nonce).encode("utf-8")).digest()


# ── Internal Endpoint for Engineer B ─────────────────────────────────────────


@router.post(
    "/internal/approvals",
    response_model=ApprovalCreated,
    dependencies=[Depends(require_internal_key)],
)
async def create_approval(
    payload: ApprovalCreate,
    db: AsyncSession = Depends(get_db),
) -> ApprovalCreated:
    # Refuse if user is frozen or not linked
    stmt = select(User).where(User.id == payload.user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if user is None or not user.linked or user.frozen:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User not linked or account is frozen.",
        )

    now = datetime.now(UTC)
    # Ensure expires_at is timezone-aware
    target_expiry = payload.expires_at
    if target_expiry.tzinfo is None:
        target_expiry = target_expiry.replace(tzinfo=UTC)

    max_expiry = now + timedelta(minutes=10)
    actual_expires_at = min(target_expiry, max_expiry)

    raw_token = new_token()
    token_h = hash_token(raw_token)
    nonce = new_token()
    approval_id = uuid.uuid4()

    approval = Approval(
        id=approval_id,
        user_id=payload.user_id,
        kind=payload.kind,
        subject_id=payload.subject_id,
        payload_hash=payload.payload_hash,
        summary=payload.summary,
        token_hash=token_h,
        nonce=nonce,
        expires_at=actual_expires_at,
        state="pending",
    )
    db.add(approval)
    await audit(
        db,
        actor="internal:money",
        action="approval.created",
        target=approval_id,
        details={"kind": payload.kind, "subject_id": str(payload.subject_id)},
    )
    await db.commit()

    return ApprovalCreated(
        approval_id=approval_id,
        url=f"{settings.PUBLIC_BASE_URL}/approve/{approval_id}?t={raw_token}",
    )


# ── Public Endpoints for User ────────────────────────────────────────────────


@router.get("/approve/{approval_id}", response_class=HTMLResponse)
async def get_approve_page(
    request: Request,
    approval_id: uuid.UUID,
    t: str = "",
    db: AsyncSession = Depends(get_db),
):
    if not t:
        return HTMLResponse("<p>Missing token.</p>", status_code=400)

    token_h = hash_token(t)
    stmt = select(Approval).where(Approval.id == approval_id, Approval.token_hash == token_h)
    res = await db.execute(stmt)
    approval = res.scalar_one_or_none()

    now = datetime.now(UTC)
    if approval is None or approval.state != "pending" or ensure_utc(approval.expires_at) < now:
        return HTMLResponse(
            "<!DOCTYPE html><html>"
            "<body style='font-family:sans-serif;text-align:center;padding:50px;'>"
            "<h2>Request Expired or Unavailable</h2>"
            "<p>This request expired. Ask again in WhatsApp.</p>"
            "</body></html>",
            status_code=410,
        )

    # Check that user is not frozen
    user_stmt = select(User).where(User.id == approval.user_id)
    user_res = await db.execute(user_stmt)
    user = user_res.scalar_one_or_none()
    if user is None or user.frozen:
        return HTMLResponse(
            "<!DOCTYPE html><html>"
            "<body style='font-family:sans-serif;text-align:center;padding:50px;'>"
            "<h2>Account Frozen</h2>"
            "<p>This account is frozen. Unfreeze via WhatsApp before approving transfers.</p>"
            "</body></html>",
            status_code=403,
        )

    return templates.TemplateResponse(
        request=request,
        name="approve.html",
        context={
            "approval_id": approval.id,
            "token": t,
            "kind": approval.kind,
            "summary": approval.summary,
            "expires_at_iso": approval.expires_at.isoformat(),
        },
    )


@router.get("/approve/{approval_id}/approved", response_class=HTMLResponse)
async def get_approved_page(request: Request, approval_id: uuid.UUID):
    return templates.TemplateResponse(request=request, name="approved.html")


@router.post("/approve/{approval_id}/options")
async def get_approval_auth_options(
    approval_id: uuid.UUID,
    payload: TokenRequest,
    db: AsyncSession = Depends(get_db),
):
    token_h = hash_token(payload.t)
    stmt = select(Approval).where(Approval.id == approval_id, Approval.token_hash == token_h)
    res = await db.execute(stmt)
    approval = res.scalar_one_or_none()

    now = datetime.now(UTC)
    if approval is None or approval.state != "pending" or ensure_utc(approval.expires_at) < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approval expired, invalid, or already processed.",
        )

    challenge = derive_approval_challenge(approval.payload_hash, approval.nonce)
    return await start_auth(approval.user_id, challenge, db)


@router.post("/approve/{approval_id}/verify")
async def verify_and_confirm_approval(
    approval_id: uuid.UUID,
    payload: VerifyApprovalRequest,
    origin: str | None = Header(None, alias="Origin"),
    db: AsyncSession = Depends(get_db),
):
    token_h = hash_token(payload.t)

    # 1. Row lock on approval to prevent double-spending / concurrent approvals
    stmt = (
        select(Approval)
        .where(Approval.id == approval_id, Approval.token_hash == token_h)
        .with_for_update()
    )
    res = await db.execute(stmt)
    approval = res.scalar_one_or_none()

    now = datetime.now(UTC)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found.")
    if approval.state != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Approval already processed."
        )
    if ensure_utc(approval.expires_at) < now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Approval has expired.")

    # 2. Derive expected challenge and finish WebAuthn auth
    challenge = derive_approval_challenge(approval.payload_hash, approval.nonce)
    await finish_auth(approval.user_id, payload.credential, challenge, db, origin)

    # 3. Store raw signature assertion and mark approved
    cred_id_bytes = base64url_to_bytes(payload.credential["id"])
    auth_data = base64url_to_bytes(payload.credential["response"]["authenticatorData"])
    client_data = base64url_to_bytes(payload.credential["response"]["clientDataJSON"])
    sig_bytes = base64url_to_bytes(payload.credential["response"]["signature"])

    sig_id = uuid.uuid4()
    signature_record = Signature(
        id=sig_id,
        user_id=approval.user_id,
        approval_id=approval.id,
        credential_id=cred_id_bytes,
        payload_hash=approval.payload_hash,
        nonce=approval.nonce,
        authenticator_data=auth_data,
        client_data_json=client_data,
        signature=sig_bytes,
    )
    db.add(signature_record)
    approval.state = "approved"

    await audit(
        db,
        actor=str(approval.user_id),
        action="approval.approved",
        target=approval.id,
        details={"signature_id": str(sig_id)},
    )
    await db.commit()

    # 4. Notify Engineer B (Money service) via internal callback
    confirmed_payload = ApprovalConfirmed(
        approval_id=approval.id,
        subject_id=approval.subject_id,
        payload_hash=approval.payload_hash,
        signature_id=sig_id,
    )

    headers = {"X-Internal-Key": settings.INTERNAL_API_KEY}
    url = f"{settings.MONEY_URL.rstrip('/')}/internal/approvals/confirmed"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                url, json=confirmed_payload.model_dump(mode="json"), headers=headers
            )
            if resp.status_code >= 400:
                await audit(
                    db,
                    actor="system",
                    action="approval.money_callback_failed",
                    target=approval.id,
                    details={"status_code": resp.status_code, "response": resp.text},
                )
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Something went wrong, no money was moved.",
                )
        except httpx.RequestError as exc:
            await audit(
                db,
                actor="system",
                action="approval.money_callback_error",
                target=approval.id,
                details={"error": str(exc)},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Something went wrong, no money was moved.",
            ) from exc

    return {"status": "ok", "approval_id": str(approval.id)}


@router.post("/approve/{approval_id}/cancel")
async def cancel_approval(
    approval_id: uuid.UUID,
    payload: TokenRequest,
    db: AsyncSession = Depends(get_db),
):
    token_h = hash_token(payload.t)
    stmt = select(Approval).where(Approval.id == approval_id, Approval.token_hash == token_h)
    res = await db.execute(stmt)
    approval = res.scalar_one_or_none()

    if approval is None or approval.state != "pending":
        raise HTTPException(
            status_code=400, detail="Cannot cancel: not found or already processed."
        )

    approval.state = "rejected"
    await audit(
        db,
        actor=str(approval.user_id),
        action="approval.cancelled",
        target=approval.id,
        details={},
    )
    await db.commit()

    return {"status": "cancelled", "approval_id": str(approval.id)}
