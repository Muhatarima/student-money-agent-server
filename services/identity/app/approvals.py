"""Approval pages: the user sees exactly what Money asks to approve and signs it with a passkey.

Why it's safe:
- The passkey challenge is sha256(payload_hash + nonce), so the fingerprint signature
  covers exactly what was shown. payload_hash is recomputed from the shown summary
  every time; if anything was changed, nothing can be approved (fail closed).
- The link is single-use, expires, and only this user's passkeys can sign it.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from contracts.hashing import payload_hash
from contracts.models import ApprovalConfirmed, ApprovalCreate, ApprovalCreated
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes

from app import money_client
from app.audit import audit
from app.auth import require_internal_key
from app.config import settings
from app.crypto import hash_token, new_token
from app.db import get_session
from app.models import Approval, ApprovalState, Signature, User
from app.passkeys import AuthFailed, CredentialIn, TokenIn, finish_auth, start_auth
from app.web import message_page, require_browser_json, templates

router = APIRouter()

MAX_TTL = timedelta(minutes=10)
EXPIRED_TEXT = "This request expired. Ask again in WhatsApp."

# What the page shows, in this order, for each kind
TRANSFER_ROWS = [
    ("amount", "You send"),
    ("amount_out", "You receive"),
    ("rate", "Rate"),
    ("fee", "Fee"),
    ("provider", "Provider"),
]
RULE_ROWS = [
    ("target_rate", "When the rate reaches"),
    ("amount_per_run", "Convert each time"),
    ("max_per_month", "Monthly maximum"),
    ("valid_until", "Valid until"),
]


def challenge_for(approval: Approval) -> bytes:
    return hashlib.sha256((approval.payload_hash + approval.nonce).encode()).digest()


def summary_is_intact(approval: Approval) -> bool:
    # What we show must hash to what Money asked for. If not, someone changed it.
    return hmac.compare_digest(payload_hash(approval.summary), approval.payload_hash)


# --- Created by Money ---


@router.post("/internal/approvals", dependencies=[Depends(require_internal_key)])
async def create_approval(
    body: ApprovalCreate, session: AsyncSession = Depends(get_session)
) -> ApprovalCreated:
    user = await session.get(User, body.user_id)
    if user is None or not user.linked or user.frozen:
        raise HTTPException(409, "user can't approve right now")
    if body.expires_at.tzinfo is None:
        raise HTTPException(422, "expires_at needs a timezone")

    token = new_token()
    approval = Approval(
        user_id=user.id,
        kind=body.kind,
        subject_id=body.subject_id,
        payload_hash=body.payload_hash,
        summary=body.summary,
        token_hash=hash_token(token),  # only the hash is stored
        nonce=secrets.token_hex(16),
        expires_at=min(body.expires_at, datetime.now(UTC) + MAX_TTL),
    )
    session.add(approval)
    await session.flush()
    await audit(session, "money", "approval.created", str(approval.id), {"kind": body.kind})
    await session.commit()
    url = f"{settings.public_base_url}/approve/{approval.id}?t={token}"
    return ApprovalCreated(approval_id=approval.id, url=url)


# --- The page and its buttons ---


async def _find(
    session: AsyncSession, approval_id: uuid.UUID, token: str, for_update: bool = False
) -> Approval | None:
    query = select(Approval).where(
        Approval.id == approval_id, Approval.token_hash == hash_token(token)
    )
    if for_update:
        query = query.with_for_update()  # two taps at once: only one can approve
    return await session.scalar(query)


async def _problem(session: AsyncSession, approval: Approval | None) -> tuple[int, str] | None:
    """Why this approval can't be used right now, or None if it can."""
    if approval is None:
        return 404, "This link is not valid."
    if approval.state != ApprovalState.PENDING:
        return 409, "This request was already used."
    if approval.expires_at <= datetime.now(UTC):
        approval.state = ApprovalState.EXPIRED
        await session.commit()
        return 410, EXPIRED_TEXT
    user = await session.get(User, approval.user_id)
    if user.frozen:
        return 403, "Your account is frozen. Nothing can be approved."
    if not summary_is_intact(approval):
        return 409, "This request can't be approved. Ask again in WhatsApp."
    return None


async def _usable(session: AsyncSession, approval_id, token, for_update=False) -> Approval:
    approval = await _find(session, approval_id, token, for_update)
    problem = await _problem(session, approval)
    if problem:
        raise HTTPException(*problem)
    return approval


def _rows(approval: Approval) -> list[tuple[str, str]]:
    s = approval.summary
    pair = f"{s.get('from_currency')} → {s.get('to_currency')}"
    rows = [("Conversion", pair)]
    for key, label in RULE_ROWS if approval.kind == "rule" else TRANSFER_ROWS:
        rows.append((label, str(s.get(key, "—"))))
    if approval.kind != "rule":
        rows.append(("Recipient", s.get("recipient", "Your own wallet")))
    return rows


@router.get("/approve/{approval_id}")
async def approve_page(
    request: Request, approval_id: uuid.UUID, t: str = "", session=Depends(get_session)
):
    approval = await _find(session, approval_id, t)
    problem = await _problem(session, approval)
    if problem:
        status, text = problem
        return message_page(request, "Can't approve", text, status)
    context = {
        "title": "Review and approve",
        "approval": approval,
        "rows": _rows(approval),
        "token": t,
        "expires_at": approval.expires_at.isoformat(),
    }
    return templates.TemplateResponse(request, "approve.html", context)


@router.post("/approve/{approval_id}/options", dependencies=[Depends(require_browser_json)])
async def approve_options(approval_id: uuid.UUID, body: TokenIn, session=Depends(get_session)):
    approval = await _usable(session, approval_id, body.t)
    return await start_auth(session, approval.user_id, challenge_for(approval))


@router.post("/approve/{approval_id}/verify", dependencies=[Depends(require_browser_json)])
async def approve_verify(approval_id: uuid.UUID, body: CredentialIn, session=Depends(get_session)):
    approval = await _usable(session, approval_id, body.t, for_update=True)
    try:
        passkey = await finish_auth(
            session, approval.user_id, body.credential, challenge_for(approval)
        )
        response = body.credential["response"]
        signature = Signature(
            user_id=approval.user_id,
            approval_id=approval.id,
            credential_id=passkey.credential_id,
            payload_hash=approval.payload_hash,
            nonce=approval.nonce,
            authenticator_data=base64url_to_bytes(response["authenticatorData"]),
            client_data_json=base64url_to_bytes(response["clientDataJSON"]),
            signature=base64url_to_bytes(response["signature"]),
        )
    except (AuthFailed, KeyError, TypeError, ValueError):
        raise HTTPException(400, "Fingerprint check failed. Please try again.") from None

    # One transaction: approved + signature + audit
    approval.state = ApprovalState.APPROVED
    session.add(signature)
    await session.flush()
    await audit(session, "user", "approval.approved", str(approval.id), {"kind": approval.kind})
    await session.commit()

    confirmed = ApprovalConfirmed(
        approval_id=approval.id,
        subject_id=approval.subject_id,
        payload_hash=approval.payload_hash,
        signature_id=signature.id,
    )
    if not await money_client.confirm_approval(confirmed):
        await audit(session, "system", "approval.money_refused", str(approval.id), {})
        await session.commit()
        return {"ok": False, "redirect": "/approve-failed"}
    return {"ok": True, "redirect": "/approved"}


@router.post("/approve/{approval_id}/cancel", dependencies=[Depends(require_browser_json)])
async def approve_cancel(approval_id: uuid.UUID, body: TokenIn, session=Depends(get_session)):
    approval = await _find(session, approval_id, body.t, for_update=True)
    if approval is None or approval.state != ApprovalState.PENDING:
        raise HTTPException(409, "This request was already used.")
    approval.state = ApprovalState.REJECTED
    await audit(session, "user", "approval.rejected", str(approval.id), {"kind": approval.kind})
    await session.commit()
    return {"ok": True, "redirect": "/cancelled"}


@router.get("/approved")
async def approved_page(request: Request):
    return message_page(request, "Approved ✅", "You'll get a receipt in WhatsApp.")


@router.get("/approve-failed")
async def approve_failed_page(request: Request):
    return message_page(
        request, "Not completed", "Something went wrong, no money was moved. Ask again in WhatsApp."
    )


@router.get("/cancelled")
async def cancelled_page(request: Request):
    return message_page(request, "Cancelled", "Nothing was approved. You can close this page.")
