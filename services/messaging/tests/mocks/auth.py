import hmac

from fastapi import Header, HTTPException

from app.config import settings


def require_internal_key(x_internal_key: str = Header(default="")) -> None:
    # Same rule as the real services: missing or wrong key -> 401
    expected = settings.internal_api_key.get_secret_value()
    # An empty configured key blocks everyone (fail closed)
    if not expected or not hmac.compare_digest(x_internal_key.encode(), expected.encode()):
        raise HTTPException(401, "Invalid internal key")
