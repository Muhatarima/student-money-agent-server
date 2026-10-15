from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Reads .env; a missing required value crashes at startup and names the field
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    wa_verify_token: SecretStr
    wa_app_secret: SecretStr
    redis_url: str


settings = Settings()
