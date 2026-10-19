import os

# Tests never touch the real "money" database. Set before any app module is imported.
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:5432/money_test"
)
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["INTERNAL_API_KEY"] = "test-key"
os.environ["IDENTITY_URL"] = "http://identity.test"
os.environ["ENV"] = "test"
os.environ["RUN_JOBS_IN_API"] = "false"  # tests start jobs themselves

from collections.abc import AsyncIterator  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import pool  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine  # noqa: E402


@pytest.fixture(scope="session")
def migrated_db() -> None:
    # Fresh schema once per test run, built by the real migrations (incl. the ledger trigger).
    # Downgrade instead of TRUNCATE, because the trigger blocks TRUNCATE on ledger_entries.
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
