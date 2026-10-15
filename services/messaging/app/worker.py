import asyncio
import json
import os

import redis.asyncio as redis
from dotenv import load_dotenv
from redis.exceptions import ConnectionError, TimeoutError

load_dotenv()

r = redis.from_url(
    os.environ["REDIS_URL"],
    decode_responses=True,
    protocol=2,
    socket_timeout=30,          # connection waits up to 30s...
    health_check_interval=15,   # ...and checks itself every 15s
)

QUEUE = "queue:incoming"
PROCESSING = "queue:processing"
BLOCK_SECONDS = 20              # ...but each job-wait is only 20s (less than 30)


async def handle(job: dict) -> None:
    sender = "****" + job["from"][-4:]
    print(f"⚙️  Processing from {sender}: {job['text']}")
    await asyncio.sleep(2)  # pretend this is slow AI work
    print(f"✅ Done: {job['msg_id']}")


async def main() -> None:
    print("👷 Worker started, waiting for jobs...")
    while True:
        try:
            # Atomically move one job from QUEUE to PROCESSING
            raw = await r.blmove(QUEUE, PROCESSING, timeout=BLOCK_SECONDS,
                                 src="RIGHT", dest="LEFT")
        except (TimeoutError, ConnectionError) as e:
            print(f"🔌 Redis hiccup ({type(e).__name__}), retrying in 2s...")
            await asyncio.sleep(2)
            continue

        if raw is None:
            continue  # no job yet, keep waiting

        job = json.loads(raw)
        try:
            await handle(job)
            await r.lrem(PROCESSING, 1, raw)  # finished -> remove from PROCESSING
        except Exception as e:
            print(f"❌ Failed, kept in processing list: {e}")


if __name__ == "__main__":
    asyncio.run(main())