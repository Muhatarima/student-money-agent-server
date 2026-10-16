from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import engine, get_db
from .redis_client import redis_client

# ── Startup guard ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Enforce HTTPS in production before accepting any traffic.
    if settings.ENV == "production" and not settings.PUBLIC_BASE_URL.startswith("https://"):
        raise RuntimeError(
            "ENV=production but PUBLIC_BASE_URL is not https. "
            "Set PUBLIC_BASE_URL to your Railway HTTPS domain."
        )
    yield
    # Dispose DB connection pool on shutdown.
    await engine.dispose()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Identity Service",
    description="Users, passkeys, approvals, vault — Engineer C",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Security Headers Middleware ───────────────────────────────────────────────


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; frame-ancestors 'none';"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"

    # Disallow caching on HTML pages to prevent sensitive data from persisting in browser cache
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

    if settings.ENV == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response


# Static files and Jinja2 templates
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["ops"])
async def health(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Liveness & readiness check. Returns 200 when service, DB, and Redis are reachable."""
    # Check Database connection
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        return JSONResponse(
            {"status": "degraded", "error": f"Database unreachable: {exc}"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # Check Redis connection
    try:
        await redis_client.ping()
    except Exception as exc:
        return JSONResponse(
            {"status": "degraded", "error": f"Redis unreachable: {exc}"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return JSONResponse({"status": "ok"})


# ── Routers ───────────────────────────────────────────────────────────────────

from .approvals import router as approvals_router  # noqa: E402
from .passkeys import router as passkeys_router  # noqa: E402
from .signatures import router as signatures_router  # noqa: E402
from .users import router as users_router  # noqa: E402
from .vault import router as vault_router  # noqa: E402

app.include_router(users_router)
app.include_router(passkeys_router)
app.include_router(approvals_router)
app.include_router(signatures_router)
app.include_router(vault_router)
