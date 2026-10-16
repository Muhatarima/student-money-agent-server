"""The most important tests in the project: money never moves twice, or wrongly."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from contracts.models import Currency, TransferProposal
from fakeredis import FakeAsyncRedis
from sqlalchemy import func, insert, pool, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.events import STREAM
from app.executor import GLOBAL_FREEZE_KEY, STATUS_POLL_DELAYS, execute_transfer
from app.ledger import get_balance, seed_user_funds
from app.models import Freeze, LedgerEntry, Quote, Transfer, TransferState
from app.providers.base import ProviderTimeout, SubmitDetails
from app.providers.mock import MockProvider
from app.transfers import advance, create_transfer

D = Decimal
S = TransferState


class Sleeps:
    """Records waits instead of really sleeping, so tests are fast."""

    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class LostRequestProvider(MockProvider):
    """Times out and never records anything: status stays 'unknown' forever."""

    async def submit(self, idempotency_key: str, details: SubmitDetails) -> str:
        self.submit_calls.append(idempotency_key)
        raise ProviderTimeout("lost")


class ExplodingProvider(MockProvider):
    async def submit(self, idempotency_key: str, details: SubmitDetails) -> str:
        self.submit_calls.append(idempotency_key)
        raise RuntimeError("connection reset")


@pytest.fixture
def redis():
    return FakeAsyncRedis(decode_responses=True)


AMOUNT = D("1000.00")
FUNDS = D("5000")


def reliable() -> MockProvider:
    return MockProvider(failure_rate=0, timeout_rate=0)


async def approved_transfer(
    session,
    amount: Decimal = AMOUNT,
    funds: Decimal | None = FUNDS,
    user_id: uuid.UUID | None = None,
    **quote_overrides,
) -> Transfer:
    user_id = user_id or uuid.uuid4()
    if funds:
        await seed_user_funds(session, user_id, funds)
    transfer = await create_transfer(
        session,
        TransferProposal(
            user_id=user_id,
            from_currency=Currency.BDT,
            to_currency=Currency.IDR,
            amount=amount,
            idempotency_key=f"test-{uuid.uuid4()}",
        ),
    )
    quote_id = uuid.uuid4()
    quote = {
        "id": quote_id,
        "pair": "BDT_IDR",
        "rate": D("135.00"),
        "fee": D("20.00"),
        "amount_in": amount,
        "amount_out": ((amount - D("20")) * D("135")).quantize(D("0.01")),
        "expires_at": datetime.now(UTC) + timedelta(seconds=60),
        "provider": "mock",
    } | quote_overrides
    await session.execute(insert(Quote).values(**quote))
    await advance(session, transfer.id, S.QUOTED, quote_id=quote_id)
    await advance(session, transfer.id, S.PENDING_APPROVAL)
    transfer = await advance(session, transfer.id, S.APPROVED)
    await session.commit()
    # Detached snapshot for the test to read (e.g. idempotency_key) without hitting the DB
    session.expunge(transfer)
    return transfer


async def ledger_rows(session, transfer_id) -> list[tuple[str, Decimal]]:
    rows = await session.execute(
        select(LedgerEntry.currency, LedgerEntry.amount).where(
            LedgerEntry.transfer_id == transfer_id
        )
    )
    return rows.all()


async def events(redis) -> list[dict]:
    return [json.loads(fields["data"]) for _, fields in await redis.xrange(STREAM)]


async def state_of(session, transfer_id) -> TransferState:
    return await session.scalar(
        select(Transfer.state)
        .where(Transfer.id == transfer_id)
        .execution_options(populate_existing=True)
    )


# --- Happy path ---


async def test_happy_path_settles_and_books_ledger(session, redis):
    provider = reliable()
    t = await approved_transfer(session)

    assert await execute_transfer(session, t.id, provider, redis) == S.SETTLED

    assert provider.executions == {t.idempotency_key: 1}
    done = await session.get(Transfer, t.id, populate_existing=True)
    assert done.state == S.SETTLED
    assert done.provider_ref == provider.ref_for(t.idempotency_key)
    # 5000 seeded - 1000 sent; (1000 - 20 fee) * 135 received
    assert await get_balance(session, t.user_id) == {"BDT": D("4000"), "IDR": D("132300")}


async def test_settled_ledger_sums_to_zero_per_currency(session, redis):
    t = await approved_transfer(session)
    await execute_transfer(session, t.id, reliable(), redis)
    totals: dict[str, Decimal] = {}
    for currency, amount in await ledger_rows(session, t.id):
        totals[currency] = totals.get(currency, D(0)) + amount
    assert totals == {"BDT": 0, "IDR": 0}
    assert len(await ledger_rows(session, t.id)) == 5  # incl. the fee entry


async def test_settled_event_is_published_without_personal_data(session, redis):
    t = await approved_transfer(session)
    await execute_transfer(session, t.id, reliable(), redis)
    [event] = await events(redis)
    assert event["type"] == "transfer_settled"
    assert event["user_id"] == str(t.user_id)
    assert event["data"]["transfer_id"] == str(t.id)
    assert D(event["data"]["amount_out"]) == D("132300")
    assert set(event["data"]) == {
        "transfer_id",
        "amount",
        "from_currency",
        "to_currency",
        "amount_out",
        "rate",
        "fee",
    }


async def test_zero_fee_quote_settles_without_fee_entry(session, redis):
    t = await approved_transfer(session, fee=D("0"), amount_out=D("135000.00"))
    assert await execute_transfer(session, t.id, reliable(), redis) == S.SETTLED
    assert len(await ledger_rows(session, t.id)) == 4


# --- Never pay twice ---


async def test_timeout_never_resubmits(session, redis):
    # MOCK_TIMEOUT_RATE=1.0: the provider always times out (after moving the money)
    provider = MockProvider(failure_rate=0, timeout_rate=1.0)
    sleeps = Sleeps()
    t = await approved_transfer(session)

    assert await execute_transfer(session, t.id, provider, redis, sleep=sleeps) == S.SETTLED

    assert provider.submit_calls == [t.idempotency_key]  # submit called exactly ONCE
    assert provider.executions == {t.idempotency_key: 1}
    assert sleeps.calls == [STATUS_POLL_DELAYS[0]]  # asked status, found it settled


async def test_executing_twice_settles_once(session, redis):
    provider = reliable()
    t = await approved_transfer(session)

    assert await execute_transfer(session, t.id, provider, redis) == S.SETTLED
    assert await execute_transfer(session, t.id, provider, redis) == S.SETTLED

    assert provider.submit_calls == [t.idempotency_key]
    assert len(await ledger_rows(session, t.id)) == 5
    assert len(await events(redis)) == 1


async def test_concurrent_executes_submit_once(migrated_db, redis):
    # Real race: 10 workers on separate DB connections grab the same transfer at once
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=pool.NullPool)
    provider = reliable()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            t = await approved_transfer(session)

        async def worker() -> TransferState:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await execute_transfer(session, t.id, provider, redis)

        results = await asyncio.gather(*[worker() for _ in range(10)])
        async with AsyncSession(engine) as session:
            rows = await ledger_rows(session, t.id)
            final = await state_of(session, t.id)
    finally:
        await engine.dispose()

    assert provider.submit_calls == [t.idempotency_key]
    assert final == S.SETTLED
    assert len(rows) == 5
    # The other workers saw it was already taken and stepped aside
    assert all(r in (S.SUBMITTED, S.SETTLED) for r in results)


async def test_unknown_outcome_stays_submitted(session, redis):
    provider = LostRequestProvider(failure_rate=0, timeout_rate=0)
    sleeps = Sleeps()
    t = await approved_transfer(session)

    assert await execute_transfer(session, t.id, provider, redis, sleep=sleeps) == S.SUBMITTED

    assert provider.submit_calls == [t.idempotency_key]
    assert sleeps.calls == list(STATUS_POLL_DELAYS)  # polled 5 times with backoff
    assert await state_of(session, t.id) == S.SUBMITTED
    assert await ledger_rows(session, t.id) == []
    # Running again later must NOT resubmit
    await execute_transfer(session, t.id, provider, redis, sleep=sleeps)
    assert provider.submit_calls == [t.idempotency_key]


async def test_unexpected_provider_error_stays_submitted(session, redis):
    provider = ExplodingProvider(failure_rate=0, timeout_rate=0)
    t = await approved_transfer(session)
    assert await execute_transfer(session, t.id, provider, redis) == S.SUBMITTED
    assert await ledger_rows(session, t.id) == []


async def test_mock_provider_same_key_same_ref_executes_once():
    provider = reliable()
    details = SubmitDetails("BDT", "IDR", D("1"), D("135"))
    first = await provider.submit("key-1", details)
    second = await provider.submit("key-1", details)
    assert first == second
    assert provider.executions == {"key-1": 1}


# --- Failures ---


async def test_rejected_by_provider_fails_without_ledger(session, redis):
    provider = MockProvider(failure_rate=1.0, timeout_rate=0)
    t = await approved_transfer(session)

    assert await execute_transfer(session, t.id, provider, redis) == S.FAILED

    assert await ledger_rows(session, t.id) == []
    assert await get_balance(session, t.user_id) == {"BDT": D("5000")}
    [event] = await events(redis)
    assert event["type"] == "transfer_failed"
    assert event["data"]["reason"] == "provider_rejected"


# --- Blocked before submit (provider must never be called) ---


async def assert_blocked(session, redis, t, reason):
    provider = reliable()
    assert await execute_transfer(session, t.id, provider, redis) == S.EXPIRED
    assert provider.submit_calls == []
    done = await session.get(Transfer, t.id, populate_existing=True)
    assert done.failure_reason == reason
    assert await ledger_rows(session, t.id) == []
    # The user approved it, so they must hear that it didn't happen (and why)
    [event] = await events(redis)
    assert event["type"] == "transfer_failed"
    assert event["data"] == {"transfer_id": str(t.id), "reason": reason}


@pytest.mark.parametrize("state", [S.CREATED, S.QUOTED, S.PENDING_APPROVAL])
async def test_not_approved_is_never_submitted(session, redis, state):
    t = await create_transfer(
        session,
        TransferProposal(
            user_id=uuid.uuid4(),
            from_currency=Currency.BDT,
            to_currency=Currency.IDR,
            amount=D("10"),
            idempotency_key=f"test-{uuid.uuid4()}",
        ),
    )
    for step in [S.QUOTED, S.PENDING_APPROVAL][
        : [S.CREATED, S.QUOTED, S.PENDING_APPROVAL].index(state)
    ]:
        await advance(session, t.id, step)
    provider = reliable()
    assert await execute_transfer(session, t.id, provider, redis) == state
    assert provider.submit_calls == []


async def test_unknown_transfer_is_never_submitted(session, redis):
    provider = reliable()
    assert await execute_transfer(session, uuid.uuid4(), provider, redis) == S.FAILED
    assert provider.submit_calls == []


async def test_frozen_user_is_blocked(session, redis):
    t = await approved_transfer(session)
    # Frozen AFTER approval: the check right before submit must still catch it
    await session.execute(insert(Freeze).values(user_id=t.user_id))
    await assert_blocked(session, redis, t, "user_frozen")


async def test_global_freeze_blocks_everything(session, redis):
    await redis.set(GLOBAL_FREEZE_KEY, "1")
    t = await approved_transfer(session)
    await assert_blocked(session, redis, t, "global_freeze")


async def test_expired_quote_is_blocked(session, redis):
    t = await approved_transfer(session, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    await assert_blocked(session, redis, t, "quote_expired")


async def test_missing_quote_is_blocked(session, redis):
    t = await approved_transfer(session)
    # A real flow can't get here, but if it ever does, fail closed
    await session.execute(
        Transfer.__table__.update().where(Transfer.id == t.id).values(quote_id=None)
    )
    await assert_blocked(session, redis, t, "no_quote")


@pytest.mark.parametrize("change", [{"pair": "BDT_USD"}, {"amount_in": D("999.00")}])
async def test_quote_for_a_different_transfer_is_blocked(session, redis, change):
    t = await approved_transfer(session, **change)
    await assert_blocked(session, redis, t, "quote_mismatch")


async def test_insufficient_funds_is_blocked(session, redis):
    t = await approved_transfer(session, amount=D("1000.00"), funds=D("999.99"))
    await assert_blocked(session, redis, t, "insufficient_funds")


async def test_money_already_in_flight_counts_against_balance(session, redis):
    # 1500 in the wallet. First 1000 is stuck SUBMITTED; a second 1000 must not go out.
    user_id = uuid.uuid4()
    first = await approved_transfer(session, funds=D("1500"), user_id=user_id)
    await execute_transfer(session, first.id, LostRequestProvider(0, 0), redis, sleep=Sleeps())
    assert await state_of(session, first.id) == S.SUBMITTED

    second = await approved_transfer(session, funds=None, user_id=user_id)
    await assert_blocked(session, redis, second, "insufficient_funds")


async def test_exact_balance_is_enough(session, redis):
    t = await approved_transfer(session, amount=D("1000.00"), funds=D("1000.00"))
    assert await execute_transfer(session, t.id, reliable(), redis) == S.SETTLED
    assert (await get_balance(session, t.user_id))["BDT"] == 0


async def test_ledger_row_count_matches_settled_transfers(session, redis):
    before = await session.scalar(select(func.count()).select_from(LedgerEntry))
    t = await approved_transfer(session)
    await execute_transfer(session, t.id, reliable(), redis)
    after = await session.scalar(select(func.count()).select_from(LedgerEntry))
    assert after - before == 2 + 5  # seed (2) + conversion (5)
