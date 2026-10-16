"""Rules the database itself enforces, so a bug in app code can't break them."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import Account, LedgerEntry, Transfer


async def make_account(conn) -> uuid.UUID:
    account_id = uuid.uuid4()
    await conn.execute(
        insert(Account).values(
            id=account_id, owner_type="user", owner_id=uuid.uuid4(), name="wallet", currency="BDT"
        )
    )
    return account_id


async def make_ledger_entry(conn, amount: Decimal = Decimal("100.0000")) -> uuid.UUID:
    entry_id = uuid.uuid4()
    await conn.execute(
        insert(LedgerEntry).values(
            id=entry_id,
            transfer_id=uuid.uuid4(),
            account_id=await make_account(conn),
            amount=amount,
            currency="BDT",
        )
    )
    return entry_id


def transfer_row(**overrides) -> dict:
    row = {
        "user_id": uuid.uuid4(),
        "from_currency": "BDT",
        "to_currency": "USD",
        "amount": Decimal("500.0000"),
        "idempotency_key": str(uuid.uuid4()),
    }
    return row | overrides


# --- Ledger is insert-only ---


async def test_ledger_insert_works(conn):
    entry_id = await make_ledger_entry(conn)
    amount = await conn.scalar(select(LedgerEntry.amount).where(LedgerEntry.id == entry_id))
    assert amount == Decimal("100.0000")


async def test_ledger_update_is_blocked(conn):
    entry_id = await make_ledger_entry(conn)
    with pytest.raises(DBAPIError, match="append-only"):
        await conn.execute(
            update(LedgerEntry).where(LedgerEntry.id == entry_id).values(amount=Decimal("1"))
        )


async def test_ledger_delete_is_blocked(conn):
    entry_id = await make_ledger_entry(conn)
    with pytest.raises(DBAPIError, match="append-only"):
        await conn.execute(delete(LedgerEntry).where(LedgerEntry.id == entry_id))


async def test_ledger_truncate_is_blocked(conn):
    with pytest.raises(DBAPIError, match="append-only"):
        await conn.execute(text("TRUNCATE ledger_entries CASCADE"))


async def test_ledger_zero_amount_is_rejected(conn):
    with pytest.raises(IntegrityError, match="nonzero_amount"):
        await make_ledger_entry(conn, amount=Decimal("0"))


# --- Transfers ---


async def test_transfer_duplicate_idempotency_key_is_rejected(conn):
    await conn.execute(insert(Transfer).values(transfer_row(idempotency_key="same-key")))
    with pytest.raises(IntegrityError, match="uq_transfers_idempotency_key"):
        await conn.execute(insert(Transfer).values(transfer_row(idempotency_key="same-key")))


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-5")])
async def test_transfer_amount_must_be_positive(conn, amount):
    with pytest.raises(IntegrityError, match="positive_amount"):
        await conn.execute(insert(Transfer).values(transfer_row(amount=amount)))


async def test_transfer_unknown_state_is_rejected(conn):
    # Raw SQL, because the Python Enum would refuse the bad value before it reaches the DB
    with pytest.raises(IntegrityError, match="transferstate"):
        await conn.execute(
            text(
                "INSERT INTO transfers (id, user_id, kind, from_currency, to_currency, amount,"
                " state, idempotency_key) VALUES (gen_random_uuid(), gen_random_uuid(),"
                " 'conversion', 'BDT', 'USD', 10, 'HACKED', 'k1')"
            )
        )


async def test_transfer_defaults_to_created_state(conn):
    await conn.execute(insert(Transfer).values(transfer_row(idempotency_key="k-default")))
    state = await conn.scalar(select(Transfer.state).where(Transfer.idempotency_key == "k-default"))
    assert state == "CREATED"


async def test_money_comes_back_as_decimal_not_float(conn):
    await conn.execute(
        insert(Transfer).values(transfer_row(amount=Decimal("0.1"), idempotency_key="k-dec"))
    )
    amount = await conn.scalar(select(Transfer.amount).where(Transfer.idempotency_key == "k-dec"))
    assert isinstance(amount, Decimal)
    assert amount == Decimal("0.1000")


async def test_reconcile_queries_have_partial_indexes(conn):
    # Reconcile looks for the few SUBMITTED / APPROVED rows every 5 minutes: no full scans
    rows = await conn.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'transfers'")
    )
    names = set(rows.scalars())
    assert {"ix_transfers_submitted_updated_at", "ix_transfers_approved_updated_at"} <= names
