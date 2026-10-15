from redis.asyncio import Redis

from app.config import settings

# protocol=2 so it also works with Upstash (see handbook)
redis = Redis.from_url(settings.redis_url, protocol=2, decode_responses=True)
