import hashlib
import hmac
import json
import os

import redis.asyncio as redis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

load_dotenv()  # reads .env into environment variables

VERIFY_TOKEN = os.environ["WA_VERIFY_TOKEN"]
APP_SECRET = os.environ["WA_APP_SECRET"].encode()  # hmac needs bytes

r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True, protocol=2)

QUEUE = "queue:incoming"

app = FastAPI(title="Student Money Agent")


# ---------- helpers ----------
async def is_duplicate(msg_id: str) -> bool:
    # nx=True: only set if the key doesn't exist yet (atomic check-and-save)
    # ex=86400: Redis forgets the ID after 24 hours
    was_new = await r.set(f"wa:msg:{msg_id}", "1", nx=True, ex=86400)
    return not was_new


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
        "text": msg.get("text", {}).get("body", ""),
    }
    try:
        await r.lpush(QUEUE, json.dumps(job))  # add to the queue
    except Exception:
        # If queuing fails, forget the ID so Meta's retry is NOT treated as a duplicate
        await r.delete(f"wa:msg:{msg['id']}")
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
    if mode == "subscribe" and hmac.compare_digest(token, VERIFY_TOKEN):
        return PlainTextResponse(challenge)  # echo back = "yes, this URL is mine"
    raise HTTPException(status_code=403, detail="Verification failed")


# ---- Lock 2: signature check + duplicate blocking + queue ----
@app.post("/webhook")
async def receive_webhook(request: Request):
    raw_body = await request.body()  # exact bytes, before any parsing
    received_sig = request.headers.get("X-Hub-Signature-256", "")
    expected_sig = "sha256=" + hmac.new(APP_SECRET, raw_body, hashlib.sha256).hexdigest()

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
        sender = "****" + msg.get("from", "")[-4:]  # never log full phone numbers
        print(f"📥 Queued message from {sender}")

    # Always answer 200 for genuine messages, even duplicates,
    # otherwise Meta keeps retrying.
    return {"received": True, "new": new_count, "duplicates": dup_count}