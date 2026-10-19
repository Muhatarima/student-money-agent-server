import pytest
from pydantic import ValidationError

from app.config import Settings


def test_missing_setting_error_hides_secret_values(monkeypatch):
    # Only one short secret left in the env, so pydantic can't cut it out of the message
    for name in ["WA_VERIFY_TOKEN", "REDIS_URL", "PHONE_HASH_KEY", "INTERNAL_API_KEY"]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("WA_APP_SECRET", "s3cr3t")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    text = str(error.value)  # what a crash traceback prints
    assert "wa_verify_token" in text  # still names the missing field
    assert "s3cr3t" not in text


def test_unknown_llm_provider_fails_at_startup(monkeypatch):
    # A typo must stop the service, not silently pick some other AI
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert "llm_provider" in str(error.value)
