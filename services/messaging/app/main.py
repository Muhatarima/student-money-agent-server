import hashlib
import hmac
import json
import logging

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import settings
from app.dedupe import forget, is_duplicate
from app.logs import setup_logging
from app.queue import enqueue_incoming
from app.redis_client import redis
from app.security import mask_phone

setup_logging()
log = logging.getLogger(__name__)

app = FastAPI(title="Student Money Agent")

MAX_BODY_BYTES = 1_000_000  # real WhatsApp webhooks are a few KB


# ---------- helpers ----------
def extract_messages(data: dict) -> list[dict]:
    # WhatsApp nests messages: entry -> changes -> value -> messages
    messages = []
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            messages.extend(change.get("value", {}).get("messages", []))
    return messages


async def enqueue_message(msg: dict) -> None:
    job = {
        "msg_id": msg["id"],
        "from": msg.get("from", ""),
        "type": msg.get("type", ""),
        "text": msg.get("text", {}).get("body", ""),
        # Tapped reply button (our menu): its id, e.g. "rates"
        "button_id": msg.get("interactive", {}).get("button_reply", {}).get("id", ""),
    }
    try:
        await enqueue_incoming(job)
    except Exception:
        # If queuing fails, forget the ID so Meta's retry is NOT treated as a duplicate
        await forget(msg["id"])
        raise  # -> 500 -> Meta retries later


# ---------- routes ----------
@app.get("/health")
async def health():
    # Without Redis we can't queue messages, so report unhealthy
    try:
        await redis.ping()
    except Exception:
        return JSONResponse({"status": "redis_unavailable"}, status_code=503)
    return {"status": "ok"}


# ---- Lock 1: Meta's one-time handshake ----
@app.get("/webhook")
def verify_webhook(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
):
    # compare_digest = constant-time compare (stops timing attacks)
    verify_token = settings.wa_verify_token.get_secret_value()
    if mode == "subscribe" and hmac.compare_digest(token, verify_token):
        return PlainTextResponse(challenge)  # echo back = "yes, this URL is mine"
    raise HTTPException(status_code=403, detail="Verification failed")


# ---- Lock 2: signature check + duplicate blocking + queue ----
@app.post("/webhook")
async def receive_webhook(request: Request):
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Body too large")
    raw_body = await request.body()  # exact bytes, before any parsing
    if len(raw_body) > MAX_BODY_BYTES:  # no content-length header (chunked upload)
        raise HTTPException(status_code=413, detail="Body too large")
    received_sig = request.headers.get("X-Hub-Signature-256", "")
    app_secret = settings.wa_app_secret.get_secret_value().encode()  # hmac needs bytes
    expected_sig = "sha256=" + hmac.new(app_secret, raw_body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(received_sig, expected_sig):
        raise HTTPException(status_code=401, detail="Bad signature")

    data = json.loads(raw_body)  # only parse AFTER it's proven genuine

    new_count, dup_count = 0, 0
    for msg in extract_messages(data):
        if await is_duplicate(msg["id"]):
            dup_count += 1
            log.info("duplicate ignored: %s", msg["id"])
            continue
        new_count += 1
        await enqueue_message(msg)
        log.info("queued message from %s", mask_phone(msg.get("from", "")))

    # Always answer 200 for genuine messages, even duplicates,
    # otherwise Meta keeps retrying.
    return {"received": True, "new": new_count, "duplicates": dup_count}
