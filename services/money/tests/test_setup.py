from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import require_internal_key
from app.main import app

protected = FastAPI()


@protected.get("/secret", dependencies=[Depends(require_internal_key)])
def secret():
    return {"ok": True}


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_internal_key_missing_is_401():
    assert TestClient(protected).get("/secret").status_code == 401


def test_internal_key_wrong_is_401():
    response = TestClient(protected).get("/secret", headers={"X-Internal-Key": "wrong"})
    assert response.status_code == 401


def test_internal_key_correct_passes():
    response = TestClient(protected).get("/secret", headers={"X-Internal-Key": "test-key"})
    assert response.status_code == 200
