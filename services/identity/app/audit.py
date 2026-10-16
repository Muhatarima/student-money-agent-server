import hashlib
from datetime import UTC, datetime
from typing import Any

from contracts.hashing import canonical_json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditEvent

GENESIS_HASH = "0" * 64


def hash_target(target: Any) -> str:
    """Hash the target to ensure no plaintext PII is stored."""
    if isinstance(target, str) and len(target) == 64:
        try:
            int(target, 16)
            return target
        except ValueError:
            pass
    return hashlib.sha256(str(target).encode("utf-8")).hexdigest()


def format_ts(ts: datetime) -> str:
    """Format datetime as UTC ISO string consistently across SQLite and Postgres."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.isoformat()


async def audit(
    session: AsyncSession,
    actor: str,
    action: str,
    target: Any,
    details: dict[str, Any],
) -> AuditEvent:
    """Store an append-only, tamper-evident audit record hash-chained to the prior event."""
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)
    res = await session.execute(stmt)
    latest = res.scalar_one_or_none()
    prev_hash = latest.row_hash if latest is not None else GENESIS_HASH

    ts = datetime.now(UTC)
    target_h = hash_target(target)

    fields = {
        "ts": format_ts(ts),
        "actor": actor,
        "action": action,
        "target_hash": target_h,
        "details": details,
    }
    row_hash = hashlib.sha256((prev_hash + canonical_json(fields)).encode("utf-8")).hexdigest()

    event = AuditEvent(
        ts=ts,
        actor=actor,
        action=action,
        target_hash=target_h,
        details=details,
        prev_hash=prev_hash,
        row_hash=row_hash,
    )
    session.add(event)
    await session.flush()
    return event


async def verify_chain(session: AsyncSession) -> bool:
    """Recompute and verify all row hashes in the audit chain from genesis."""
    stmt = select(AuditEvent).order_by(AuditEvent.id.asc())
    res = await session.execute(stmt)
    events = res.scalars().all()

    expected_prev = GENESIS_HASH
    for event in events:
        if event.prev_hash != expected_prev:
            return False

        fields = {
            "ts": format_ts(event.ts),
            "actor": event.actor,
            "action": event.action,
            "target_hash": event.target_hash,
            "details": event.details,
        }
        calculated_hash = hashlib.sha256(
            (expected_prev + canonical_json(fields)).encode("utf-8")
        ).hexdigest()

        if event.row_hash != calculated_hash:
            return False

        expected_prev = event.row_hash

    return True
