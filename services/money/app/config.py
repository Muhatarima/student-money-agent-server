from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str
    redis_url: str
    internal_api_key: str
    identity_url: str
    rate_source_url: str | None = None

    # Limits and fees (money is always Decimal, never float)
    max_per_tx_bdt: Decimal = Decimal("50000")
    max_per_day_bdt: Decimal = Decimal("100000")
    fee_percent: Decimal = Decimal("0.5")
    fee_min_bdt: Decimal = Decimal("20")

    quote_ttl_seconds: int = 60
    rate_stale_seconds: int = 120

    @property
    def is_production(self) -> bool:
        return self.env == "production"


settings = Settings()
