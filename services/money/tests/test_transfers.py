import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from contracts.models import Currency, TransferProposal
from sqlalchemy import func, pool, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models import Transfer, TransferState
from app.transfers import (
    ALLOWED,
    IdempotencyConflict,
    IllegalTransition,
    TransferNotFound,
    advance,
    create_transfer,
)

S = TransferState


def proposal(**overrides) -> TransferProposal:
    fields = {
        "user_id": uuid.uuid4(),
        "from_currency": Currency.BDT,
        "to_currency": Currency.IDR,
        "amount": Decimal("1000.00"),
        "idempotency_key": f"test-{uuid.uuid4()}",
    }
    return TransferProposal(**(fields | overrides))


async def transfer_in(session, state: TransferState) -> Transfer:
    """Create a transfer and walk it along the allowed path until it reaches `state`."""
    path = {
        S.CREATED: [],
        S.QUOTED: [S.QUOTED],
        S.PENDING_APPROVAL: [S.QUOTED, S.PENDING_APPROVAL],
        S.APPROVED: [S.QUOTED, S.PENDING_APPROVAL, S.APPROVED],
        S.SUBMITTED: [S.QUOTED, S.PENDING_APPROVAL, S.APPROVED, S.SUBMITTED],
        S.SETTLED: [S.QUOTED, S.PENDING_APPROVAL, S.APPROVED, S.SUBMITTED, S.SETTLED],
        S.FAILED: [S.QUOTED, S.PENDING_APPROVAL, S.APPROVED, S.SUBMITTED, S.FAILED],
        S.EXPIRED: [S.QUOTED, S.EXPIRED],
    }[state]
    transfer = await create_transfer(session, proposal())
    for step in path:
        transfer = await advance(session, transfer.id, step)
    assert transfer.state == state
    return transfer


# --- The state machine itself ---


def test_every_state_has_an_entry():
    assert set(ALLOWED) == set(TransferState)


def test_final_states_have_no_exits():
    assert ALLOWED[S.SETTLED] == ALLOWED[S.FAILED] == ALLOWED[S.EXPIRED] == set()


def test_settled_is_only_reachable_from_submitted():
    # Money can only be marked as settled after it was actually sent to the provider
    assert [s for s, nxt in ALLOWED.items() if S.SETTLED in nxt] == [S.SUBMITTED]


def test_submitted_is_only_reachable_from_approved():
    # Nothing goes to the provider without the user's approval
    assert [s for s, nxt in ALLOWED.items() if S.SUBMITTED in nxt] == [S.APPROVED]


ALLOWED_PAIRS = [(a, b) for a in TransferState for b in ALLOWED[a]]
ILLEGAL_PAIRS = [(a, b) for a in TransferState for b in TransferState if b not in ALLOWED[a]]


@pytest.mark.parametrize(("start", "target"), ALLOWED_PAIRS)
async def test_allowed_transition_works(session, start, target):
    transfer = await transfer_in(session, start)
    assert (await advance(session, transfer.id, target)).state == target


@pytest.mark.parametrize(("start", "target"), ILLEGAL_PAIRS)
async def test_illegal_transition_raises_and_changes_nothing(session, start, target):
    transfer = await transfer_in(session, start)
    with pytest.raises(IllegalTransition):
        await advance(session, transfer.id, target)
    await session.refresh(transfer)
    assert transfer.state == start


async def test_advance_saves_fields_and_bumps_updated_at(session):
    transfer = await transfer_in(session, S.APPROVED)
    an_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    await session.execute(
        update(Transfer).where(Transfer.id == transfer.id).values(updated_at=an_hour_ago)
    )
    done = await advance(session, transfer.id, S.SUBMITTED, provider_ref="mock-123")
    assert done.provider_ref == "mock-123"
    assert done.updated_at > an_hour_ago


@pytest.mark.parametrize("field", ["amount", "user_id", "to_currency", "idempotency_key", "state"])
async def test_advance_cannot_change_what_was_approved(session, field):
    transfer = await transfer_in(session, S.QUOTED)
    with pytest.raises(ValueError, match="can't be changed"):
        await advance(session, transfer.id, S.PENDING_APPROVAL, **{field: "x"})


async def test_advance_unknown_transfer_raises(session):
    with pytest.raises(TransferNotFound):
        await advance(session, uuid.uuid4(), S.QUOTED)


# --- Idempotency ---


async def test_new_transfer_starts_created(session):
    p = proposal()
    transfer = await create_transfer(session, p)
    assert transfer.state == S.CREATED
    assert transfer.amount == Decimal("1000")
    assert transfer.from_currency == "BDT" and transfer.to_currency == "IDR"
    assert transfer.idempotency_key == p.idempotency_key


async def test_same_key_twice_returns_same_transfer(session):
    p = proposal()
    first = await create_transfer(session, p)
    second = await create_transfer(session, p)
    assert first.id == second.id
    count = await session.scalar(
        select(func.count())
        .select_from(Transfer)
        .where(Transfer.idempotency_key == p.idempotency_key)
    )
    assert count == 1


async def test_same_key_returns_transfer_in_its_current_state(session):
    p = proposal()
    first = await create_transfer(session, p)
    await advance(session, first.id, S.QUOTED)
    again = await create_transfer(session, p)
    assert again.id == first.id and again.state == S.QUOTED


@pytest.mark.parametrize(
    "change",
    [
        {"amount": Decimal("999.99")},
        {"to_currency": Currency.USD},
        {"from_currency": Currency.USD},
        {"user_id": uuid.uuid4()},
        {"recipient_id": uuid.uuid4()},
    ],
)
async def test_same_key_different_transfer_is_rejected(session, change):
    p = proposal()
    await create_transfer(session, p)
    with pytest.raises(IdempotencyConflict):
        await create_transfer(session, p.model_copy(update=change))


async def test_twenty_concurrent_creates_make_exactly_one_row(migrated_db):
    # Real race: 20 separate DB connections, all committing the same key at once
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=pool.NullPool)
    p = proposal()

    async def create_once() -> uuid.UUID:
        async with AsyncSession(engine) as session:
            transfer_id = (await create_transfer(session, p)).id
            await session.commit()
            return transfer_id

    try:
        ids = await asyncio.gather(*[create_once() for _ in range(20)])
        async with AsyncSession(engine) as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Transfer)
                .where(Transfer.idempotency_key == p.idempotency_key)
            )
    finally:
        await engine.dispose()

    assert count == 1
    assert len(set(ids)) == 1
