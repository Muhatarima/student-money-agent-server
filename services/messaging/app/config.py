from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Reads .env; a missing required value crashes at startup and names the field
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    wa_verify_token: SecretStr
    wa_app_secret: SecretStr
    redis_url: str
    phone_hash_key: SecretStr  # shared team secret, see MANUAL_TODO.md

    # Other services (internal API). Every call sends X-Internal-Key.
    internal_api_key: SecretStr
    money_url: str
    identity_url: str

    # WhatsApp Cloud API (sending). With WA_FAKE_SEND=true nothing is sent to Meta.
    wa_fake_send: bool = False
    wa_fake_send_url: str = ""  # optional, in fake mode: also POST there (mock_server.py)
    wa_access_token: SecretStr = SecretStr("")
    wa_phone_number_id: str = ""
    wa_api_version: str = "v21.0"

    # AI agent. No key for the chosen provider = the agent is off and users get the button menu.
    llm_provider: Literal["anthropic", "gemini"] = "gemini"
    anthropic_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-opus-5-5"  # the Claude model (LLM_PROVIDER=anthropic)
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.8-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # true = this API also runs the background jobs (see app/background.py). Keep it false
    # when the jobs run as their own process, or every job runs twice.
    run_jobs_in_api: bool = False


settings = Settings()
