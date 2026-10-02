from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import ALLOWED_QUERY_KEYS, AdminSettings, Settings, get_settings

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


def test_accepts_a_legitimate_query_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://apm:pw@db:5432/apm?sslmode=require")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert make_url(settings.database_url.get_secret_value()).query == {"sslmode": "require"}


# --- the query string is an ALLOW-list (review N6) ---------------------------------------------

ALLOWED_KEYS = {
    "sslmode": "require",
    "sslrootcert": "/etc/ssl/certs/ca.pem",
    "connect_timeout": "5",
    "application_name": "apm-api",
}
# Every libpq option that could change who connects, where, to what, or how the session behaves.
REFUSED_KEYS = [
    "user",
    "password",
    "host",
    "hostaddr",
    "port",
    "dbname",
    "service",
    "passfile",
    "options",
    "sslpassword",
    "sslcert",
    "sslkey",
    "target_session_attrs",
    "channel_binding",
    "krbsrvname",
    "gssencmode",
    "client_encoding",
    "fallback_application_name",
    "keepalives",
    "replication",
]


def _url_with(query: str) -> str:
    return f"postgresql+psycopg://apm_app:okPw0@db:5432/apm?{query}"


def _settings_from(monkeypatch: pytest.MonkeyPatch, url: str) -> Settings:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DATABASE_URL", url)
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("key", sorted(ALLOWED_KEYS))
def test_each_allowed_query_key_is_accepted(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    settings = _settings_from(monkeypatch, _url_with(f"{key}={ALLOWED_KEYS[key]}"))

    assert make_url(settings.database_url.get_secret_value()).query == {key: ALLOWED_KEYS[key]}


def test_all_the_allowed_query_keys_together_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    query = "&".join(f"{k}={v}" for k, v in ALLOWED_KEYS.items())

    settings = _settings_from(monkeypatch, _url_with(query))

    assert dict(make_url(settings.database_url.get_secret_value()).query) == ALLOWED_KEYS


def test_the_allow_list_is_exactly_these_four() -> None:
    assert ALLOWED_QUERY_KEYS == ("sslmode", "sslrootcert", "connect_timeout", "application_name")


@pytest.mark.parametrize("variable", ["DATABASE_URL", "DATABASE_ADMIN_URL"])
@pytest.mark.parametrize(
    "variant",
    ["lower", "upper", "title"],
)
@pytest.mark.parametrize("key", REFUSED_KEYS)
def test_every_other_query_key_is_refused_in_any_case_with_a_fixed_message(
    monkeypatch: pytest.MonkeyPatch, variable: str, variant: str, key: str
) -> None:
    spelled = {"lower": key.lower(), "upper": key.upper(), "title": key.title()}[variant]
    value = "VALUE_CANARY_9"
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://apm_app:okPw0@db:5432/apm")
    monkeypatch.setenv("DATABASE_ADMIN_URL", "postgresql+psycopg://apm:okPw1@db:5432/apm")
    monkeypatch.setenv(variable, f"postgresql+psycopg://x:okPw2@db:5432/apm?{spelled}={value}")

    with pytest.raises(ValidationError) as error:
        AdminSettings(_env_file=None)  # type: ignore[call-arg]

    message = str(error.value)
    assert "may only use these query parameters" in message
    assert value not in message and spelled not in message.replace("query parameters", "")


@pytest.mark.parametrize(
    "query",
    [
        "options=-c%20row_security%3Doff",
        "options=-c%20search_path%3Dpg_temp",
        "sslmode=require&options=-c%20role%3Dapm_owner",
        "sslmode=require&sslmode=disable",
        "user=apm&user=apm_app",
        "application_name=a&application_name=b",
        "connect_timeout=1&connect_timeout=999",
    ],
)
def test_options_and_repeated_keys_are_refused(monkeypatch: pytest.MonkeyPatch, query: str) -> None:
    with pytest.raises(ValidationError, match="may only use these query parameters"):
        _settings_from(monkeypatch, _url_with(query))
