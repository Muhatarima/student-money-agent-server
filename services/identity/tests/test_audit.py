import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError

from app.audit import GENESIS, audit, verify_chain
from app.models import AuditEvent


async def rows(session):
    return (await session.scalars(select(AuditEvent).order_by(AuditEvent.id))).all()


async def test_chain_verifies(session):
    for i in range(3):
        await audit(session, "system", "test.event", f"user-{i}", {"n": i})
    assert await verify_chain(session)


async def test_rows_link_to_each_other(session):
    await audit(session, "system", "a", None, {})
    await audit(session, "system", "b", None, {})
    *_, first, second = await rows(session)
    assert second.prev_hash == first.row_hash
    assert (await rows(session))[0].prev_hash == GENESIS


async def test_target_is_stored_only_as_a_hash(session):
    await audit(session, "system", "user.frozen", "8801700001234", {})
    row = (await rows(session))[-1]
    assert row.target_hash != "8801700001234" and len(row.target_hash) == 64


async def test_update_is_blocked_by_the_trigger(session):
    await audit(session, "system", "a", None, {})
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(update(AuditEvent).values(action="changed"))


async def test_delete_is_blocked_by_the_trigger(session):
    await audit(session, "system", "a", None, {})
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(delete(AuditEvent))


async def test_truncate_is_blocked_by_the_trigger(session):
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text("TRUNCATE audit_events"))


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE audit_events SET details = '{\"n\": 99}' WHERE id = :id",
        "UPDATE audit_events SET action = 'nothing.happened' WHERE id = :id",
        "DELETE FROM audit_events WHERE id = :id",
    ],
)
async def test_editing_a_row_breaks_verification(session, tamper):
    for i in range(3):
        await audit(session, "system", "test.event", None, {"n": i})
    middle = (await rows(session))[-2].id
    # Bypass the trigger (only possible for the table owner; undone when the test ends)
    await session.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only"))
    await session.execute(text(tamper), {"id": middle})
    session.expunge_all()
    assert not await verify_chain(session)
