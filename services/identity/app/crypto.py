import hashlib
import secrets
from datetime import UTC, datetime

from cryptography.fernet import Fernet

from .config import settings


def _get_fernet() -> Fernet:
    return Fernet(settings.FERNET_KEY.encode("utf-8"))


def encrypt_str(plaintext: str) -> str:
    """Encrypt a plaintext string using the configured FERNET_KEY."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_str(ciphertext: str) -> str:
    """Decrypt a ciphertext string back to plaintext using FERNET_KEY."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def new_token() -> str:
    """Generate a secure, random 32-byte URL-safe token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Compute the SHA-256 hex digest of a token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware in UTC (normalizes SQLite naive datetimes)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt

