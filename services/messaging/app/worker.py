import asyncio
import json
import logging

from redis.exceptions import ConnectionError, TimeoutError

from app import handlers
from app.logs import setup_logging
from app.queue import DEAD, INCOMING, PROCESSING
from app.redis_client import redis as r
from app.security import mask_phone

log = logging.getLogger(__name__)

BLOCK_SECONDS = 20  # must stay below the client's socket_timeout (30s)
MAX_FAILURES = 3


async def handle(job: dict) -> None:
    # Never log the message text or the full phone number
    log.info("processing %s from %s", job["msg_id"], mask_phone(job["from"]))
    await handlers.handle_message(job)
    log.info("done: %s", job["msg_id"])


async def recover() -> int:
    """After a crash, jobs left in PROCESSING go back to INCOMING. Returns how many."""
    moved = 0
    # LEFT -> RIGHT keeps the original order (the oldest is processed first)
    while await r.lmove(PROCESSING, INCOMING, src="LEFT", dest="RIGHT"):
        moved += 1
    return moved


async def move(raw: str, to: str) -> None:
    # Both steps or neither, so a job is never lost or duplicated
    async with r.pipeline(transaction=True) as pipe:
        pipe.lrem(PROCESSING, 1, raw)
        pipe.lpush(to, raw)
        await pipe.execute()


async def process(raw: str) -> None:
    try:
        job = json.loads(raw)
        msg_id = job["msg_id"]
    except (ValueError, KeyError, TypeError):
        log.error("unreadable job moved to the dead queue")
        await move(raw, DEAD)
        return

    failures_key = f"queue:failures:{msg_id}"
    try:
        await handle(job)
    except Exception as e:
        failures = await r.incr(failures_key)
        await r.expire(failures_key, 86400)
        if failures >= MAX_FAILURES:
            log.error(
                "%s failed %d times, moved to the dead queue (%s)",
                msg_id,
                failures,
                type(e).__name__,
            )
            await move(raw, DEAD)
        else:
            log.warning(
                "%s failed (%s), retry %d/%d", msg_id, type(e).__name__, failures, MAX_FAILURES - 1
            )
            await move(raw, INCOMING)
        return

    await r.lrem(PROCESSING, 1, raw)  # finished -> remove from PROCESSING
    await r.delete(failures_key)


async def main() -> None:
    recovered = await recover()
    log.info("worker started (%d job(s) recovered), waiting for jobs...", recovered)
    while True:
        try:
            # Atomically move one job from INCOMING to PROCESSING
            raw = await r.blmove(
                INCOMING, PROCESSING, timeout=BLOCK_SECONDS, src="RIGHT", dest="LEFT"
            )
        except (TimeoutError, ConnectionError) as e:
            log.warning("Redis hiccup (%s), retrying in 2s...", type(e).__name__)
            await asyncio.sleep(2)
            continue

        if raw is not None:
            await process(raw)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
