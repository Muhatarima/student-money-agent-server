"""Tamper-evident audit log.

Each row stores row_hash = sha256(prev_hash + canonical_json(its fields)), so changing or
removing any old row breaks every hash after it. A DB trigger also blocks UPDATE/DELETE.
Never put PII in details; the target (e.g. a user id) is stored only as a hash.
"""

import hashlib
from datetime import UTC, datetime

from contracts.hashing import canonical_json
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent

GENESIS = "0" * 64  # prev_hash of the very first row
AUDIT_LOCK = 42_001  # any fixed number: one writer at a time keeps the chain straight


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _row_hash(prev_hash: str, ts: datetime, actor: str, action: str, target_hash, details):
    fields = {
        "ts": ts.isoformat(),
        "actor": actor,
        "action": action,
        "target_hash": target_hash,
        "details": details,
    }
    return _sha256(prev_hash + canonical_json(fields))


async def audit(
    session: AsyncSession, actor: str, action: str, target: str | None, details: dict
) -> None:
    """Add one row. Runs in the caller's transaction: it's saved when they commit."""
    # Without this lock, two requests at once could both chain onto the same row
    await session.execute(select(func.pg_advisory_xact_lock(AUDIT_LOCK)))
    prev_hash = await session.scalar(
        select(AuditEvent.row_hash).order_by(AuditEvent.id.desc()).limit(1)
    )
    prev_hash = prev_hash or GENESIS
    ts = datetime.now(UTC)
    target_hash = _sha256(target) if target is not None else None
    session.add(
        AuditEvent(
            ts=ts,
            actor=actor,
            action=action,
            target_hash=target_hash,
            details=details,
            prev_hash=prev_hash,
            row_hash=_row_hash(prev_hash, ts, actor, action, target_hash, details),
        )
    )
    await session.flush()


async def verify_chain(session: AsyncSession) -> bool:
    """Recompute every hash from the first row. False = something was changed or removed."""
    expected_prev = GENESIS
    rows = await session.stream_scalars(select(AuditEvent).order_by(AuditEvent.id))
    async for row in rows:
        if row.prev_hash != expected_prev:
            return False
        recomputed = _row_hash(
            row.prev_hash, row.ts, row.actor, row.action, row.target_hash, row.details
        )
        if recomputed != row.row_hash:
            return False
        expected_prev = row.row_hash
    return True
