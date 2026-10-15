import json

from app.redis_client import redis

INCOMING = "queue:incoming"
PROCESSING = "queue:processing"
DEAD = "queue:dead"


async def enqueue_incoming(job: dict) -> None:
    await redis.lpush(INCOMING, json.dumps(job))
