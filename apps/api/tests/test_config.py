from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import Settings, get_settings

VALID_URL = "postgresql+psycopg://user:s3cret@db:5432/apm"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()


def test_loads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DATABASE_URL", VALID_URL)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.env == "development"
    assert settings.database_url.get_secret_value() == VALID_URL
    assert settings.is_production is False


def test_fails_fast_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_URL)

    with pytest.raises(ValidationError, match="env"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_fails_fast_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_rejects_unknown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", VALID_URL)

    with pytest.raises(ValidationError, match="env"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_rejects_database_url_without_psycopg_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@db/apm")

    with pytest.raises(ValidationError, match="postgresql\\+psycopg"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_database_url_is_not_leaked_by_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DATABASE_URL", VALID_URL)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert "s3cret" not in repr(settings)
    assert "s3cret" not in str(settings)


def test_get_settings_fails_fast_when_environment_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Run from an empty directory so no .env file is picked up.
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError):
        get_settings()


def test_accepts_percent_encoded_special_characters_in_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://apm:Wq8Zk%403Xp9Lm@db:5432/apm")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    password = make_url(settings.database_url.get_secret_value()).password
    assert password == "Wq8Zk@3Xp9Lm"  # noqa: S105  (fake password)
