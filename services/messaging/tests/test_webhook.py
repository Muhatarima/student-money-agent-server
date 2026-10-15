import hashlib
import hmac
import json

import httpx
import pytest

from app import main

SECRET = b"test-app-secret"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


def message_payload(msg_id="wamid.TEST1", text="convert 10000 BDT to IDR") -> bytes:
    msg = {"id": msg_id, "from": "8801700001234", "type": "text", "text": {"body": text}}
    payload = {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}
    return json.dumps(payload).encode()


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def post_signed(client, body: bytes, signature: str | None = None):
    headers = {"X-Hub-Signature-256": signature or sign(body)}
    return await client.post("/webhook", content=body, headers=headers)


# --- Lock 1: handshake ---


def handshake_params(token="test-verify-token", mode="subscribe"):
    return {"hub.mode": mode, "hub.verify_token": token, "hub.challenge": "12345"}


async def test_handshake_ok_echoes_challenge(client):
    response = await client.get("/webhook", params=handshake_params())
    assert response.status_code == 200
    assert response.text == "12345"


async def test_handshake_wrong_token_is_403(client):
    response = await client.get("/webhook", params=handshake_params(token="wrong"))
    assert response.status_code == 403


async def test_handshake_wrong_mode_is_403(client):
    response = await client.get("/webhook", params=handshake_params(mode="unsubscribe"))
    assert response.status_code == 403


# --- Lock 2: signature ---


async def test_valid_signature_is_200(client):
    response = await post_signed(client, message_payload())
    assert response.status_code == 200
    assert response.json() == {"received": True, "new": 1, "duplicates": 0}


async def test_fake_signature_is_401(client, redis):
    response = await post_signed(client, message_payload(), signature="sha256=fake")
    assert response.status_code == 401
    assert await redis.llen("queue:incoming") == 0  # nothing got through


async def test_missing_signature_is_401(client):
    response = await client.post("/webhook", content=message_payload())
    assert response.status_code == 401


async def test_tampered_body_is_401(client):
    body = message_payload(text="send 10 taka")
    tampered = body.replace(b"send 10 taka", b"send 99 taka")
    response = await post_signed(client, tampered, signature=sign(body))
    assert response.status_code == 401


# --- Dedupe + queue ---


async def test_same_message_three_times_is_queued_once(client, redis):
    body = message_payload(msg_id="wamid.RETRY")
    results = [(await post_signed(client, body)).json() for _ in range(3)]

    assert [(r["new"], r["duplicates"]) for r in results] == [(1, 0), (0, 1), (0, 1)]
    assert await redis.llen("queue:incoming") == 1


async def test_queued_job_has_the_message(client, redis):
    await post_signed(client, message_payload(msg_id="wamid.JOB", text="hello"))
    job = json.loads(await redis.rpop("queue:incoming"))
    assert job == {"msg_id": "wamid.JOB", "from": "8801700001234", "text": "hello"}


async def test_enqueue_failure_forgets_dedupe_key(client, redis, monkeypatch):
    async def broken_lpush(*args):
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis, "lpush", broken_lpush)
    with pytest.raises(ConnectionError):
        await post_signed(client, message_payload(msg_id="wamid.FAIL"))

    # Key forgotten, so Meta's retry is treated as NEW, not a duplicate
    assert await redis.exists("wa:msg:wamid.FAIL") == 0
    monkeypatch.undo()
    response = await post_signed(client, message_payload(msg_id="wamid.FAIL"))
    assert response.json()["new"] == 1
