from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import approvals, background, jobs, passkeys, signatures, users, vault
from app.config import settings
from app.db import engine
from app.logs import setup_logging
from app.redis_client import redis

HERE = Path(__file__).parent.parent
# Uploads are max 10 MB; anything much bigger is refused before it is even read
MAX_BODY_BYTES = 11 * 1024 * 1024

CSP = (
    "default-src 'self'; script-src 'self' https://unpkg.com; "
    "frame-ancestors 'none'; form-action 'self'"
)

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.run_jobs_in_api:
        yield
        return
    async with background.running({"identity-jobs": jobs.main}):
        yield


app = FastAPI(title="Identity", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.include_router(users.router)
app.include_router(passkeys.router)
app.include_router(approvals.router)
app.include_router(signatures.router)
app.include_router(vault.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"  # page URLs carry tokens
    response.headers["X-Frame-Options"] = "DENY"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store"  # pages show personal data
    return response


@app.get("/health")
async def health():
    # Without the database or Redis nothing here works, so report unhealthy
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await redis.ping()
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}
