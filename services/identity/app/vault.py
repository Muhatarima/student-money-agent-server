import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime, timedelta

import filetype
from cryptography.fernet import Fernet
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit import audit
from .auth import require_internal_key
from .config import settings
from .crypto import ensure_utc, hash_token, new_token
from .db import get_db
from .models import Document, LinkToken, User
from .storage import get_storage

router = APIRouter(tags=["vault"])
templates = Jinja2Templates(directory="templates")

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}


def sign_vault_session(user_id: uuid.UUID) -> str:
    """Create a tamper-evident session token valid for 10 minutes."""
    expiry = int(time.time()) + 600
    msg = f"{user_id}:{expiry}".encode()
    sig = hmac.new(settings.SESSION_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return f"{user_id}:{expiry}:{sig}"


def verify_vault_session(cookie_val: str | None) -> uuid.UUID | None:
    """Verify session cookie validity, signature, and expiration."""
    if not cookie_val:
        return None
    parts = cookie_val.split(":")
    if len(parts) != 3:
        return None
    user_str, expiry_str, sig = parts
    try:
        expiry = int(expiry_str)
        if time.time() > expiry:
            return None
        msg = f"{user_str}:{expiry}".encode()
        expected_sig = hmac.new(settings.SESSION_SECRET.encode(), msg, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        return uuid.UUID(user_str)
    except Exception:
        return None


def generate_csrf_token(session_cookie: str) -> str:
    """Generate CSRF token bound to the current vault session."""
    return hmac.new(
        settings.SESSION_SECRET.encode(), session_cookie.encode(), hashlib.sha256
    ).hexdigest()


class UploadLinkRequest(BaseModel):
    user_id: uuid.UUID


@router.post(
    "/internal/upload-links",
    dependencies=[Depends(require_internal_key)],
)
async def create_upload_link(
    payload: UploadLinkRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate a single-use upload link valid for 15 minutes."""
    stmt = select(User).where(User.id == payload.user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    raw_token = new_token()
    token_h = hash_token(raw_token)
    upload_id = uuid.uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=15)

    link_token = LinkToken(
        id=upload_id,
        token_hash=token_h,
        phone_hash=user.phone_hash,
        wa_id_encrypted=user.wa_id_encrypted,
        expires_at=expires_at,
    )
    db.add(link_token)
    await audit(
        db,
        actor="internal",
        action="upload_link.created",
        target=user.id,
        details={"upload_id": str(upload_id)},
    )
    await db.commit()

    return {"url": f"{settings.PUBLIC_BASE_URL}/upload/{upload_id}?t={raw_token}"}


@router.get("/upload/{upload_id}", response_class=HTMLResponse)
async def get_upload_page(
    request: Request,
    upload_id: uuid.UUID,
    t: str = "",
    vault_session: str | None = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Serve upload form for authenticated passkey sessions or authenticate valid tokens."""
    # Check if user already has an active, valid session cookie
    valid_user_id = verify_vault_session(vault_session)
    if valid_user_id is not None:
        csrf = generate_csrf_token(vault_session)  # type: ignore[arg-type]
        return templates.TemplateResponse(
            request=request,
            name="upload.html",
            context={"upload_id": upload_id, "csrf_token": csrf},
        )

    # If no session, authenticate using token link
    if not t:
        return HTMLResponse(
            "<p>Authentication required. Please request a new upload link.</p>", status_code=401
        )

    token_h = hash_token(t)
    stmt = select(LinkToken).where(LinkToken.id == upload_id, LinkToken.token_hash == token_h)
    res = await db.execute(stmt)
    link_token = res.scalar_one_or_none()

    now = datetime.now(UTC)
    if (
        link_token is None
        or link_token.used_at is not None
        or ensure_utc(link_token.expires_at) < now
    ):
        return HTMLResponse(
            "<p>This upload link has expired or has already been used.</p>", status_code=410
        )

    # Find user
    u_stmt = select(User).where(User.phone_hash == link_token.phone_hash)
    u_res = await db.execute(u_stmt)
    user = u_res.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    # BUG-4 fix: mark token as used so the upload link cannot be replayed
    # within its 15-minute window by anyone who intercepts the URL.
    link_token.used_at = now
    await db.commit()

    session_val = sign_vault_session(user.id)
    csrf = generate_csrf_token(session_val)

    # Set 10-minute HttpOnly signed session cookie
    response = templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={"upload_id": upload_id, "csrf_token": csrf},
    )
    is_secure = settings.ENV == "production"
    response.set_cookie(
        key="vault_session",
        value=session_val,
        max_age=600,
        httponly=True,
        secure=is_secure,
        samesite="strict",
    )
    return response


@router.post("/upload/{upload_id}")
async def upload_document(
    upload_id: uuid.UUID,
    doc_type: str = Form(...),
    csrf_token: str = Form(...),
    file: UploadFile = File(...),
    vault_session: str | None = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload and envelope-encrypt a sensitive document, auto-purging after 7 days."""
    user_id = verify_vault_session(vault_session)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Valid vault session required."
        )

    expected_csrf = generate_csrf_token(vault_session)  # type: ignore[arg-type]
    if not hmac.compare_digest(csrf_token, expected_csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")

    # 1. Read file contents and check size limit (10MB)
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="File exceeds maximum allowed size of 10 MB.",
        )

    # 2. Check MAGIC BYTES using filetype library (reject renamed binaries)
    kind = filetype.guess(content)
    if kind is None or kind.extension.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Only PDF, JPEG, and PNG files are allowed.",
        )

    # 3. Envelope Encryption
    # Generate random unique data key for this file
    data_key = Fernet.generate_key()
    ciphertext = Fernet(data_key).encrypt(content)

    # Wrap the data key using the vault master key
    master_fernet = Fernet(settings.VAULT_MASTER_KEY.encode())
    wrapped_key = master_fernet.encrypt(data_key)

    # Random storage key (UUID, never the user's filename)
    storage_key = str(uuid.uuid4())
    storage = get_storage()
    await storage.put(storage_key, ciphertext)

    now = datetime.now(UTC)
    delete_after = now + timedelta(days=7)

    doc_record = Document(
        id=uuid.uuid4(),
        user_id=user_id,
        doc_type=doc_type,
        storage_key=storage_key,
        wrapped_key=wrapped_key,
        content_type=kind.mime,
        size_bytes=len(content),
        delete_after=delete_after,
        created_at=now,
    )
    db.add(doc_record)
    await audit(
        db,
        actor=str(user_id),
        action="document.uploaded",
        target=user_id,
        details={"doc_type": doc_type, "size_bytes": len(content)},
    )
    await db.commit()

    date_str = delete_after.strftime("%Y-%m-%d")
    return HTMLResponse(
        f"<!DOCTYPE html><html>"
        f"<body style='font-family:sans-serif;text-align:center;padding:50px;'>"
        f"<h2>Document Stored Securely ✅</h2>"
        f"<p>Stored securely with envelope encryption. Auto-deletes on {date_str}.</p>"
        f"<p>You can close this window and return to WhatsApp.</p>"
        f"</body></html>"
    )
