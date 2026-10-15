"""Exchange rates (kept in Redis) and quotes (kept in Postgres)."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal, InvalidOperation

import httpx
from contracts.models import RateOut
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Quote

PAIRS = ["BDT_IDR", "BDT_USD", "USD_IDR"]
# Used when RATE_SOURCE_URL is empty. Change a rate for demos:
#   redis-cli SET rates:mock:BDT_IDR 136.5
MOCK_DEFAULTS = {"BDT_IDR": "135.20", "BDT_USD": "0.0082", "USD_IDR": "16500"}
CENT = Decimal("0.01")


class RateStale(Exception):
    """No rate, or too old to trust. Callers must block the action."""


def parse_rate(value) -> Decimal | None:
    """A rate we can trust, or None. Never raises on bad input from outside."""
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return rate if rate.is_finite() and rate > 0 else None


async def fetch_rates(redis: Redis) -> None:
    """Get fresh rates and store them as Redis hash rates:{pair} = {rate, fetched_at}."""
    if settings.rate_source_url:
        rates = await _fetch_from_api(settings.rate_source_url)
    else:
        rates = {
            pair: await redis.get(f"rates:mock:{pair}") or MOCK_DEFAULTS[pair] for pair in PAIRS
        }

    now = datetime.now(UTC).isoformat()
    for pair, value in rates.items():
        rate = parse_rate(value)
        if rate is not None:  # bad value: skip it, the old one goes stale and blocks
            await redis.hset(f"rates:{pair}", mapping={"rate": str(rate), "fetched_at": now})


async def _fetch_from_api(url: str) -> dict:
    # Expects open.er-api.com style: GET {url}/BDT -> {"rates": {"IDR": 135.2, ...}}
    rates = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for pair in PAIRS:
            base, quote = pair.split("_")
            try:
                response = await client.get(f"{url.rstrip('/')}/{base}")
                rates[pair] = response.json()["rates"][quote]
            except Exception:
                continue  # missing rate -> stale -> blocked
    return rates


async def get_rate(redis: Redis, pair: str) -> RateOut:
    data = await redis.hgetall(f"rates:{pair}")
    rate = parse_rate(data.get("rate"))
    if rate is None:
        raise RateStale(f"no rate for {pair}")
    fetched_at = datetime.fromisoformat(data["fetched_at"])
    if datetime.now(UTC) - fetched_at > timedelta(seconds=settings.rate_stale_seconds):
        raise RateStale(f"rate for {pair} is too old")
    return RateOut(pair=pair, rate=rate, fetched_at=fetched_at)


def calc_fee(amount: Decimal, from_currency: str) -> Decimal:
    fee = (amount * settings.fee_percent / 100).quantize(CENT, rounding=ROUND_UP)
    if from_currency == "BDT":
        fee = max(fee, settings.fee_min_bdt)
    return fee


async def create_quote(session: AsyncSession, redis: Redis, pair: str, amount: Decimal) -> Quote:
    """Raises RateStale if the rate can't be trusted, ValueError if the fee eats the amount."""
    rate = (await get_rate(redis, pair)).rate
    fee = calc_fee(amount, pair.split("_")[0])
    if amount <= fee:
        raise ValueError("amount is too small to cover the fee")

    quote = Quote(
        pair=pair,
        rate=rate,
        fee=fee,
        amount_in=amount,
        amount_out=((amount - fee) * rate).quantize(CENT, rounding=ROUND_DOWN),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.quote_ttl_seconds),
        provider="mock",
    )
    session.add(quote)
    await session.flush()
    return quote
