import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit, verify_chain
from app.models import AuditEvent


@pytest.mark.asyncio
async def test_audit_chain_integrity(db_session: AsyncSession):
    # Empty chain is valid
    assert await verify_chain(db_session) is True

    # Add multiple audit records
    await audit(
        db_session,
        actor="system",
        action="user.created",
        target="user-uuid-1",
        details={"note": "first user"},
    )
    await audit(
        db_session,
        actor="system",
        action="passkey.registered",
        target="user-uuid-1",
        details={"device": "TouchID"},
    )
    await audit(
        db_session,
        actor="internal:messaging",
        action="link_token.created",
        target="user-uuid-2",
        details={"ttl": 600},
    )
    await db_session.commit()

    # Chain should verify
    assert await verify_chain(db_session) is True


@pytest.mark.asyncio
async def test_audit_tampering_detected(db_session: AsyncSession):
    await audit(
        db_session,
        actor="admin",
        action="policy.changed",
        target="target-1",
        details={"rule": "max_transfer"},
    )
    await audit(
        db_session,
        actor="admin",
        action="user.unfrozen",
        target="target-1",
        details={},
    )
    await db_session.commit()

    assert await verify_chain(db_session) is True

    # Tamper with the first event's details
    stmt = select(AuditEvent).where(AuditEvent.id == 1)
    res = await db_session.execute(stmt)
    event1 = res.scalar_one()
    event1.details = {"rule": "tampered_value"}
    await db_session.commit()

    # Tampered chain must fail verification!
    assert await verify_chain(db_session) is False
