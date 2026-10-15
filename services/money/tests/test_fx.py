from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.fx import RateStale, calc_fee, create_quote, fetch_rates, get_rate, parse_rate

D = Decimal


@pytest.fixture
def redis():
    return FakeAsyncRedis(decode_responses=True)


async def set_rate(redis, pair, rate, age_seconds=0):
    fetched_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    await redis.hset(f"rates:{pair}", mapping={"rate": rate, "fetched_at": fetched_at.isoformat()})


# --- Rates ---


async def test_fetch_uses_mock_defaults(redis):
    await fetch_rates(redis)
    assert (await get_rate(redis, "BDT_IDR")).rate == D("135.20")


async def test_mock_rate_can_be_changed_for_demos(redis):
    await redis.set("rates:mock:BDT_IDR", "136.5")
    await fetch_rates(redis)
    assert (await get_rate(redis, "BDT_IDR")).rate == D("136.5")


async def test_bad_mock_rate_is_not_stored(redis):
    await redis.set("rates:mock:BDT_IDR", "-1")
    await fetch_rates(redis)
    with pytest.raises(RateStale):
        await get_rate(redis, "BDT_IDR")


@pytest.mark.parametrize("bad", [None, "", "abc", "0", "-5", "NaN", "Infinity", [1]])
def test_parse_rate_rejects_bad_values(bad):
    assert parse_rate(bad) is None


def test_parse_rate_accepts_numbers_without_float_errors():
    assert parse_rate(135.2) == D("135.2")
    assert parse_rate("0.0082") == D("0.0082")


async def test_missing_rate_is_stale(redis):
    with pytest.raises(RateStale):
        await get_rate(redis, "BDT_IDR")


async def test_old_rate_is_stale(redis):
    await set_rate(redis, "BDT_IDR", "135", age_seconds=settings.rate_stale_seconds + 1)
    with pytest.raises(RateStale):
        await get_rate(redis, "BDT_IDR")


async def test_rate_just_inside_limit_is_fresh(redis):
    await set_rate(redis, "BDT_IDR", "135", age_seconds=settings.rate_stale_seconds - 5)
    assert (await get_rate(redis, "BDT_IDR")).rate == D("135")


# --- Fees ---


def test_fee_minimum_applies():
    # 0.5% of 1000 = 5, below the 20 BDT minimum
    assert calc_fee(D("1000"), "BDT") == D("20")


def test_fee_percent_above_minimum():
    # 0.5% of 10000 = 50
    assert calc_fee(D("10000"), "BDT") == D("50.00")


def test_fee_rounds_up_to_cents():
    # 0.5% of 10000.01 = 50.00005 -> 50.01
    assert calc_fee(D("10000.01"), "BDT") == D("50.01")


def test_bdt_minimum_does_not_apply_to_other_currencies():
    assert calc_fee(D("100"), "USD") == D("0.50")


# --- Quotes ---


async def test_quote_rounds_amount_out_down(session, redis):
    await set_rate(redis, "BDT_IDR", "135.2")
    quote = await create_quote(session, redis, "BDT_IDR", D("1000.37"))
    # (1000.37 - 20) * 135.2 = 132546.024 -> 132546.02 (never in the user's favour by accident)
    assert quote.amount_out == D("132546.02")
    assert quote.fee == D("20")
    assert quote.rate == D("135.2")


async def test_quote_expires_after_ttl(session, redis):
    await set_rate(redis, "BDT_IDR", "135")
    quote = await create_quote(session, redis, "BDT_IDR", D("1000"))
    expected = datetime.now(UTC) + timedelta(seconds=settings.quote_ttl_seconds)
    assert abs((quote.expires_at - expected).total_seconds()) < 2


async def test_quote_with_stale_rate_is_refused(session, redis):
    with pytest.raises(RateStale):
        await create_quote(session, redis, "BDT_IDR", D("1000"))


async def test_quote_smaller_than_fee_is_refused(session, redis):
    await set_rate(redis, "BDT_IDR", "135")
    with pytest.raises(ValueError, match="fee"):
        await create_quote(session, redis, "BDT_IDR", D("20"))


# --- Route ---


@pytest.fixture
def client(redis, monkeypatch):
    monkeypatch.setattr(main, "redis", redis)
    return TestClient(main.app, headers={"X-Internal-Key": "test-key"})


def test_rate_route_needs_internal_key(client):
    assert client.get("/internal/rates/BDT_IDR", headers={"X-Internal-Key": ""}).status_code == 401


async def test_rate_route_returns_rate(client, redis):
    await set_rate(redis, "BDT_IDR", "135.2")
    body = client.get("/internal/rates/BDT_IDR").json()
    assert body["pair"] == "BDT_IDR" and D(body["rate"]) == D("135.2")


def test_rate_route_stale_is_503(client):
    assert client.get("/internal/rates/BDT_IDR").status_code == 503


def test_rate_route_unknown_pair_is_404(client):
    assert client.get("/internal/rates/EUR_XYZ").status_code == 404
