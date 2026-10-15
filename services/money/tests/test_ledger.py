import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.ledger import (
    HOUSE_FEES,
    HOUSE_FUNDING,
    HOUSE_FX,
    USER_WALLET,
    LedgerError,
    LedgerImbalance,
    get_balance,
    get_or_create_account,
    post_entries,
    seed_user_funds,
)
from app.models import Account, LedgerEntry, OwnerType

D = Decimal


async def wallet(session, user_id, currency="BDT"):
    return await get_or_create_account(session, OwnerType.USER, user_id, USER_WALLET, currency)


async def house(session, name, currency="BDT"):
    return await get_or_create_account(session, OwnerType.HOUSE, None, name, currency)


async def entry_count(session) -> int:
    return await session.scalar(select(func.count()).select_from(LedgerEntry))


# --- Accounts ---


async def test_get_or_create_account_returns_same_account(session):
    user_id = uuid.uuid4()
    first = await wallet(session, user_id)
    second = await wallet(session, user_id)
    assert first.id == second.id


async def test_house_account_is_unique_even_with_no_owner(session):
    # owner_id is NULL for house accounts; the unique rule must still hold
    first = await house(session, HOUSE_FX)
    second = await house(session, HOUSE_FX)
    assert first.id == second.id
    count = await session.scalar(
        select(func.count())
        .select_from(Account)
        .where(Account.name == HOUSE_FX, Account.currency == "BDT")
    )
    assert count == 1


async def test_different_currency_is_different_account(session):
    user_id = uuid.uuid4()
    assert (await wallet(session, user_id, "BDT")).id != (await wallet(session, user_id, "IDR")).id


# --- post_entries ---


async def test_balanced_post_works(session):
    user_id = uuid.uuid4()
    await post_entries(
        session,
        uuid.uuid4(),
        [
            (await house(session, HOUSE_FUNDING), D("-500")),
            (await wallet(session, user_id), D("500")),
        ],
    )
    assert await get_balance(session, user_id) == {"BDT": D("500")}


async def test_handbook_conversion_example(session):
    # Convert 1000 BDT -> IDR at 135, fee 20 BDT (from docs/engineer-B-money-core.md)
    user_id = uuid.uuid4()
    await seed_user_funds(session, user_id, D("1000"))
    await post_entries(
        session,
        uuid.uuid4(),
        [
            (await wallet(session, user_id, "BDT"), D("-1000")),
            (await house(session, HOUSE_FX, "BDT"), D("980")),
            (await house(session, HOUSE_FEES, "BDT"), D("20")),
            (await house(session, HOUSE_FX, "IDR"), D("-132300")),
            (await wallet(session, user_id, "IDR"), D("132300")),
        ],
    )
    assert await get_balance(session, user_id) == {"BDT": D("0"), "IDR": D("132300")}


async def test_unbalanced_post_raises_and_writes_nothing(session):
    before = await entry_count(session)
    with pytest.raises(LedgerImbalance):
        await post_entries(
            session,
            uuid.uuid4(),
            [
                (await house(session, HOUSE_FUNDING), D("-500")),
                (await wallet(session, uuid.uuid4()), D("499.99")),
            ],
        )
    assert await entry_count(session) == before


async def test_each_currency_must_balance_on_its_own(session):
    # Total is 0, but BDT is +100 and IDR is -100: still money from nowhere
    user_id = uuid.uuid4()
    with pytest.raises(LedgerImbalance):
        await post_entries(
            session,
            uuid.uuid4(),
            [
                (await wallet(session, user_id, "BDT"), D("100")),
                (await wallet(session, user_id, "IDR"), D("-100")),
            ],
        )


async def test_single_entry_is_rejected(session):
    with pytest.raises(LedgerError, match="at least 2"):
        await post_entries(session, uuid.uuid4(), [(await wallet(session, uuid.uuid4()), D("1"))])


async def test_zero_amount_is_rejected(session):
    a, b = await house(session, HOUSE_FUNDING), await wallet(session, uuid.uuid4())
    with pytest.raises(LedgerError, match="not be 0"):
        await post_entries(session, uuid.uuid4(), [(a, D("0")), (b, D("0"))])


async def test_float_amount_is_rejected(session):
    a, b = await house(session, HOUSE_FUNDING), await wallet(session, uuid.uuid4())
    with pytest.raises(LedgerError, match="Decimal"):
        await post_entries(session, uuid.uuid4(), [(a, -0.1), (b, 0.1)])


async def test_too_many_decimal_places_is_rejected(session):
    # The DB keeps 4 places; storing 0.00005 would round it and break the balance
    a, b = await house(session, HOUSE_FUNDING), await wallet(session, uuid.uuid4())
    with pytest.raises(LedgerError, match="decimal places"):
        await post_entries(session, uuid.uuid4(), [(a, D("-0.00005")), (b, D("0.00005"))])


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity")])
async def test_non_finite_amount_is_rejected(session, bad):
    a, b = await house(session, HOUSE_FUNDING), await wallet(session, uuid.uuid4())
    with pytest.raises(LedgerError, match="finite"):
        await post_entries(session, uuid.uuid4(), [(a, -bad), (b, bad)])


# --- get_balance ---


async def test_balance_math_and_precision(session):
    user_id = uuid.uuid4()
    funding = await house(session, HOUSE_FUNDING)
    me = await wallet(session, user_id)
    # 0.1 + 0.2 is the classic float bug (0.30000000000000004); Decimal must give exactly 0.3
    for amount in [D("0.1"), D("0.2")]:
        await post_entries(session, uuid.uuid4(), [(funding, -amount), (me, amount)])
    await post_entries(session, uuid.uuid4(), [(me, D("-0.0001")), (funding, D("0.0001"))])
    assert await get_balance(session, user_id) == {"BDT": D("0.2999")}


async def test_balance_of_unknown_user_is_empty(session):
    assert await get_balance(session, uuid.uuid4()) == {}


async def test_balance_only_counts_this_user(session):
    alice, bob = uuid.uuid4(), uuid.uuid4()
    await seed_user_funds(session, alice, D("100"))
    await seed_user_funds(session, bob, D("7"))
    assert await get_balance(session, alice) == {"BDT": D("100")}
    assert await get_balance(session, bob) == {"BDT": D("7")}


async def test_house_accounts_never_show_in_user_balance(session):
    user_id = uuid.uuid4()
    await seed_user_funds(session, user_id, D("100"))
    balance = await get_balance(session, user_id)
    assert list(balance) == ["BDT"] and balance["BDT"] == D("100")


async def test_balance_is_decimal(session):
    user_id = uuid.uuid4()
    await seed_user_funds(session, user_id, D("12.34"))
    assert isinstance((await get_balance(session, user_id))["BDT"], Decimal)


# --- seed_user_funds ---


async def test_seed_credits_user_from_house_funding(session):
    user_id = uuid.uuid4()
    funding_before = await _account_total(session, await house(session, HOUSE_FUNDING))
    await seed_user_funds(session, user_id, D("250"))
    assert await get_balance(session, user_id) == {"BDT": D("250")}
    funding_after = await _account_total(session, await house(session, HOUSE_FUNDING))
    assert funding_after - funding_before == D("-250")


@pytest.mark.parametrize("env", ["production", "prod", "staging", ""])
async def test_seed_is_blocked_outside_dev_and_test(session, monkeypatch, env):
    monkeypatch.setattr(settings, "env", env)
    user_id = uuid.uuid4()
    before = await entry_count(session)
    with pytest.raises(PermissionError):
        await seed_user_funds(session, user_id, D("100"))
    assert await entry_count(session) == before
    assert await get_balance(session, user_id) == {}


@pytest.mark.parametrize("bad", [D("0"), D("-5"), 5.0])
async def test_seed_rejects_bad_amounts(session, bad):
    with pytest.raises(LedgerError):
        await seed_user_funds(session, uuid.uuid4(), bad)


async def _account_total(session, account) -> Decimal:
    total = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account_id == account.id
        )
    )
    return Decimal(total)
