"""Real client code against the mock services (no network)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from contracts.models import Currency, RuleProposal, TransferProposal
from pydantic import SecretStr

from app.clients import IdentityClient, MoneyClient, ServiceUnavailable
from app.config import settings
from tests.mocks import mock_identity, mock_money

USER = uuid.uuid4()


@pytest.fixture
def money():
    return MoneyClient(transport=httpx.ASGITransport(app=mock_money.app))


@pytest.fixture
def identity():
    return IdentityClient(transport=httpx.ASGITransport(app=mock_identity.app))


# --- Money ---


async def test_get_rate(money):
    rate = await money.get_rate("BDT_IDR")
    assert rate.pair == "BDT_IDR" and rate.rate == Decimal("135.20")


async def test_unknown_pair_raises(money):
    with pytest.raises(httpx.HTTPStatusError):
        await money.get_rate("EUR_XYZ")


async def test_get_balance(money):
    balance = await money.get_balance(USER)
    assert balance.user_id == USER and balance.balances["BDT"] == Decimal("5000.00")


async def test_propose_transfer(money):
    proposal = TransferProposal(
        user_id=USER,
        from_currency=Currency.BDT,
        to_currency=Currency.IDR,
        amount=Decimal("10000"),
        idempotency_key="wamid.X:0",
    )
    result = await money.propose_transfer(proposal)
    assert result.status == "pending_approval"
    assert result.approval_url == "http://localhost:8003/approve/demo"


async def test_propose_rule(money):
    proposal = RuleProposal(
        user_id=USER,
        from_currency=Currency.BDT,
        to_currency=Currency.IDR,
        target_rate=Decimal("136"),
        amount_per_run=Decimal("5000"),
        max_per_month=Decimal("20000"),
        valid_until=datetime.now(UTC) + timedelta(days=30),
        idempotency_key="wamid.Y:0",
    )
    assert (await money.propose_rule(proposal)).status == "pending_approval"


async def test_list_rules_and_freeze_money(money):
    assert (await money.list_rules(USER))[0]["pair"] == "BDT_IDR"
    await money.freeze_money(USER)  # no error = ok


# --- Identity ---


async def test_known_user(identity):
    user = await identity.get_user_by_phone("a" * 64)
    assert user.linked and not user.frozen


async def test_unknown_user_is_none(identity):
    assert await identity.get_user_by_phone(mock_identity.UNKNOWN_HASH) is None


async def test_create_link_token(identity):
    token = await identity.create_link_token("a" * 64, wa_id="8801700001234")
    assert token.url.startswith("http://localhost:8003/link/")


async def test_freeze_marks_user_frozen(identity):
    phone_hash = "b" * 64
    user = await identity.get_user_by_phone(phone_hash)
    await identity.freeze(user.user_id)
    assert (await identity.get_user_by_phone(phone_hash)).frozen


async def test_wa_target_and_upload_link(identity):
    assert await identity.get_wa_target(USER) == "8801700001234"
    assert (await identity.create_upload_link(USER)).startswith("http://localhost:8003/upload/")


# --- Security + failures ---


async def test_every_call_sends_the_internal_key():
    seen = []

    def record(request):
        seen.append(request.headers.get("X-Internal-Key"))
        return httpx.Response(200, json={"wa_id": "1", "url": "u"})

    identity = IdentityClient(transport=httpx.MockTransport(record))
    await identity.get_wa_target(USER)
    await identity.create_upload_link(USER)
    assert seen == ["test-internal-key"] * 2


async def test_mock_rejects_wrong_key():
    # The mocks check the key like the real services do
    transport = httpx.ASGITransport(app=mock_money.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://m") as client:
        response = await client.get(f"/internal/balance/{USER}", headers={"X-Internal-Key": "x"})
    assert response.status_code == 401


async def test_connection_error_is_service_unavailable():
    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ServiceUnavailable):
        await MoneyClient(transport=httpx.MockTransport(down)).get_rate("BDT_IDR")


async def test_5xx_is_service_unavailable():
    broken = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(ServiceUnavailable):
        await IdentityClient(transport=broken).get_user_by_phone("a" * 64)


async def test_mock_with_empty_configured_key_blocks_everyone(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_key", SecretStr(""))
    transport = httpx.ASGITransport(app=mock_money.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://m") as client:
        response = await client.get(f"/internal/balance/{USER}", headers={"X-Internal-Key": ""})
    assert response.status_code == 401
