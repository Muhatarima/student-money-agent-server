import os

# Fake settings so tests never need a real .env. Set before any app module is imported.
os.environ["WA_VERIFY_TOKEN"] = "test-verify-token"
os.environ["WA_APP_SECRET"] = "test-app-secret"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["PHONE_HASH_KEY"] = "test-phone-hash-key"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["MONEY_URL"] = "http://money.test"
os.environ["IDENTITY_URL"] = "http://identity.test"
# Never use a real AI key from .env: tests must not call a real AI
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""

import fakeredis  # noqa: E402
import pytest  # noqa: E402

from app import redis_client  # noqa: E402

# Swap the real client for an in-memory fake BEFORE main/worker import it
redis_client.redis = fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture(autouse=True)
async def redis():
    await redis_client.redis.flushall()  # every test starts with empty Redis
    return redis_client.redis
