from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    DATABASE_URL: str

    # Redis
    REDIS_URL: str

    # Shared secrets
    INTERNAL_API_KEY: str
    PHONE_HASH_KEY: str

    # Service URLs
    MONEY_URL: str = "http://localhost:8002"

    # WebAuthn
    PUBLIC_BASE_URL: str
    RP_ID: str
    RP_NAME: str = "Student Money Agent"

    # Encryption keys
    FERNET_KEY: str  # Fernet key for wa_id encryption
    VAULT_MASTER_KEY: str  # Fernet key for document key wrapping

    # Session signing
    SESSION_SECRET: str

    # Document storage
    STORAGE_BACKEND: str = "local"  # "local" | "s3"
    S3_ENDPOINT: str = ""
    S3_BUCKET: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""

    # Environment
    ENV: str = "dev"

    @field_validator("PUBLIC_BASE_URL")
    @classmethod
    def https_in_production(cls, v: str, info) -> str:
        # We cannot access other fields here easily at class level;
        # the startup check in main.py enforces this at runtime.
        return v.rstrip("/")


settings = Settings()
