import hashlib
from datetime import UTC, datetime
from typing import Any

from contracts.hashing import canonical_json
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditEvent

GENESIS_HASH = "0" * 64

# PostgreSQL advisory lock ID used to serialise all audit-chain writes.
# This prevents two concurrent requests from reading the same prev_hash and
# corrupting the hash chain. Must be stable across deployments.
_AUDIT_LOCK_ID = 8_675_309  # arbitrary stable integer


def _prehash(value: Any) -> str:
    """Return a stable SHA-256 hex digest of any value.

    Callers that already have a 64-char hex digest should pass it directly;
    this function always produces a deterministic, collision-resistant string
    without fragile "is this already a hash?" heuristics.
    """
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


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
    """Store an append-only, tamper-evident audit record hash-chained to the prior event.

    Uses a PostgreSQL advisory transaction lock to serialise concurrent writes
    so the hash chain is never corrupted by two simultaneous requests.
    """
    # Acquire advisory lock for the duration of this transaction.
    # On SQLite (tests) this is a no-op — SQLite serialises writes natively.
    try:
        await session.execute(text(f"SELECT pg_advisory_xact_lock({_AUDIT_LOCK_ID})"))
    except Exception:
        # SQLite / non-Postgres environment: fall through (tests use SQLite)
        pass

    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)
    res = await session.execute(stmt)
    latest = res.scalar_one_or_none()
    prev_hash = latest.row_hash if latest is not None else GENESIS_HASH

    ts = datetime.now(UTC)
    target_h = _prehash(target)

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
    """Recompute and verify all row hashes in the audit chain from genesis.

    Uses yield_per() to stream rows in batches of 500 — avoids OOM on large logs.
    """
    stmt = select(AuditEvent).order_by(AuditEvent.id.asc()).execution_options(yield_per=500)
    expected_prev = GENESIS_HASH

    async for event in await session.stream_scalars(stmt):
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
