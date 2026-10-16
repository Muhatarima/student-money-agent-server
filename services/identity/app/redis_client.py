import redis.asyncio as aioredis

from .config import settings

# Single shared async Redis client (protocol=2 required for Upstash compatibility).
redis_client: aioredis.Redis = aioredis.from_url(
    settings.REDIS_URL,
    protocol=2,
    decode_responses=True,
)
