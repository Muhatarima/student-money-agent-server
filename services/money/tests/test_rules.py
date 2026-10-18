"""Day 5: auto-exchange rules, freeze, reconciliation."""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from contracts.models import TransferProposal
from fakeredis import FakeAsyncRedis
from sqlalchemy import insert, select, update

from app import identity_client, jobs
from app.executor import GLOBAL_FREEZE_KEY
from app.ledger import HOUSE_FUNDING, get_balance, get_or_create_account, seed_user_funds
from app.models import (
    Freeze,
    LedgerEntry,
    Mandate,
    MandateState,
    OwnerType,
    Transfer,
    TransferState,
)
from app.proposals import spent_this_month_bdt
from app.providers.mock import MockProvider
from app.rules_engine import freeze_user, index_rule, on_rate
from app.transfers import create_transfer
from tests.test_executor import LostRequestProvider, approved_transfer

D = Decimal
S = TransferState


@pytest.fixture
async def redis():
    return FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def provider():
    return MockProvider(failure_rate=0, timeout_rate=0)


@pytest.fixture
def signature_valid(monkeypatch):
    answer = {"valid": True}

    async def verify(data):
        return answer["valid"]

    monkeypatch.setattr(identity_client, "verify_signature", verify)
    return answer


@pytest.fixture
def sessions(session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


async def set_rate(redis, rate, age_seconds=0, pair="BDT_IDR"):
    fetched_at = (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()
    await redis.hset(f"rates:{pair}", mapping={"rate": str(rate), "fetched_at": fetched_at})


async def active_rule(session, redis, target="136", per_run="500", max_month="5000", **changes):
    user_id = uuid.uuid4()
    await seed_user_funds(session, user_id, D("10000"))
    fields = {
        "user_id": user_id,
        "from_currency": "BDT",
        "to_currency": "IDR",
        "target_rate": D(target),
        "amount_per_run": D(per_run),
        "max_per_month": D(max_month),
        "valid_until": datetime.now(UTC) + timedelta(days=30),
        "state": MandateState.ACTIVE,
        "signature_id": str(uuid.uuid4()),
        "payload_hash": "a" * 64,
        "idempotency_key": f"rule-{uuid.uuid4()}",
    }
    mandate = Mandate(**(fields | changes))
    session.add(mandate)
    await session.commit()
    await index_rule(redis, mandate)
    return mandate


async def rate_hits(sessions, redis, provider, rate):
    await set_rate(redis, rate)
    await on_rate(sessions, redis, provider, "BDT_IDR", D(rate))


async def transfers_of(session, mandate) -> list[Transfer]:
    rows = await session.scalars(
        select(Transfer)
        .where(Transfer.mandate_id == mandate.id)
        .execution_options(populate_existing=True)
    )
    return rows.all()


async def event_types(redis) -> list[str]:
    return [json.loads(f["data"])["type"] for _, f in await redis.xrange("events:notify")]


# --- Rate-target rules ---


async def test_rule_fires_when_rate_reaches_target(
    session, sessions, redis, provider, signature_valid
):
    mandate = await active_rule(session, redis, target="136")
    await rate_hits(sessions, redis, provider, "136.5")

    [transfer] = await transfers_of(session, mandate)
    assert transfer.state == S.SETTLED
    assert transfer.amount == D("500")
    balance = await get_balance(session, mandate.user_id)
    assert balance["BDT"] == D("9500")
    assert balance["IDR"] == D("65520")  # (500 - 20 fee) * 136.5
    assert "rule_triggered" in await event_types(redis)


async def test_rule_fires_at_exactly_the_target(
    session, sessions, redis, provider, signature_valid
):
    mandate = await active_rule(session, redis, target="136")
    await rate_hits(sessions, redis, provider, "136")
    assert len(await transfers_of(session, mandate)) == 1


async def test_rule_does_not_fire_below_target(session, sessions, redis, provider, signature_valid):
    mandate = await active_rule(session, redis, target="136")
    await rate_hits(sessions, redis, provider, "135.99")
    assert await transfers_of(session, mandate) == []
    assert provider.submit_calls == []


async def test_rule_fires_at_most_once_per_day(session, sessions, redis, provider, signature_valid):
    mandate = await active_rule(session, redis)
    for rate in ["136.5", "137", "140"]:
        await rate_hits(sessions, redis, provider, rate)
    assert len(await transfers_of(session, mandate)) == 1
    assert len(provider.submit_calls) == 1


async def test_invalid_signature_pauses_rule(session, sessions, redis, provider, signature_valid):
    signature_valid["valid"] = False
    mandate = await active_rule(session, redis)
    await rate_hits(sessions, redis, provider, "140")

    await session.refresh(mandate)
    assert mandate.state == MandateState.PAUSED
    assert await transfers_of(session, mandate) == []
    assert await redis.zscore("rules:BDT_IDR", str(mandate.id)) is None  # unindexed
    assert "rule_paused" in await event_types(redis)


async def test_expired_rule_does_not_fire(session, sessions, redis, provider, signature_valid):
    mandate = await active_rule(
        session, redis, valid_until=datetime.now(UTC) - timedelta(seconds=1)
    )
    await rate_hits(sessions, redis, provider, "140")
    await session.refresh(mandate)
    assert mandate.state == MandateState.EXPIRED
    assert await transfers_of(session, mandate) == []


async def test_paused_rule_does_not_fire(session, sessions, redis, provider, signature_valid):
    mandate = await active_rule(session, redis, state=MandateState.PAUSED)
    await rate_hits(sessions, redis, provider, "140")
    assert await transfers_of(session, mandate) == []


async def test_rule_over_monthly_limit_is_skipped(
    session, sessions, redis, provider, signature_valid
):
    mandate = await active_rule(session, redis, per_run="500", max_month="400")
    await rate_hits(sessions, redis, provider, "140")

    [transfer] = await transfers_of(session, mandate)
    assert transfer.state == S.EXPIRED
    assert transfer.failure_reason == "over_rule_monthly_limit"
    assert provider.submit_calls == []
    assert "rule_skipped" in await event_types(redis)


async def test_stale_rate_fires_nothing(
    session, sessions, redis, provider, signature_valid, monkeypatch
):
    mandate = await active_rule(session, redis)

    async def no_fetch(redis):
        pass  # the rate source is down: nothing new arrives

    monkeypatch.setattr(jobs, "fetch_rates", no_fetch)
    await set_rate(redis, "140", age_seconds=9999)
    await jobs.check_rates_and_rules(sessions, redis, provider)
    assert await transfers_of(session, mandate) == []


async def test_fresh_rate_from_the_job_fires_rule(
    session, sessions, redis, provider, signature_valid
):
    mandate = await active_rule(session, redis, target="130")
    await jobs.check_rates_and_rules(sessions, redis, provider)  # mock rate 135.20
    assert len(await transfers_of(session, mandate)) == 1


# --- Freeze ---


async def test_freeze_pauses_rules_and_blocks_them(
    session, sessions, redis, provider, signature_valid
):
    mandate = await active_rule(session, redis)
    await freeze_user(session, redis, mandate.user_id)

    await session.refresh(mandate)
    assert mandate.state == MandateState.PAUSED
    assert await session.get(Freeze, mandate.user_id) is not None
    assert "frozen" in await event_types(redis)

    await rate_hits(sessions, redis, provider, "140")
    assert await transfers_of(session, mandate) == []


async def test_freezing_twice_is_fine(session, redis):
    user_id = uuid.uuid4()
    await freeze_user(session, redis, user_id)
    await freeze_user(session, redis, user_id)
    assert await session.get(Freeze, user_id) is not None


# --- Reconciliation ---


@pytest.mark.parametrize(("outcome", "expected"), [("settled", S.SETTLED), ("failed", S.FAILED)])
async def test_reconcile_resolves_stuck_submitted(session, redis, provider, outcome, expected):
    t = await approved_transfer(session)
    await jobs.execute_transfer(session, t.id, LostRequestProvider(0, 0), redis, sleep=_no_sleep)
    await _age(session, t.id, minutes=3)
    provider.records[t.idempotency_key] = ("mock-ref", outcome)

    await jobs.reconcile(session, redis, provider)

    done = await session.get(Transfer, t.id, populate_existing=True)
    assert done.state == expected
    assert provider.submit_calls == []  # asked status, never resubmitted


async def test_reconcile_leaves_recent_submitted_alone(session, redis, provider):
    t = await approved_transfer(session)
    await jobs.execute_transfer(session, t.id, LostRequestProvider(0, 0), redis, sleep=_no_sleep)
    provider.records[t.idempotency_key] = ("mock-ref", "settled")
    await jobs.reconcile(session, redis, provider)
    assert (await session.get(Transfer, t.id, populate_existing=True)).state == S.SUBMITTED


async def test_reconcile_picks_up_forgotten_approved(session, redis, provider):
    t = await approved_transfer(session)
    await _age(session, t.id, minutes=2)
    await jobs.reconcile(session, redis, provider)
    # Quote is still valid in this test, so it settles
    assert (await session.get(Transfer, t.id, populate_existing=True)).state == S.SETTLED


async def test_balanced_ledger_does_not_freeze(session, redis, provider):
    await seed_user_funds(session, uuid.uuid4(), D("100"))
    await jobs.reconcile(session, redis, provider)
    assert await redis.get(GLOBAL_FREEZE_KEY) is None


async def test_unbalanced_ledger_turns_on_global_freeze(session, redis, provider):
    account = await get_or_create_account(session, OwnerType.HOUSE, None, HOUSE_FUNDING, "BDT")
    # Bypass post_entries on purpose: a single entry is money from nowhere
    await session.execute(
        insert(LedgerEntry).values(
            transfer_id=uuid.uuid4(), account_id=account.id, amount=D("1"), currency="BDT"
        )
    )
    await jobs.reconcile(session, redis, provider)
    assert await redis.get(GLOBAL_FREEZE_KEY) == "1"


async def test_settled_without_ledger_turns_on_global_freeze(session, redis, provider):
    session.add(
        Transfer(
            user_id=uuid.uuid4(),
            from_currency="BDT",
            to_currency="IDR",
            amount=D("1"),
            idempotency_key=f"test-{uuid.uuid4()}",
            state=S.SETTLED,
        )
    )
    await session.flush()
    await jobs.reconcile(session, redis, provider)
    assert await redis.get(GLOBAL_FREEZE_KEY) == "1"


async def _no_sleep(seconds):
    pass


async def _age(session, transfer_id, minutes):
    await session.execute(
        update(Transfer)
        .where(Transfer.id == transfer_id)
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=minutes))
    )


async def test_rule_run_that_never_got_a_quote_does_not_count_this_month(session, redis):
    # e.g. the rate went stale mid-run: the transfer stays CREATED and can never move money
    mandate = await active_rule(session, redis)
    await set_rate(redis, "136")

    async def mandate_transfer():
        proposal = TransferProposal(
            user_id=mandate.user_id,
            from_currency="BDT",
            to_currency="IDR",
            amount=D("500"),
            idempotency_key=f"mandate-test-{uuid.uuid4()}",
        )
        return await create_transfer(session, proposal, mandate_id=mandate.id)

    await mandate_transfer()  # the dead run
    current = await mandate_transfer()
    assert await spent_this_month_bdt(session, redis, current) == 0
