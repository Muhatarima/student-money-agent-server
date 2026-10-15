"""Double-entry ledger: the single source of truth for balances.

Every money movement is a group of entries (one transfer_id) that sums to exactly 0
per currency. Money never appears from nowhere or vanishes.
"""

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Account, LedgerEntry, OwnerType

# Must match the ledger_entries.amount column (Numeric(20, 4))
AMOUNT_PLACES = 4

USER_WALLET = "wallet"
HOUSE_FUNDING = "funding"
HOUSE_FX = "fx"
HOUSE_FEES = "fees"


class LedgerError(ValueError):
    """The entries can't be posted. Nothing was written."""


class LedgerImbalance(LedgerError):
    """Entries for a currency don't sum to exactly 0."""


async def get_or_create_account(
    session: AsyncSession,
    owner_type: OwnerType,
    owner_id: uuid.UUID | None,
    name: str,
    currency: str,
) -> Account:
    # INSERT ... ON CONFLICT DO NOTHING, then SELECT: two callers at once still get one account
    await session.execute(
        pg_insert(Account)
        .values(owner_type=owner_type, owner_id=owner_id, name=name, currency=currency)
        .on_conflict_do_nothing()
    )
    owner_match = Account.owner_id.is_(None) if owner_id is None else Account.owner_id == owner_id
    return await session.scalar(
        select(Account).where(
            Account.owner_type == owner_type,
            owner_match,
            Account.name == name,
            Account.currency == currency,
        )
    )


def _check_amount(amount: Decimal) -> None:
    # Fail closed: refuse anything that could be silently changed when stored
    if not isinstance(amount, Decimal):
        raise LedgerError(f"amount must be Decimal, got {type(amount).__name__}")
    if not amount.is_finite():
        raise LedgerError("amount must be a finite number")
    if amount == 0:
        raise LedgerError("amount must not be 0")
    if amount.as_tuple().exponent < -AMOUNT_PLACES:
        # The DB would round it, and the rounded entries might no longer sum to 0
        raise LedgerError(f"amount has more than {AMOUNT_PLACES} decimal places")


async def post_entries(
    session: AsyncSession,
    transfer_id: uuid.UUID,
    entries: list[tuple[Account, Decimal]],
) -> None:
    """Write a balanced group of entries. The caller commits, so the entries can share one
    transaction with other changes (e.g. marking the transfer SETTLED)."""
    if len(entries) < 2:
        raise LedgerError("need at least 2 entries")

    totals: dict[str, Decimal] = defaultdict(Decimal)
    for account, amount in entries:
        if account.id is None:
            raise LedgerError("account is not saved yet")
        _check_amount(amount)
        totals[account.currency] += amount

    unbalanced = {cur: total for cur, total in totals.items() if total != 0}
    if unbalanced:
        raise LedgerImbalance(f"entries don't sum to 0: {unbalanced}")

    # One INSERT statement: either every entry is written or none is
    await session.execute(
        insert(LedgerEntry),
        [
            {
                "transfer_id": transfer_id,
                "account_id": account.id,
                "amount": amount,
                "currency": account.currency,
            }
            for account, amount in entries
        ],
    )


async def get_balance(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Decimal]:
    """Sum of all the user's accounts, per currency. Currencies with no account are left out."""
    rows = await session.execute(
        select(Account.currency, func.coalesce(func.sum(LedgerEntry.amount), 0))
        .outerjoin(LedgerEntry, LedgerEntry.account_id == Account.id)
        .where(Account.owner_type == OwnerType.USER, Account.owner_id == user_id)
        .group_by(Account.currency)
    )
    return {currency: Decimal(total) for currency, total in rows.all()}


async def seed_user_funds(
    session: AsyncSession, user_id: uuid.UUID, amount: Decimal, currency: str = "BDT"
) -> uuid.UUID:
    """DEV/TEST ONLY: give a user fake money from house:funding. Returns the transfer_id."""
    # Allow-list, not "!= production": a typo like ENV=prod must still block this
    if settings.env not in {"dev", "test"}:
        raise PermissionError("seeding funds is only allowed in dev and test")
    if not isinstance(amount, Decimal) or amount <= 0:
        raise LedgerError("seed amount must be a positive Decimal")

    funding = await get_or_create_account(session, OwnerType.HOUSE, None, HOUSE_FUNDING, currency)
    wallet = await get_or_create_account(session, OwnerType.USER, user_id, USER_WALLET, currency)
    transfer_id = uuid.uuid4()
    await post_entries(session, transfer_id, [(funding, -amount), (wallet, amount)])
    return transfer_id
