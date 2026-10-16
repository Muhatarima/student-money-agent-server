import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .audit import audit
from .db import AsyncSessionLocal
from .models import Approval, Document, LinkToken
from .storage import get_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("identity.jobs")


async def purge_expired_records(db: AsyncSession) -> dict[str, int]:
    """Purge expired documents, tokens, and approvals.

    Deletes document bytes from storage, zeros out the wrapped_key, and records audit trails.
    """
    now = datetime.now(UTC)
    storage = get_storage()

    # 1. Purge documents past delete_after
    stmt = select(Document).where(Document.delete_after <= now, Document.deleted_at.is_(None))
    res = await db.execute(stmt)
    expired_docs = res.scalars().all()

    purged_doc_count = 0
    for doc in expired_docs:
        try:
            await storage.delete(doc.storage_key)
        except Exception as exc:
            logger.warning("Failed to delete storage key %s: %s", doc.storage_key, exc)

        doc.wrapped_key = None
        doc.deleted_at = now
        purged_doc_count += 1

        await audit(
            db,
            actor="system:purge_job",
            action="document.purged",
            target=doc.user_id,
            details={"doc_id": str(doc.id), "doc_type": doc.doc_type},
        )

    # 2. Expire pending approvals past their expiration time
    app_stmt = (
        update(Approval)
        .where(Approval.expires_at <= now, Approval.state == "pending")
        .values(state="expired")
    )
    app_res = await db.execute(app_stmt)
    expired_approvals_count = app_res.rowcount

    # 3. Clean up expired link tokens
    tok_stmt = (
        update(LinkToken)
        .where(LinkToken.expires_at <= now, LinkToken.used_at.is_(None))
        .values(used_at=now)
    )
    tok_res = await db.execute(tok_stmt)
    expired_tokens_count = tok_res.rowcount

    await db.commit()

    return {
        "purged_documents": purged_doc_count,
        "expired_approvals": expired_approvals_count,
        "expired_tokens": expired_tokens_count,
    }


async def run_worker() -> None:
    """Continuous background worker executing the purge cycle every 10 minutes."""
    logger.info("Starting identity background jobs worker (purge cycle: 10m)...")
    while True:
        try:
            async with AsyncSessionLocal() as session:
                counts = await purge_expired_records(session)
                if any(counts.values()):
                    logger.info("Purge cycle completed: %s", counts)
        except Exception as exc:
            logger.error("Error during purge cycle: %s", exc)

        await asyncio.sleep(600)


if __name__ == "__main__":
    asyncio.run(run_worker())
