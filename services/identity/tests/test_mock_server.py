import uuid
from datetime import UTC, datetime

import httpx
import pytest

import mock_server

KEY = {"X-Internal-Key": "test-key"}  # INTERNAL_API_KEY from conftest


@pytest.fixture
async def mock():
    transport = httpx.ASGITransport(app=mock_server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mock") as client:
        yield client


async def test_all_contract_endpoints_answer(mock):
    user_id = uuid.uuid4()
    approval = {
        "user_id": str(user_id),
        "kind": "transfer",
        "subject_id": str(uuid.uuid4()),
        "payload_hash": "a" * 64,
        "summary": {},
        "expires_at": datetime.now(UTC).isoformat(),
    }
    signature = {"user_id": str(user_id), "signature_id": str(uuid.uuid4()), "payload_hash": "a"}
    calls = [
        ("GET", f"/internal/users/by-phone/{'a' * 64}", None, "linked"),
        ("POST", "/internal/link-tokens", {"phone_hash": "a", "wa_id": "1"}, "url"),
        ("GET", f"/internal/users/{user_id}/wa-target", None, "wa_id"),
        ("POST", f"/internal/users/{user_id}/freeze", None, "frozen"),
        ("POST", "/internal/upload-links", {"user_id": str(user_id)}, "url"),
        ("POST", "/internal/approvals", approval, "url"),
        ("POST", "/internal/verify-signature", signature, "valid"),
    ]
    for method, path, body, field in calls:
        response = await mock.request(method, path, json=body, headers=KEY)
        assert response.status_code == 200, path
        assert field in response.json()


async def test_mock_checks_the_key(mock, monkeypatch):
    assert (await mock.get(f"/internal/users/by-phone/{'a' * 64}")).status_code == 401
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    response = await mock.get(
        f"/internal/users/by-phone/{'a' * 64}", headers={"X-Internal-Key": ""}
    )
    assert response.status_code == 401
