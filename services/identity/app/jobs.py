"""Background clean-up. Run with: python -m app.jobs (every 10 minutes)."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.db import SessionLocal
from app.logs import setup_logging
from app.models import Approval, ApprovalState, Document, LinkToken, UploadLink
from app.storage import storage

log = logging.getLogger(__name__)

EVERY_SECONDS = 600


async def purge(session: AsyncSession, now: datetime | None = None) -> None:
    # now can be set to a later time, e.g. the e2e demo purges "as if 8 days later"
    now = now or datetime.now(UTC)

    # 1. Documents past delete_after: delete the file AND destroy its key
    expired_docs = await session.execute(
        select(Document.id, Document.storage_key, Document.doc_type).where(
            Document.deleted_at.is_(None), Document.delete_after <= now
        )
    )
    for doc_id, storage_key, doc_type in expired_docs.all():
        await asyncio.to_thread(storage.delete, storage_key)
        await session.execute(
            update(Document).where(Document.id == doc_id).values(wrapped_key=None, deleted_at=now)
        )
        await audit(session, "system", "document.purged", str(doc_id), {"doc_type": doc_type})
        await session.commit()  # one document at a time: a crash never loses track

    # 2. Expired one-time links can go; pending approvals past expiry become expired
    await session.execute(delete(LinkToken).where(LinkToken.expires_at <= now))
    await session.execute(delete(UploadLink).where(UploadLink.expires_at <= now))
    await session.execute(
        update(Approval)
        .where(Approval.state == ApprovalState.PENDING, Approval.expires_at <= now)
        .values(state=ApprovalState.EXPIRED)
    )
    await session.commit()


async def purge_once(now: datetime | None = None) -> None:
    async with SessionLocal() as session:
        await purge(session, now)


async def main() -> None:
    while True:
        try:
            await purge_once()
        except Exception:
            log.exception("purge failed, will try again")  # one failure never stops the loop
        await asyncio.sleep(EVERY_SECONDS)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
