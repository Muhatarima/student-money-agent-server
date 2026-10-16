import asyncio
import json

from redis.exceptions import ConnectionError, TimeoutError

from app import handlers
from app.queue import DEAD, INCOMING, PROCESSING
from app.redis_client import redis as r
from app.security import mask_phone

BLOCK_SECONDS = 20  # must stay below the client's socket_timeout (30s)
MAX_FAILURES = 3


async def handle(job: dict) -> None:
    # Never log the message text or the full phone number
    print(f"⚙️  Processing {job['msg_id']} from {mask_phone(job['from'])}")
    await handlers.handle_message(job)
    print(f"✅ Done: {job['msg_id']}")


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
        print("☠️  Unreadable job moved to dead queue")
        await move(raw, DEAD)
        return

    failures_key = f"queue:failures:{msg_id}"
    try:
        await handle(job)
    except Exception as e:
        failures = await r.incr(failures_key)
        await r.expire(failures_key, 86400)
        if failures >= MAX_FAILURES:
            print(f"☠️  {msg_id} failed {failures} times, moved to dead queue ({type(e).__name__})")
            await move(raw, DEAD)
        else:
            print(f"❌ {msg_id} failed ({type(e).__name__}), retry {failures}/{MAX_FAILURES - 1}")
            await move(raw, INCOMING)
        return

    await r.lrem(PROCESSING, 1, raw)  # finished -> remove from PROCESSING
    await r.delete(failures_key)


async def main() -> None:
    recovered = await recover()
    print(f"👷 Worker started ({recovered} job(s) recovered), waiting for jobs...")
    while True:
        try:
            # Atomically move one job from INCOMING to PROCESSING
            raw = await r.blmove(
                INCOMING, PROCESSING, timeout=BLOCK_SECONDS, src="RIGHT", dest="LEFT"
            )
        except (TimeoutError, ConnectionError) as e:
            print(f"🔌 Redis hiccup ({type(e).__name__}), retrying in 2s...")
            await asyncio.sleep(2)
            continue

        if raw is not None:
            await process(raw)


if __name__ == "__main__":
    asyncio.run(main())
