"""Proposal -> quote -> policy -> approval (C) -> confirmed. Nothing here moves money."""

import hmac
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from contracts.hashing import payload_hash
from contracts.models import (
    ApprovalConfirmed,
    ApprovalCreate,
    ProposalResult,
    RuleProposal,
    TransferProposal,
)
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import events, identity_client
from app.fx import PAIRS, RateStale, create_quote, get_rate
from app.models import Freeze, Mandate, MandateState, Quote, Recipient, Transfer, TransferState
from app.policy import PolicyContext, evaluate
from app.transfers import IdempotencyConflict, IllegalTransition, advance, create_transfer

log = logging.getLogger(__name__)

S = TransferState
RULE_APPROVAL_TTL = timedelta(minutes=15)


class ConfirmError(Exception):
    """Confirmation refused. status_code is what the API returns."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def rejected(reason: str, subject_id: uuid.UUID | None = None) -> ProposalResult:
    return ProposalResult(status="rejected", reason=reason, subject_id=subject_id)


async def to_bdt(redis: Redis, currency: str, amount: Decimal) -> Decimal:
    if currency == "BDT":
        return amount
    return amount / (await get_rate(redis, f"BDT_{currency}")).rate


async def spent_today_bdt(session: AsyncSession, redis: Redis, transfer: Transfer) -> Decimal:
    """What the user already sent or is sending today (UTC), not counting this transfer."""
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = await session.execute(
        select(Transfer.from_currency, func.sum(Transfer.amount))
        .where(
            Transfer.user_id == transfer.user_id,
            Transfer.id != transfer.id,
            Transfer.created_at >= midnight,
            Transfer.state.not_in([S.EXPIRED, S.FAILED]),
        )
        .group_by(Transfer.from_currency)
    )
    return sum([await to_bdt(redis, cur, amount) for cur, amount in rows.all()], Decimal(0))


async def recipient_cooling(session: AsyncSession, transfer: Transfer) -> bool:
    if transfer.recipient_id is None:
        return False  # user's own wallet
    recipient = await session.get(Recipient, transfer.recipient_id)
    return recipient.cooling_until > datetime.now(UTC)


async def spent_this_month_bdt(session: AsyncSession, redis: Redis, transfer: Transfer) -> Decimal:
    """What this transfer's rule already converted this month (UTC), not counting this one."""
    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = await session.scalar(
        select(func.coalesce(func.sum(Transfer.amount), 0)).where(
            Transfer.mandate_id == transfer.mandate_id,
            Transfer.id != transfer.id,
            Transfer.created_at >= month_start,
            Transfer.state.not_in([S.EXPIRED, S.FAILED]),
        )
    )
    return await to_bdt(redis, transfer.from_currency, Decimal(total))


async def build_context(
    session: AsyncSession,
    redis: Redis,
    transfer: Transfer,
    quote: Quote,
    mandate: Mandate | None = None,
) -> PolicyContext:
    """Collect every fact the policy engine needs. Raises RateStale."""
    now = datetime.now(UTC)
    rate = await get_rate(redis, quote.pair)
    ctx = PolicyContext(
        user_frozen=await session.get(Freeze, transfer.user_id) is not None,
        amount_bdt=await to_bdt(redis, transfer.from_currency, transfer.amount),
        spent_today_bdt=await spent_today_bdt(session, redis, transfer),
        spent_this_month_for_mandate=None,
        mandate_max_per_month=None,
        rate_age_seconds=int((now - rate.fetched_at).total_seconds()),
        quote_expires_in_seconds=int((quote.expires_at - now).total_seconds()),
        recipient_cooling_active=await recipient_cooling(session, transfer),
        mandate_active=None,
        mandate_valid_until_passed=None,
        currency_pair_supported=quote.pair in PAIRS,
    )
    if mandate is not None:
        ctx.mandate_active = mandate.state == MandateState.ACTIVE
        ctx.mandate_valid_until_passed = mandate.valid_until <= now
        ctx.spent_this_month_for_mandate = await spent_this_month_bdt(session, redis, transfer)
        ctx.mandate_max_per_month = await to_bdt(
            redis, mandate.from_currency, mandate.max_per_month
        )
    return ctx


async def propose_transfer(
    session: AsyncSession, redis: Redis, proposal: TransferProposal
) -> ProposalResult:
    if proposal.recipient_id is not None:
        recipient = await session.get(Recipient, proposal.recipient_id)
        if recipient is None or recipient.user_id != proposal.user_id:
            return rejected("unknown_recipient")
    try:
        transfer = await create_transfer(session, proposal)
    except IdempotencyConflict:
        return rejected("idempotency_key_reused")
    if transfer.state != S.CREATED:
        # A retry of a proposal we already handled: report where it is now
        return ProposalResult(status=transfer.state.lower(), subject_id=transfer.id)

    pair = f"{transfer.from_currency}_{transfer.to_currency}"
    if pair not in PAIRS:
        await session.commit()
        return rejected("pair_not_supported", transfer.id)
    try:
        quote = await create_quote(session, redis, pair, transfer.amount)
        ctx = await build_context(session, redis, transfer, quote)
    except RateStale:
        await session.commit()  # transfer stays CREATED; a retry tries again
        return rejected("rate_unavailable", transfer.id)
    except ValueError:
        await session.commit()
        return rejected("amount_too_small", transfer.id)

    summary = {
        "from_currency": transfer.from_currency,
        "to_currency": transfer.to_currency,
        "amount": str(quote.amount_in),
        "rate": str(quote.rate),
        "fee": str(quote.fee),
        "amount_out": str(quote.amount_out),
        "expires_at": quote.expires_at.isoformat(),
        "provider": quote.provider,
    }
    try:
        await advance(
            session, transfer.id, S.QUOTED, quote_id=quote.id, payload_hash=payload_hash(summary)
        )
    except IllegalTransition:
        # Same proposal arrived twice at the same moment; the other request is handling it
        await session.rollback()
        return ProposalResult(status="in_progress", subject_id=transfer.id)

    decision = evaluate(ctx)
    if not decision.allowed:
        reason = ", ".join(decision.reasons)
        await advance(session, transfer.id, S.EXPIRED, failure_reason=reason)
        await session.commit()
        return rejected(reason, transfer.id)
    await session.commit()  # don't hold the row lock while we wait for C

    try:
        approval = await identity_client.create_approval(
            ApprovalCreate(
                user_id=transfer.user_id,
                kind="transfer",
                subject_id=transfer.id,
                payload_hash=transfer.payload_hash,
                summary=summary,
                expires_at=quote.expires_at,
            )
        )
    except Exception:
        log.exception("transfer %s: could not create approval", transfer.id)
        await advance(session, transfer.id, S.EXPIRED, failure_reason="approval_unavailable")
        await session.commit()
        return rejected("approval_unavailable", transfer.id)

    await advance(session, transfer.id, S.PENDING_APPROVAL, approval_id=str(approval.approval_id))
    await session.commit()
    await events.publish(redis, "approval_needed", transfer.user_id, {"url": approval.url})
    return ProposalResult(
        status="pending_approval", approval_url=approval.url, subject_id=transfer.id
    )


async def propose_rule(session: AsyncSession, redis: Redis, rule: RuleProposal) -> ProposalResult:
    pair = f"{rule.from_currency.value}_{rule.to_currency.value}"
    if pair not in PAIRS:
        return rejected("pair_not_supported")
    if rule.valid_until <= datetime.now(UTC):
        return rejected("valid_until_in_past")

    summary = {
        "from_currency": rule.from_currency.value,
        "to_currency": rule.to_currency.value,
        "target_rate": str(rule.target_rate),
        "amount_per_run": str(rule.amount_per_run),
        "max_per_month": str(rule.max_per_month),
        "valid_until": rule.valid_until.isoformat(),
    }
    hash_ = payload_hash(summary)
    # Same key twice -> one mandate (ON CONFLICT DO NOTHING, like transfers)
    await session.execute(
        pg_insert(Mandate)
        .values(
            id=uuid.uuid4(),
            user_id=rule.user_id,
            from_currency=rule.from_currency.value,
            to_currency=rule.to_currency.value,
            target_rate=rule.target_rate,
            amount_per_run=rule.amount_per_run,
            max_per_month=rule.max_per_month,
            valid_until=rule.valid_until,
            state=MandateState.PENDING_SIGNATURE,
            payload_hash=hash_,
            idempotency_key=rule.idempotency_key,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    mandate = await session.scalar(
        select(Mandate).where(Mandate.idempotency_key == rule.idempotency_key)
    )
    await session.commit()
    if mandate.user_id != rule.user_id or mandate.payload_hash != hash_:
        return rejected("idempotency_key_reused")
    if mandate.state != MandateState.PENDING_SIGNATURE:
        return ProposalResult(status=mandate.state.value, subject_id=mandate.id)

    try:
        approval = await identity_client.create_approval(
            ApprovalCreate(
                user_id=mandate.user_id,
                kind="rule",
                subject_id=mandate.id,
                payload_hash=mandate.payload_hash,
                summary=summary,
                expires_at=datetime.now(UTC) + RULE_APPROVAL_TTL,
            )
        )
    except Exception:
        log.exception("mandate %s: could not create approval", mandate.id)
        return rejected("approval_unavailable", mandate.id)

    await events.publish(redis, "approval_needed", mandate.user_id, {"url": approval.url})
    return ProposalResult(
        status="pending_approval", approval_url=approval.url, subject_id=mandate.id
    )


async def confirm_approval(session: AsyncSession, confirmed: ApprovalConfirmed) -> str:
    """Returns "transfer" (caller must start execute_transfer) or "rule". Raises ConfirmError."""
    transfer = await session.scalar(
        select(Transfer).where(Transfer.id == confirmed.subject_id).with_for_update()
    )
    if transfer is not None:
        await _confirm_transfer(session, transfer, confirmed)
        return "transfer"

    mandate = await session.scalar(
        select(Mandate).where(Mandate.id == confirmed.subject_id).with_for_update()
    )
    if mandate is not None:
        _check_hash(mandate.payload_hash, confirmed.payload_hash)
        if mandate.state != MandateState.PENDING_SIGNATURE:
            raise ConfirmError(409, "already confirmed")
        mandate.signature_id = str(confirmed.signature_id)
        mandate.state = MandateState.ACTIVE
        await session.commit()
        return "rule"

    raise ConfirmError(404, "unknown subject")


def _check_hash(expected: str | None, given: str) -> None:
    # What the user signed must be exactly what we stored
    if not expected or not hmac.compare_digest(expected.encode(), given.encode()):
        raise ConfirmError(409, "payload hash mismatch")


async def _confirm_transfer(
    session: AsyncSession, transfer: Transfer, confirmed: ApprovalConfirmed
) -> None:
    _check_hash(transfer.payload_hash, confirmed.payload_hash)
    if transfer.approval_id != str(confirmed.approval_id):
        raise ConfirmError(409, "approval does not match")
    # State check makes every confirmation single-use
    if transfer.state != S.PENDING_APPROVAL:
        raise ConfirmError(409, "not waiting for approval")

    quote = await session.get(Quote, transfer.quote_id)
    if quote.expires_at <= datetime.now(UTC):
        await advance(session, transfer.id, S.EXPIRED, failure_reason="quote_expired")
        await session.commit()
        raise ConfirmError(409, "quote expired")

    await advance(session, transfer.id, S.APPROVED)
    await session.commit()
