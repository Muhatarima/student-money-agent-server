"""Transfer state machine. Only the arrows in ALLOWED exist; anything else is a bug.

CREATED -> QUOTED -> PENDING_APPROVAL -> APPROVED -> SUBMITTED -> SETTLED
                           |                |           '-> FAILED
                           '-> EXPIRED      '-> EXPIRED
"""

import uuid

from contracts.models import TransferProposal
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transfer, TransferState

S = TransferState

ALLOWED: dict[TransferState, set[TransferState]] = {
    S.CREATED: {S.QUOTED},
    S.QUOTED: {S.PENDING_APPROVAL, S.EXPIRED},
    S.PENDING_APPROVAL: {S.APPROVED, S.EXPIRED},
    S.APPROVED: {S.SUBMITTED, S.EXPIRED},
    S.SUBMITTED: {S.SETTLED, S.FAILED},
    # Final states: nothing can come after them
    S.SETTLED: set(),
    S.FAILED: set(),
    S.EXPIRED: set(),
}

# Only these may change along with the state. What the user approved (amount, currencies,
# user, recipient, idempotency key) can never be changed after the transfer is created.
UPDATABLE_FIELDS = {"quote_id", "payload_hash", "approval_id", "provider_ref", "failure_reason"}


class IllegalTransition(Exception):
    pass


class TransferNotFound(Exception):
    pass


class IdempotencyConflict(Exception):
    """Same idempotency key was used before, but for a different transfer."""


async def advance(
    session: AsyncSession, transfer_id: uuid.UUID, new_state: TransferState, **fields
) -> Transfer:
    """Move a transfer to new_state. The caller commits."""
    bad_fields = set(fields) - UPDATABLE_FIELDS
    if bad_fields:
        raise ValueError(f"these fields can't be changed: {sorted(bad_fields)}")

    # FOR UPDATE locks the row until commit, so two workers can't both advance it
    transfer = await session.scalar(
        select(Transfer).where(Transfer.id == transfer_id).with_for_update()
    )
    if transfer is None:
        raise TransferNotFound(str(transfer_id))
    if new_state not in ALLOWED[transfer.state]:
        raise IllegalTransition(f"{transfer.state} -> {new_state}")

    transfer.state = new_state
    for name, value in fields.items():
        setattr(transfer, name, value)
    transfer.updated_at = func.now()
    await session.flush()
    await session.refresh(transfer)
    return transfer


async def create_transfer(
    session: AsyncSession, proposal: TransferProposal, mandate_id: uuid.UUID | None = None
) -> Transfer:
    """Create the transfer, or return the existing one if this idempotency key was seen before.
    The caller commits."""
    values = {
        "user_id": proposal.user_id,
        "from_currency": proposal.from_currency.value,
        "to_currency": proposal.to_currency.value,
        "amount": proposal.amount,
        "recipient_id": proposal.recipient_id,
        "mandate_id": mandate_id,
    }
    # ON CONFLICT DO NOTHING: if 20 calls race, Postgres lets exactly one insert win
    await session.execute(
        pg_insert(Transfer)
        .values(id=uuid.uuid4(), idempotency_key=proposal.idempotency_key, **values)
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    transfer = await session.scalar(
        select(Transfer)
        .where(Transfer.idempotency_key == proposal.idempotency_key)
        .execution_options(populate_existing=True)
    )

    # Fail closed: a reused key must describe the exact same transfer
    existing = {name: getattr(transfer, name) for name in values}
    if existing != values:
        raise IdempotencyConflict("idempotency key already used for a different transfer")
    return transfer
