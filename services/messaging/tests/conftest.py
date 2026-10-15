import os

# Fake settings so tests never need a real .env. Set before any app module is imported.
os.environ["WA_VERIFY_TOKEN"] = "test-verify-token"
os.environ["WA_APP_SECRET"] = "test-app-secret"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"

import fakeredis  # noqa: E402
import pytest  # noqa: E402

from app import redis_client  # noqa: E402

# Swap the real client for an in-memory fake BEFORE main/worker import it
redis_client.redis = fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture(autouse=True)
async def redis():
    await redis_client.redis.flushall()  # every test starts with empty Redis
    return redis_client.redis
