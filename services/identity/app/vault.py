"""Document vault: passkey-gated upload, envelope encryption, auto-delete.

Envelope encryption: every document gets its OWN key (data key). The file is encrypted
with it, and the data key itself is encrypted ("wrapped") with VAULT_MASTER_KEY.
Deleting the wrapped key makes any leftover copy of the file unreadable.
Nothing in the MVP ever returns a document's contents.
"""

import asyncio
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import filetype
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth import require_internal_key
from app.config import settings
from app.crypto import hash_token, new_token
from app.db import get_session
from app.models import Document, UploadLink, User
from app.passkeys import AuthFailed, CredentialIn, TokenIn, finish_auth, start_auth
from app.redis_client import redis
from app.storage import storage
from app.web import limit_verify, message_page, require_browser_json, templates

router = APIRouter()

LINK_TTL = timedelta(minutes=15)
SESSION_SECONDS = 600  # 10 minutes after the fingerprint check
CHALLENGE_TTL = 300
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
KEEP_FOR = timedelta(days=7)
DOC_TYPES = {"passport", "visa", "admission_letter", "other"}
# Checked by the file's first bytes (magic bytes), never by its name
ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png"}
COOKIE = "upload_session"

_sessions = URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt="upload")


class UploadLinkIn(BaseModel):
    user_id: uuid.UUID


# --- Created by Messaging ---


@router.post("/internal/upload-links", dependencies=[Depends(require_internal_key)])
async def create_upload_link(body: UploadLinkIn, session: AsyncSession = Depends(get_session)):
    user = await session.get(User, body.user_id)
    if user is None or not user.linked or user.frozen:
        raise HTTPException(409, "user can't upload right now")
    token = new_token()
    link = UploadLink(
        user_id=user.id, token_hash=hash_token(token), expires_at=datetime.now(UTC) + LINK_TTL
    )
    session.add(link)
    await session.flush()
    await audit(session, "messaging", "upload_link.created", str(user.id), {})
    await session.commit()
    return {"url": f"{settings.public_base_url}/upload/{link.id}?t={token}"}


# --- Helpers ---


async def _valid_link(session: AsyncSession, link_id: uuid.UUID, token: str, for_update=False):
    """The upload link if the token matches, it's unused, unexpired and the user isn't frozen."""
    query = select(UploadLink).where(
        UploadLink.id == link_id, UploadLink.token_hash == hash_token(token)
    )
    if for_update:
        query = query.with_for_update()
    link = await session.scalar(query)
    if link is None or link.used_at is not None or link.expires_at <= datetime.now(UTC):
        return None
    user = await session.get(User, link.user_id)
    return None if user.frozen else link


async def _link_or_410(session, link_id, token, for_update=False) -> UploadLink:
    link = await _valid_link(session, link_id, token, for_update)
    if link is None:
        raise HTTPException(410, "This link expired or was already used.")
    return link


def _read_session(request: Request, link_id: uuid.UUID) -> dict | None:
    """The signed upload session from the cookie, if it is for this link and still fresh."""
    try:
        data = _sessions.loads(request.cookies.get(COOKIE, ""), max_age=SESSION_SECONDS)
    except BadSignature:  # also covers "expired"
        return None
    return data if data.get("link_id") == str(link_id) else None


def _challenge_key(link_id: uuid.UUID) -> str:
    return f"webauthn:upload:{link_id}"


# --- Page, passkey login, upload ---


@router.get("/upload/{link_id}")
async def upload_page(
    request: Request, link_id: uuid.UUID, t: str = "", session=Depends(get_session)
):
    if await _valid_link(session, link_id, t) is None:
        return message_page(request, "Link expired", "Ask again in WhatsApp (send UPLOAD).", 410)
    upload_session = _read_session(request, link_id)
    context = {
        "title": "Upload a document",
        "link_id": link_id,
        "token": t,
        "logged_in": upload_session is not None,
        "csrf": upload_session["csrf"] if upload_session else "",
        "doc_types": sorted(DOC_TYPES),
    }
    return templates.TemplateResponse(request, "upload.html", context)


@router.post("/upload/{link_id}/options", dependencies=[Depends(require_browser_json)])
async def upload_options(link_id: uuid.UUID, body: TokenIn, session=Depends(get_session)):
    link = await _link_or_410(session, link_id, body.t)
    challenge = secrets.token_bytes(32)
    await redis.set(_challenge_key(link_id), challenge.hex(), ex=CHALLENGE_TTL)
    return await start_auth(session, link.user_id, challenge)


@router.post("/upload/{link_id}/verify", dependencies=[Depends(require_browser_json)])
async def upload_verify(
    request: Request, link_id: uuid.UUID, body: CredentialIn, session=Depends(get_session)
):
    await limit_verify(request, str(link_id))
    link = await _link_or_410(session, link_id, body.t)
    challenge = await redis.getdel(_challenge_key(link_id))  # works once only
    if challenge is None:
        raise HTTPException(400, "Please start again.")
    try:
        await finish_auth(session, link.user_id, body.credential, bytes.fromhex(challenge))
    except AuthFailed:
        raise HTTPException(400, "Fingerprint check failed. Please try again.") from None
    await session.commit()

    cookie = _sessions.dumps(
        {"link_id": str(link_id), "user_id": str(link.user_id), "csrf": secrets.token_urlsafe(32)}
    )
    response = JSONResponse({"ok": True, "redirect": f"/upload/{link_id}?t={body.t}"})
    response.set_cookie(
        COOKIE,
        cookie,
        max_age=SESSION_SECONDS,
        path="/upload",
        httponly=True,
        secure=settings.public_base_url.startswith("https://"),  # not on plain localhost
        samesite="strict",
    )
    return response


@router.post("/upload/{link_id}")
async def upload(
    request: Request,
    link_id: uuid.UUID,
    t: str = Form(...),
    csrf: str = Form(...),
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    if request.headers.get("origin") != settings.public_base_url:
        raise HTTPException(403, "bad origin")
    upload_session = _read_session(request, link_id)
    if upload_session is None:
        raise HTTPException(401, "Please confirm with your fingerprint first.")
    if not hmac.compare_digest(csrf, upload_session["csrf"]):
        raise HTTPException(403, "bad CSRF token")
    link = await _link_or_410(session, link_id, t, for_update=True)
    if doc_type not in DOC_TYPES:
        raise HTTPException(422, "unknown document type")

    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "The file is bigger than 10 MB.")
    kind = filetype.guess(data)
    if kind is None or kind.mime not in ALLOWED_TYPES:
        raise HTTPException(415, "Only PDF, JPEG or PNG files.")

    data_key = Fernet.generate_key()  # this document's own key
    ciphertext = Fernet(data_key).encrypt(data)
    wrapped_key = Fernet(settings.vault_master_key.get_secret_value()).encrypt(data_key)
    storage_key = uuid.uuid4().hex  # never the file name
    await asyncio.to_thread(storage.put, storage_key, ciphertext)

    delete_after = datetime.now(UTC) + KEEP_FOR
    session.add(
        Document(
            user_id=link.user_id,
            doc_type=doc_type,
            storage_key=storage_key,
            wrapped_key=wrapped_key,
            content_type=kind.mime,
            size_bytes=len(data),
            delete_after=delete_after,
        )
    )
    link.used_at = datetime.now(UTC)  # single-use
    await audit(session, "user", "document.uploaded", str(link.user_id), {"doc_type": doc_type})
    await session.commit()
    return message_page(
        request,
        "Stored securely 🔒",
        f"Your document is encrypted. It auto-deletes on {delete_after:%d %B %Y}.",
    )
