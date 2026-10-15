"""Run the real identity client against the mock identity app (no network)."""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from contracts.models import ApprovalCreate, VerifySignatureIn

from app import identity_client
from tests.mocks import mock_identity


@pytest.fixture(autouse=True)
def mock_transport(monkeypatch):
    monkeypatch.setattr(identity_client, "transport", httpx.ASGITransport(app=mock_identity.app))


def approval() -> ApprovalCreate:
    return ApprovalCreate(
        user_id=uuid.uuid4(),
        kind="transfer",
        subject_id=uuid.uuid4(),
        payload_hash="a" * 64,
        summary={"amount": "1000"},
        expires_at=datetime.now(UTC),
    )


def signature() -> VerifySignatureIn:
    return VerifySignatureIn(user_id=uuid.uuid4(), signature_id=uuid.uuid4(), payload_hash="a" * 64)


async def test_create_approval_works():
    created = await identity_client.create_approval(approval())
    assert created.url.endswith(str(created.approval_id))


async def test_verify_signature_works():
    assert await identity_client.verify_signature(signature()) is True


async def test_rejected_call_fails(monkeypatch):
    unauthorized = httpx.MockTransport(lambda request: httpx.Response(401))
    monkeypatch.setattr(identity_client, "transport", unauthorized)
    with pytest.raises(httpx.HTTPStatusError):
        await identity_client.create_approval(approval())
    # Fail closed: can't verify -> not valid
    assert await identity_client.verify_signature(signature()) is False


async def test_identity_down_means_not_valid(monkeypatch):
    def down(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(identity_client, "transport", httpx.MockTransport(down))
    assert await identity_client.verify_signature(signature()) is False


async def test_identity_saying_invalid_means_not_valid(monkeypatch):
    answer = httpx.MockTransport(lambda request: httpx.Response(200, json={"valid": False}))
    monkeypatch.setattr(identity_client, "transport", answer)
    assert await identity_client.verify_signature(signature()) is False
