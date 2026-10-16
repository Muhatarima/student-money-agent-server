import logging
import uuid

import pytest
from pydantic import ValidationError

from app import main
from app.config import Settings, settings
from app.logs import MASK, mask_secrets
from tests.conftest import ORIGIN
from tests.factories import WA_ID, phone_hash
from tests.test_approvals import create, linked_user, money, sign  # noqa: F401 (money fixture)
from tests.test_passkeys import new_link

# --- Security headers ---


async def test_pages_get_security_headers(client):
    token = await new_link(client)
    response = await client.get(f"/link?t={token}")
    headers = response.headers
    assert headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert "script-src 'self' https://unpkg.com" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" not in headers  # not on plain localhost


async def test_hsts_only_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    response = await client.get("/done")
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")


async def test_api_responses_get_headers_too(client):
    response = await client.get(f"/internal/users/by-phone/{phone_hash()}")
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_huge_request_is_refused_before_reading(client):
    headers = ORIGIN | {"content-length": str(main.MAX_BODY_BYTES + 1)}
    response = await client.post(f"/upload/{uuid.uuid4()}", content=b"x", headers=headers)
    assert response.status_code == 413


# --- Rate limits on passkey checks ---


async def test_approval_verify_is_limited_per_approval(client, session):
    user, cid = await linked_user(session)
    approval_id, token = await create(client, user)
    body = {"t": token, "credential": {}}
    codes = [
        (await client.post(f"/approve/{approval_id}/verify", json=body, headers=ORIGIN)).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [400] * 10 and codes[10] == 429


async def test_register_verify_is_limited_per_token(client):
    token = await new_link(client)
    body = {"t": token, "credential": {}}
    codes = [
        (await client.post("/passkeys/register/verify", json=body, headers=ORIGIN)).status_code
        for _ in range(11)
    ]
    assert codes[-1] == 429


async def test_limit_is_also_per_ip(client, session):
    # 10 different approvals from one IP: the 11th try is refused even on a fresh one
    user, cid = await linked_user(session)
    for _ in range(10):
        approval_id, token = await create(client, user)
        await client.post(
            f"/approve/{approval_id}/verify", json={"t": token, "credential": {}}, headers=ORIGIN
        )
    approval_id, token = await create(client, user)
    assert (await sign(client, approval_id, token, cid)).status_code == 429


# --- Never log tokens ---


def test_access_log_line_hides_tokens():
    line = '127.0.0.1 - "GET /approve/abc?t=SECRETTOKEN123 HTTP/1.1" 200'
    assert "SECRETTOKEN123" not in mask_secrets(line)
    assert "/approve/abc?t=****" in mask_secrets(line)


def test_filter_masks_log_arguments():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "%s", ("/link?t=abc",), None
    )
    MASK.filter(record)
    assert record.getMessage() == "/link?t=****"


def test_uvicorn_access_log_has_the_filter():
    assert MASK in logging.getLogger("uvicorn.access").filters


async def test_app_logs_never_contain_tokens_wa_id_or_phone_hash(client, caplog):
    caplog.set_level(logging.DEBUG)
    ph = phone_hash()
    token = await new_link(client, ph)
    await client.get(f"/link?t={token}")
    assert token not in caplog.text and WA_ID not in caplog.text and ph not in caplog.text


# --- /health ---


async def test_health_checks_db_and_redis(client):
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_health_503_when_redis_is_down(client, monkeypatch):
    async def down():
        raise ConnectionError("redis down")

    monkeypatch.setattr(main.redis, "ping", down)
    assert (await client.get("/health")).status_code == 503


# --- Production refuses unsafe settings ---


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"PUBLIC_BASE_URL": "http://identity.example"}, "https"),
        ({"PUBLIC_BASE_URL": "https://identity.example", "STORAGE_BACKEND": "local"}, "s3"),
    ],
)
def test_production_refuses_unsafe_settings(monkeypatch, env, message):
    monkeypatch.setenv("ENV", "production")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None)


def test_production_with_https_and_s3_starts(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://identity.example")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    assert Settings(_env_file=None).is_production
