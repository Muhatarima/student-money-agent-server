import os

from cryptography.fernet import Fernet

# Fake settings so tests never need a real .env or the real "identity" database.
# Set before any app module is imported.
os.environ["ENV"] = "test"
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:5432/identity_test"
)
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["INTERNAL_API_KEY"] = "test-key"
os.environ["MONEY_URL"] = "http://money.test"
os.environ["PUBLIC_BASE_URL"] = "http://localhost:8003"
os.environ["RP_ID"] = "localhost"
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
os.environ["VAULT_MASTER_KEY"] = Fernet.generate_key().decode()
os.environ["SESSION_SECRET"] = "test-session-secret"

import fakeredis  # noqa: E402
import pytest  # noqa: E402

from app import redis_client  # noqa: E402

# Swap the real Redis client for an in-memory fake BEFORE other app modules import it
redis_client.redis = fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture(autouse=True)
async def redis():
    await redis_client.redis.flushall()  # every test starts with empty Redis
    return redis_client.redis
