from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Reads .env. A missing required value crashes at startup and names the field;
    # hide_input_in_errors: that error never prints the other settings' values.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    env: str  # "dev", "test" or "production". Required: no silent default
    database_url: str
    redis_url: str
    internal_api_key: SecretStr  # shared team secret, checked on every /internal call
    money_url: str

    # Public pages and passkeys. RP_ID is the domain only (no https://, no port).
    public_base_url: str
    rp_id: str
    rp_name: str = "Student Money Agent"

    # Fernet keys. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: SecretStr  # encrypts wa_id
    vault_master_key: SecretStr  # wraps each document's own key
    session_secret: SecretStr  # signs the short upload session cookie

    # Documents: "local" (folder vault_local/, dev only) or "s3" (e.g. Cloudflare R2)
    storage_backend: str = "local"
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")

    # true = this API also runs the background jobs (see app/background.py). Keep it false
    # when the jobs run as their own process, or every job runs twice.
    run_jobs_in_api: bool = False

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @model_validator(mode="after")
    def _safe_in_production(self):
        # Refuse to start with settings that are only OK on a laptop (fail closed)
        if self.is_production and not self.public_base_url.startswith("https://"):
            raise ValueError("PUBLIC_BASE_URL must start with https:// in production")
        if self.is_production and self.storage_backend != "s3":
            raise ValueError("STORAGE_BACKEND must be s3 in production (vault_local is dev only)")
        return self


settings = Settings()
