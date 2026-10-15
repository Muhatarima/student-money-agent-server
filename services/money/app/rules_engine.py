"""Auto-exchange rules (mandates): "convert X when the rate reaches Y".

Active rules live in Redis sorted sets rules:{pair}, scored by target rate, so one
ZRANGEBYSCORE finds every rule whose target the current rate has reached.
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from contracts.models import Currency, TransferProposal, VerifySignatureIn
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import events, identity_client
from app.executor import execute_transfer
from app.fx import RateStale, create_quote
from app.models import Freeze, Mandate, MandateState, TransferState
from app.policy import evaluate
from app.proposals import build_context
from app.providers.base import Provider
from app.transfers import advance, create_transfer

log = logging.getLogger(__name__)

S = TransferState
ONE_DAY = 86400


def pair_of(mandate: Mandate) -> str:
    return f"{mandate.from_currency}_{mandate.to_currency}"


async def index_rule(redis: Redis, mandate: Mandate) -> None:
    await redis.zadd(f"rules:{pair_of(mandate)}", {str(mandate.id): float(mandate.target_rate)})


async def unindex_rule(redis: Redis, mandate: Mandate) -> None:
    await redis.zrem(f"rules:{pair_of(mandate)}", str(mandate.id))


async def rebuild_index(session: AsyncSession, redis: Redis) -> None:
    """On startup: Postgres is the truth, Redis is just a fast lookup."""
    for mandate in await session.scalars(
        select(Mandate).where(Mandate.state == MandateState.ACTIVE)
    ):
        await index_rule(redis, mandate)


async def freeze_user(session: AsyncSession, redis: Redis, user_id: uuid.UUID) -> None:
    """Kill switch for one user: blocks every submit (executor checks freezes) and pauses rules."""
    await session.execute(pg_insert(Freeze).values(user_id=user_id).on_conflict_do_nothing())
    paused = await session.scalars(
        update(Mandate)
        .where(Mandate.user_id == user_id, Mandate.state == MandateState.ACTIVE)
        .values(state=MandateState.PAUSED)
        .returning(Mandate)
    )
    paused = paused.all()
    await session.commit()
    for mandate in paused:
        await unindex_rule(redis, mandate)
    await events.publish(redis, "frozen", user_id, {})


async def on_rate(
    sessions: async_sessionmaker, redis: Redis, provider: Provider, pair: str, rate: Decimal
) -> None:
    """Called after every FRESH rate fetch. Runs each matching rule at most once a day."""
    today = datetime.now(UTC).date().isoformat()
    for mandate_id in await redis.zrangebyscore(f"rules:{pair}", "-inf", float(rate)):
        # SET NX = only the first caller today gets the lock
        if not await redis.set(f"locks:mandate:{mandate_id}:{today}", "1", nx=True, ex=ONE_DAY):
            continue
        try:
            async with sessions() as session:
                await run_mandate(session, redis, provider, uuid.UUID(mandate_id), rate, today)
        except Exception:
            log.exception("mandate %s failed", mandate_id)


async def run_mandate(
    session: AsyncSession,
    redis: Redis,
    provider: Provider,
    mandate_id: uuid.UUID,
    rate: Decimal,
    today: str,
) -> None:
    mandate = await session.get(Mandate, mandate_id)
    if mandate is None or mandate.state != MandateState.ACTIVE:
        if mandate is not None:
            await unindex_rule(redis, mandate)
        return
    if mandate.valid_until <= datetime.now(UTC):
        mandate.state = MandateState.EXPIRED
        await session.commit()
        await unindex_rule(redis, mandate)
        return
    if rate < mandate.target_rate:  # exact Decimal check (Redis scores are floats)
        return

    # Check the user's signature EVERY run. Can't verify -> pause the rule (fail closed).
    valid = await identity_client.verify_signature(
        VerifySignatureIn(
            user_id=mandate.user_id,
            signature_id=uuid.UUID(mandate.signature_id),
            payload_hash=mandate.payload_hash,
        )
    )
    if not valid:
        mandate.state = MandateState.PAUSED
        await session.commit()
        await unindex_rule(redis, mandate)
        await events.publish(redis, "rule_paused", mandate.user_id, {"rule_id": str(mandate.id)})
        return

    proposal = TransferProposal(
        user_id=mandate.user_id,
        from_currency=Currency(mandate.from_currency),
        to_currency=Currency(mandate.to_currency),
        amount=mandate.amount_per_run.quantize(Decimal("0.01"), rounding=ROUND_DOWN),
        idempotency_key=f"mandate:{mandate.id}:{today}",
    )
    transfer = await create_transfer(session, proposal, mandate_id=mandate.id)
    if transfer.state != S.CREATED:
        return  # already ran today

    try:
        quote = await create_quote(session, redis, pair_of(mandate), transfer.amount)
        decision = evaluate(await build_context(session, redis, transfer, quote, mandate))
    except (RateStale, ValueError) as e:
        await session.commit()
        await skip(redis, mandate, type(e).__name__)
        return

    await advance(session, transfer.id, S.QUOTED, quote_id=quote.id)
    if not decision.allowed:
        reason = ", ".join(decision.reasons)
        await advance(session, transfer.id, S.EXPIRED, failure_reason=reason)
        await session.commit()
        await skip(redis, mandate, reason)
        return

    # The user pre-approved by signing the rule, so it goes straight through to APPROVED
    await advance(session, transfer.id, S.PENDING_APPROVAL)
    await advance(session, transfer.id, S.APPROVED, approval_id=mandate.signature_id)
    mandate.last_run_at = datetime.now(UTC)
    await session.commit()

    # Read these now: the executor may reset the session
    user_id, data = mandate.user_id, {"rule_id": str(mandate.id), "transfer_id": str(transfer.id)}
    state = await execute_transfer(session, transfer.id, provider, redis)
    await events.publish(redis, "rule_triggered", user_id, data | {"state": state.value})


async def skip(redis: Redis, mandate: Mandate, reason: str) -> None:
    await events.publish(
        redis, "rule_skipped", mandate.user_id, {"rule_id": str(mandate.id), "reason": reason}
    )
