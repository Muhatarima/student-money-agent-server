import hmac

from fastapi import Header, HTTPException

from app.config import settings


def require_internal_key(x_internal_key: str = Header(default="")) -> None:
    # compare_digest = constant-time compare (stops timing attacks)
    if not hmac.compare_digest(x_internal_key.encode(), settings.internal_api_key.encode()):
        raise HTTPException(status_code=401, detail="Invalid internal key")
