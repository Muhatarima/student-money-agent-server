"""Shared helpers for the public web pages (link, approve, upload)."""

import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.crypto import hash_token
from app.redis_client import redis

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

# @simplewebauthn/browser, pinned to an exact version. The integrity hash makes the browser
# refuse the file if unpkg ever serves something different.
SIMPLEWEBAUTHN_URL = "https://unpkg.com/@simplewebauthn/browser@13.2.0/dist/bundle/index.umd.min.js"
SIMPLEWEBAUTHN_SRI = "sha384-m4IRnNHkGJRpIMeEMsDCeJl3aTxdy2m58GoPovXjVLrdz5+4m1NLhUNBqgX2HBzO"
templates.env.globals.update(
    simplewebauthn_url=SIMPLEWEBAUTHN_URL, simplewebauthn_sri=SIMPLEWEBAUTHN_SRI
)


def message_page(request: Request, title: str, text: str, status_code: int = 200):
    return templates.TemplateResponse(
        request, "message.html", {"title": title, "text": text}, status_code=status_code
    )


def require_browser_json(request: Request) -> None:
    """Every POST from our pages: JSON only, and sent from our own site (stops CSRF)."""
    if request.headers.get("origin") != settings.public_base_url:
        raise HTTPException(403, "bad origin")
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "JSON only")


VERIFY_PER_MINUTE = 10


async def limit_verify(request: Request, subject: str) -> None:
    """At most 10 passkey checks a minute per IP address and per link/approval."""
    minute = int(time.time() // 60)
    ip = request.client.host if request.client else "unknown"
    for key in (
        f"identity:rl:verify:ip:{ip}:{minute}",
        f"identity:rl:verify:subject:{hash_token(subject)}:{minute}",  # tokens only as hashes
    ):
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        if count > VERIFY_PER_MINUTE:
            raise HTTPException(429, "Too many tries. Please wait a minute.")
