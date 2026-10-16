import pytest
from pydantic import ValidationError

from app.config import Settings


def test_missing_setting_error_hides_secret_values(monkeypatch):
    # Only one short secret left in the env, so pydantic can't cut it out of the message
    for name in ["DATABASE_URL", "REDIS_URL", "IDENTITY_URL", "ENV"]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("INTERNAL_API_KEY", "s3cr3t")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    text = str(error.value)  # what a crash traceback prints
    assert "database_url" in text  # still names the missing field
    assert "s3cr3t" not in text
