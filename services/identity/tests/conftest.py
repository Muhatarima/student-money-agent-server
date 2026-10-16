"""
Pytest configuration for the identity service test suite.

Uses aiosqlite as an in-memory async DB so tests never need a real Postgres.
Overrides DATABASE_URL and REDIS_URL via environment variables before importing
anything from the app, so settings load with safe test values.
"""

import os

from cryptography.fernet import Fernet


def _fernet_key() -> str:
    return Fernet.generate_key().decode()


# Set env vars before any app import so pydantic-settings picks them up.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["PHONE_HASH_KEY"] = "a" * 64
os.environ["MONEY_URL"] = "http://localhost:8002"
os.environ["PUBLIC_BASE_URL"] = "http://localhost:8003"
os.environ["RP_ID"] = "localhost"
os.environ["RP_NAME"] = "Test Agent"
os.environ["FERNET_KEY"] = _fernet_key()
os.environ["VAULT_MASTER_KEY"] = _fernet_key()
os.environ["SESSION_SECRET"] = "a" * 64
os.environ["STORAGE_BACKEND"] = "local"
os.environ["ENV"] = "dev"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

# ── In-memory SQLite engine for tests ─────────────────────────────────────────


@pytest.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    """HTTP test client with the DB dependency overridden."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    import fakeredis.aioredis

    import app.redis_client

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(app.redis_client, "redis_client", fake)

    for mod_name in ["main", "users", "passkeys", "events", "approvals", "vault", "jobs"]:
        try:
            mod = __import__(f"app.{mod_name}", fromlist=["redis_client"])
            if hasattr(mod, "redis_client"):
                monkeypatch.setattr(mod, "redis_client", fake)
        except ImportError:
            pass

    yield fake
