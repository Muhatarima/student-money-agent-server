import hmac

from fastapi import Header, HTTPException

from app.config import settings


def require_internal_key(x_internal_key: str = Header(default="")) -> None:
    # An empty configured key must block everyone, not let an empty header in (fail closed).
    # compare_digest = constant-time compare (stops timing attacks)
    expected = settings.internal_api_key.encode()
    if not expected or not hmac.compare_digest(x_internal_key.encode(), expected):
        raise HTTPException(status_code=401, detail="Invalid internal key")
