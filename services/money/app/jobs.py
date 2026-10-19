"""Background loops. Run with: python -m app.jobs"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.executor import GLOBAL_FREEZE_KEY, execute_transfer, fail, settle
from app.fx import PAIRS, RateStale, fetch_rates, get_rate
from app.models import LedgerEntry, Transfer, TransferState
from app.providers.base import Provider
from app.providers.mock import MockProvider
from app.redis_client import redis
from app.rules_engine import on_rate, rebuild_index

log = logging.getLogger(__name__)

S = TransferState
RATES_EVERY = 60
RECONCILE_EVERY = 300


async def check_rates_and_rules(sessions, redis: Redis, provider: Provider) -> None:
    await fetch_rates(redis)
    for pair in PAIRS:
        try:
            rate = await get_rate(redis, pair)
        except RateStale:
            # Stale-rate guard: never fire rules on a rate we can't trust
            log.warning("rate for %s is stale, skipping its rules", pair)
            continue
        await on_rate(sessions, redis, provider, pair, rate.rate)


async def reconcile(session: AsyncSession, redis: Redis, provider: Provider) -> None:
    now = datetime.now(UTC)

    # 1. Stuck in SUBMITTED: ask the provider what happened (never resubmit)
    stuck = await session.execute(
        select(Transfer.id, Transfer.idempotency_key).where(
            Transfer.state == S.SUBMITTED, Transfer.updated_at < now - timedelta(minutes=2)
        )
    )
    for transfer_id, key in stuck.all():
        try:
            status = await provider.status(key)
            if status == "settled":
                await settle(session, redis, transfer_id, provider_ref=None)
            elif status == "failed":
                await fail(session, redis, transfer_id, "provider_failed")
        except Exception:
            await session.rollback()
            log.exception("reconcile: transfer %s", transfer_id)

    # 2. Approved but never executed (e.g. the API restarted). The executor
    #    re-checks everything and expires it if the quote is too old.
    approved = await session.scalars(
        select(Transfer.id).where(
            Transfer.state == S.APPROVED, Transfer.updated_at < now - timedelta(minutes=1)
        )
    )
    for transfer_id in approved.all():
        await execute_transfer(session, transfer_id, provider, redis)

    # 3. The books must balance. If not, stop ALL money movement (fail closed).
    unbalanced = await session.scalar(
        select(LedgerEntry.transfer_id)
        .group_by(LedgerEntry.transfer_id, LedgerEntry.currency)
        .having(func.sum(LedgerEntry.amount) != 0)
        .limit(1)
    )
    settled_without_entries = await session.scalar(
        select(Transfer.id)
        .where(
            Transfer.state == S.SETTLED,
            ~exists().where(LedgerEntry.transfer_id == Transfer.id),
        )
        .limit(1)
    )
    if unbalanced or settled_without_entries:
        log.critical(
            "LEDGER MISMATCH (transfer %s) - global freeze ON",
            unbalanced or settled_without_entries,
        )
        await redis.set(GLOBAL_FREEZE_KEY, "1")


async def every(seconds: int, job) -> None:
    # One failure must never stop the loop
    while True:
        try:
            await job()
        except Exception:
            log.exception("job failed")
        await asyncio.sleep(seconds)


async def main(provider: Provider | None = None) -> None:
    # Inside money-api (RUN_JOBS_IN_API) we get the API's provider: one provider per process
    provider = provider or MockProvider()
    async with SessionLocal() as session:
        await rebuild_index(session, redis)

    async def reconcile_job():
        async with SessionLocal() as session:
            await reconcile(session, redis, provider)

    await asyncio.gather(
        every(RATES_EVERY, lambda: check_rates_and_rules(SessionLocal, redis, provider)),
        every(RECONCILE_EVERY, reconcile_job),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
