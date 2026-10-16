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


# --- Database (identity_test). Fresh schema once per run, built by the real migrations ---

from collections.abc import AsyncIterator  # noqa: E402
from pathlib import Path  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import pool  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine  # noqa: E402


@pytest.fixture(scope="session")
def migrated_db() -> None:
    # Downgrade instead of TRUNCATE, because the trigger blocks TRUNCATE on audit_events
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
async def conn(migrated_db: None) -> AsyncIterator[AsyncConnection]:
    # Each test runs in its own transaction and is rolled back, so tests don't see each other
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.begin()
        yield connection
        await connection.rollback()
    await engine.dispose()


@pytest.fixture
async def session(conn: AsyncConnection) -> AsyncIterator[AsyncSession]:
    # A session inside the test's transaction: even session.commit() is undone afterwards
    async with AsyncSession(
        bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    ) as s:
        yield s


# --- HTTP client against the app, using the test's DB session ---

import httpx  # noqa: E402

from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402

KEY = {"X-Internal-Key": "test-key"}
ORIGIN = {"Origin": "http://localhost:8003"}  # what a browser on our own pages sends


@pytest.fixture
async def client(session) -> AsyncIterator[httpx.AsyncClient]:
    async def test_session():
        yield session

    app.dependency_overrides[get_session] = test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8003") as c:
        yield c
    app.dependency_overrides.clear()
