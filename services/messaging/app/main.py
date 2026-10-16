import hashlib
import hmac
import json

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.dedupe import forget, is_duplicate
from app.queue import enqueue_incoming
from app.security import mask_phone

app = FastAPI(title="Student Money Agent")


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
def health():
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
    raw_body = await request.body()  # exact bytes, before any parsing
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
            print(f"🔁 Duplicate ignored: {msg['id']}")
            continue
        new_count += 1
        await enqueue_message(msg)
        print(f"📥 Queued message from {mask_phone(msg.get('from', ''))}")

    # Always answer 200 for genuine messages, even duplicates,
    # otherwise Meta keeps retrying.
    return {"received": True, "new": new_count, "duplicates": dup_count}
