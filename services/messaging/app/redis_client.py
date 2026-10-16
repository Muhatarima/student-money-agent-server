from redis.asyncio import Redis

from app.config import settings

# One shared client. protocol=2 so it works with Upstash.
# socket_timeout (30s) must be bigger than the worker's BLMOVE wait (20s).
redis = Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    protocol=2,
    socket_timeout=30,
    health_check_interval=15,
)
