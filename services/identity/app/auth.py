import hmac

from fastapi import Header, HTTPException, status

from .config import settings


async def require_internal_key(
    x_internal_key: str | None = Header(None, alias="X-Internal-Key"),
) -> None:
    """FastAPI dependency. Raises 401 if the X-Internal-Key header is missing or wrong."""
    if not x_internal_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing internal API key",
        )
    provided = x_internal_key.encode()
    expected = settings.INTERNAL_API_KEY.encode()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing internal API key",
        )
