"""Small crypto helpers. Tokens are given to the user once and stored only as a hash."""

import hashlib
import secrets

from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    return Fernet(settings.fernet_key.get_secret_value())


def encrypt_str(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_str(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 32 random bytes


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
