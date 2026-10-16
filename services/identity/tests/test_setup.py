import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.auth import require_internal_key
from app.config import Settings, settings

protected = FastAPI()


@protected.get("/secret", dependencies=[Depends(require_internal_key)])
def secret():
    return {"ok": True}


# --- X-Internal-Key ---


def test_internal_key_missing_is_401():
    assert TestClient(protected).get("/secret").status_code == 401


def test_internal_key_wrong_is_401():
    response = TestClient(protected).get("/secret", headers={"X-Internal-Key": "wrong"})
    assert response.status_code == 401


def test_internal_key_correct_passes():
    response = TestClient(protected).get("/secret", headers={"X-Internal-Key": "test-key"})
    assert response.status_code == 200


def test_empty_configured_key_blocks_everyone(monkeypatch):
    # INTERNAL_API_KEY= (empty) in .env must not mean "no header needed" (fail closed)
    monkeypatch.setattr(settings, "internal_api_key", SecretStr(""))
    assert TestClient(protected).get("/secret").status_code == 401
    response = TestClient(protected).get("/secret", headers={"X-Internal-Key": ""})
    assert response.status_code == 401


# --- Settings ---


def test_missing_setting_error_hides_secret_values(monkeypatch):
    # Only one short secret left in the env, so pydantic can't cut it out of the message
    for name in ["ENV", "DATABASE_URL", "REDIS_URL", "MONEY_URL", "PUBLIC_BASE_URL", "RP_ID"]:
        monkeypatch.delenv(name)
    for name in ["FERNET_KEY", "VAULT_MASTER_KEY", "SESSION_SECRET"]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("INTERNAL_API_KEY", "s3cr3t")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    text = str(error.value)  # what a crash traceback prints
    assert "database_url" in text  # still names the missing field
    assert "s3cr3t" not in text


def test_env_must_be_set(monkeypatch):
    # No silent "dev" default
    monkeypatch.delenv("ENV")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert "env" in str(error.value)
