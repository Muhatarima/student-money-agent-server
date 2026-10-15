import os

# Tests never touch the real "money" database. Set before any app module is imported.
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:5432/money_test"
)
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["INTERNAL_API_KEY"] = "test-key"
os.environ["IDENTITY_URL"] = "http://identity.test"
os.environ["ENV"] = "test"
