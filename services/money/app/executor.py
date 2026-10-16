"""Executes APPROVED transfers. The one place that sends money to the provider.

Golden rule: once a transfer is SUBMITTED, never call provider.submit for it again.
If we don't know what happened, leave it SUBMITTED; reconciliation asks the provider later.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import events
from app.ledger import HOUSE_FEES, HOUSE_FX, USER_WALLET, get_or_create_account, post_entries
from app.models import Account, Freeze, LedgerEntry, OwnerType, Quote, Transfer, TransferState
from app.providers.base import Provider, ProviderRejected, ProviderTimeout, SubmitDetails
from app.transfers import advance

log = logging.getLogger(__name__)

S = TransferState
GLOBAL_FREEZE_KEY = "money:global_freeze"
# Waits between status checks after a timeout (seconds)
STATUS_POLL_DELAYS = (0.5, 1, 2, 4, 8)

Sleep = Callable[[float], Awaitable[None]]


async def execute_transfer(
    session: AsyncSession,
    transfer_id: uuid.UUID,
    provider: Provider,
    redis: Redis,
    sleep: Sleep = asyncio.sleep,
) -> TransferState:
    """Safe to call any number of times, even at once: only an APPROVED transfer is sent."""
    # --- 1. Checks, while holding the row lock ---
    # populate_existing: always read the row fresh from the DB, never a cached copy
    transfer = await session.scalar(
        select(Transfer)
        .where(Transfer.id == transfer_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if transfer is None or transfer.state != S.APPROVED:
        # Already handled (or being handled) by someone else
        current = transfer.state if transfer else S.FAILED
        await session.rollback()
        return current

    reason = await _blocking_reason(session, transfer, redis)
    if reason:
        await advance(session, transfer.id, S.EXPIRED, failure_reason=reason)
        await session.commit()
        log.warning("transfer %s not submitted: %s", transfer.id, reason)
        # The user already approved it, so tell them it didn't happen
        await _notify(redis, "transfer_failed", transfer, {"reason": reason})
        return S.EXPIRED

    quote = await session.get(Quote, transfer.quote_id)
    # --- 2. Mark SUBMITTED and COMMIT before calling the provider ---
    # If we crash after this line, a restart sees SUBMITTED and never resubmits.
    await advance(session, transfer.id, S.SUBMITTED)
    await session.commit()

    details = SubmitDetails(
        from_currency=transfer.from_currency,
        to_currency=transfer.to_currency,
        amount_in=transfer.amount,
        amount_out=quote.amount_out,
    )
    try:
        provider_ref = await provider.submit(transfer.idempotency_key, details)
    except ProviderRejected:
        return await fail(session, redis, transfer.id, "provider_rejected")
    except ProviderTimeout:
        return await _resolve_after_timeout(session, transfer, provider, redis, sleep)
    except Exception:
        # Unknown outcome: maybe the money moved. Leave SUBMITTED for reconciliation.
        log.exception("transfer %s: unexpected provider error, left SUBMITTED", transfer.id)
        return S.SUBMITTED

    return await settle(session, redis, transfer.id, provider_ref)


async def _resolve_after_timeout(
    session: AsyncSession, transfer: Transfer, provider: Provider, redis: Redis, sleep: Sleep
) -> TransferState:
    # Ask, never resubmit
    for delay in STATUS_POLL_DELAYS:
        await sleep(delay)
        try:
            status = await provider.status(transfer.idempotency_key)
        except Exception:
            log.warning("transfer %s: status check failed, will retry", transfer.id)
            continue
        if status == "settled":
            return await settle(session, redis, transfer.id, provider_ref=None)
        if status == "failed":
            return await fail(session, redis, transfer.id, "provider_failed")
    log.warning("transfer %s: still unknown after timeout, left SUBMITTED", transfer.id)
    return S.SUBMITTED


async def _blocking_reason(session: AsyncSession, transfer: Transfer, redis: Redis) -> str | None:
    """Why this transfer must not be sent, or None if it may. Checked right before submit."""
    if await redis.get(GLOBAL_FREEZE_KEY) == "1":
        return "global_freeze"
    if await session.get(Freeze, transfer.user_id) is not None:
        return "user_frozen"

    quote = await session.get(Quote, transfer.quote_id) if transfer.quote_id else None
    if quote is None:
        return "no_quote"
    if quote.expires_at <= datetime.now(UTC):
        return "quote_expired"
    # The quote must be for exactly this transfer
    if quote.pair != f"{transfer.from_currency}_{transfer.to_currency}" or (
        quote.amount_in != transfer.amount
    ):
        return "quote_mismatch"

    if await _available_balance(session, transfer) < transfer.amount:
        return "insufficient_funds"
    return None


async def _available_balance(session: AsyncSession, transfer: Transfer) -> Decimal:
    """Wallet balance minus money already on its way out (SUBMITTED, not yet settled)."""
    wallet = await get_or_create_account(
        session, OwnerType.USER, transfer.user_id, USER_WALLET, transfer.from_currency
    )
    # Lock the wallet row: two transfers for one user check their balance one after the other
    await session.execute(select(Account.id).where(Account.id == wallet.id).with_for_update())
    balance = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account_id == wallet.id
        )
    )
    in_flight = await session.scalar(
        select(func.coalesce(func.sum(Transfer.amount), 0)).where(
            Transfer.user_id == transfer.user_id,
            Transfer.from_currency == transfer.from_currency,
            Transfer.state == S.SUBMITTED,
        )
    )
    return Decimal(balance) - Decimal(in_flight)


async def settle(
    session: AsyncSession, redis: Redis, transfer_id: uuid.UUID, provider_ref: str | None
) -> TransferState:
    """Ledger entries + SETTLED in ONE transaction. Used by the executor and reconciliation."""
    fields = {"provider_ref": provider_ref} if provider_ref else {}
    transfer = await advance(session, transfer_id, S.SETTLED, **fields)
    quote = await session.get(Quote, transfer.quote_id)
    user, frm, to = transfer.user_id, transfer.from_currency, transfer.to_currency

    async def account(owner: OwnerType, owner_id, name: str, currency: str) -> Account:
        return await get_or_create_account(session, owner, owner_id, name, currency)

    entries = [
        (await account(OwnerType.USER, user, USER_WALLET, frm), -transfer.amount),
        (await account(OwnerType.HOUSE, None, HOUSE_FX, frm), transfer.amount - quote.fee),
        (await account(OwnerType.HOUSE, None, HOUSE_FX, to), -quote.amount_out),
        (await account(OwnerType.USER, user, USER_WALLET, to), quote.amount_out),
    ]
    if quote.fee:
        entries.append((await account(OwnerType.HOUSE, None, HOUSE_FEES, frm), quote.fee))

    try:
        await post_entries(session, transfer.id, entries)
        await session.commit()
    except Exception:
        # The provider moved the money but our books didn't record it. Stay SUBMITTED.
        await session.rollback()
        log.critical("transfer %s settled at provider but ledger post failed", transfer_id)
        raise

    await _notify(
        redis,
        "transfer_settled",
        transfer,
        {
            "amount": str(transfer.amount),
            "from_currency": frm,
            "to_currency": to,
            "amount_out": str(quote.amount_out),
            "rate": str(quote.rate),
            "fee": str(quote.fee),
        },
    )
    return S.SETTLED


async def fail(
    session: AsyncSession, redis: Redis, transfer_id: uuid.UUID, reason: str
) -> TransferState:
    transfer = await advance(session, transfer_id, S.FAILED, failure_reason=reason)
    await session.commit()
    await _notify(redis, "transfer_failed", transfer, {"reason": reason})
    return S.FAILED


async def _notify(redis: Redis, type: str, transfer: Transfer, data: dict) -> None:
    # The money part is already committed; a lost notification must not undo it
    try:
        await events.publish(
            redis, type, transfer.user_id, {"transfer_id": str(transfer.id)} | data
        )
    except Exception:
        log.exception("transfer %s: could not publish %s event", transfer.id, type)
