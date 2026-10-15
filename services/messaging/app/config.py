from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Reads .env; a missing required value crashes at startup and names the field
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    wa_verify_token: SecretStr
    wa_app_secret: SecretStr
    redis_url: str
    phone_hash_key: SecretStr  # shared team secret, see MANUAL_TODO.md

    # WhatsApp Cloud API (sending). With WA_FAKE_SEND=true nothing is sent to Meta.
    wa_fake_send: bool = False
    wa_access_token: SecretStr = SecretStr("")
    wa_phone_number_id: str = ""
    wa_api_version: str = "v21.0"


settings = Settings()
