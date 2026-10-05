"""A6/A8: the settings of the API process: secrets never echoed, the cookie mode, the origins, and
a production that refuses to start unless it can be started safely."""

import os
import subprocess
import sys
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import DEV_ORIGINS, ApiSettings
from tests.dbsupport import API_DIR
from tests.helpers import TEST_AUTH_SECRET, UNREACHABLE_DATABASE_URL

DATABASE = "postgresql+psycopg://apm_app:CanaryDbPw9Zq@127.0.0.1:1/none"
ENVIRONMENT = (
    "ENV",
    "DATABASE_URL",
    "AUTH_SECRET",
    "COOKIE_SECURE",
    "PUBLIC_ORIGINS",
    "PUBLIC_BASE_URL",
    "EMAIL_SENDER",
    "OUTBOX_DIR",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


def build(**values: Any) -> ApiSettings:
    base: dict[str, Any] = {
        "env": "development",
        "database_url": UNREACHABLE_DATABASE_URL,
        "auth_secret": TEST_AUTH_SECRET,
    }
    return ApiSettings(_env_file=None, **{**base, **values})  # type: ignore[call-arg]


PRODUCTION = {
    "env": "production",
    "cookie_secure": True,
    "public_origins": "https://app.example.test",
}


# --- cookies ----------------------------------------------------------------------------------


def test_cookies_are_secure_by_default_and_carry_the_host_prefix() -> None:
    settings = build()

    assert settings.cookie_secure is True
    assert (settings.session_cookie_name, settings.csrf_cookie_name) == (
        "__Host-apm_session",
        "__Host-apm_csrf",
    )


def test_the_development_mode_drops_secure_and_the_prefix_together() -> None:
    settings = build(cookie_secure=False)

    assert (settings.session_cookie_name, settings.csrf_cookie_name) == ("apm_session", "apm_csrf")


@pytest.mark.parametrize("env", ["development", "test"])
def test_insecure_cookies_are_allowed_only_outside_production(env: str) -> None:
    assert build(env=env, cookie_secure=False).cookie_secure is False


def test_insecure_cookies_are_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="COOKIE_SECURE must be true"):
        build(**{**PRODUCTION, "cookie_secure": False})


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE"])
def test_the_environment_value_for_insecure_cookies_is_read_and_refused_in_production(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("COOKIE_SECURE", value)

    assert build().cookie_secure is False
    with pytest.raises(ValidationError, match="COOKIE_SECURE must be true"):
        build(env="production", public_origins="https://app.example.test")


# --- production ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"public_origins": ""}, "PUBLIC_ORIGINS must list the https origins"),
        ({"public_origins": "http://app.example.test"}, "PUBLIC_ORIGINS must list the https"),
        (
            {"public_origins": "https://a.example.test,http://b.example.test"},
            "PUBLIC_ORIGINS must list the https",
        ),
        ({}, "no e-mail sender is available for ENV=production"),
    ],
    ids=["no-origins", "http-origin", "one-http-origin", "no-real-sender"],
)
def test_production_refuses_what_is_not_safe_to_serve(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        build(**{**PRODUCTION, **changes})


def test_production_cannot_start_today_because_there_is_no_real_e_mail_sender() -> None:
    """Even with everything else right: invitations would be written to a file (with the accept
    link, so the token) on the server. That is for development only, until a real sender exists."""
    with pytest.raises(ValidationError, match="no e-mail sender is available"):
        build(**PRODUCTION)


@pytest.mark.parametrize("env", ["development", "test"])
def test_the_file_sender_is_fine_outside_production(env: str) -> None:
    assert build(env=env).email_sender == "file"


def _start(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(API_DIR)}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", "from app.main import create_app; create_app()"],
        cwd=API_DIR,
        env={**base, **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"COOKIE_SECURE": "false", "PUBLIC_ORIGINS": "https://app.example.test"},
            "COOKIE_SECURE must be true",
        ),
        (
            {"COOKIE_SECURE": "true", "PUBLIC_ORIGINS": "http://app.example.test"},
            "PUBLIC_ORIGINS must list the https",
        ),
        (
            {"COOKIE_SECURE": "true", "PUBLIC_ORIGINS": "https://app.example.test"},
            "no e-mail sender is available",
        ),
    ],
    ids=["insecure-cookies", "http-origin", "no-sender"],
)
def test_the_process_refuses_to_start_in_production_and_says_why_without_leaking(
    env: dict[str, str], message: str
) -> None:
    result = _start(
        {"ENV": "production", "DATABASE_URL": DATABASE, "AUTH_SECRET": TEST_AUTH_SECRET, **env}
    )

    assert result.returncode != 0
    assert message in result.stderr
    assert "CanaryDbPw9Zq" not in result.stdout + result.stderr
    assert TEST_AUTH_SECRET not in result.stdout + result.stderr


def test_the_process_starts_in_development_with_insecure_cookies() -> None:
    result = _start(
        {
            "ENV": "development",
            "DATABASE_URL": DATABASE,
            "AUTH_SECRET": TEST_AUTH_SECRET,
            "COOKIE_SECURE": "false",
        }
    )

    assert result.returncode == 0, result.stderr


# --- the secret ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret", ["", "short", "x" * 31, "a b c d e f g h i j k l m n o p q"[:31]]
)
def test_a_short_auth_secret_is_refused_without_echoing_it(secret: str) -> None:
    with pytest.raises(ValidationError) as error:
        build(auth_secret=secret)

    assert "at least 32 characters" in str(error.value)
    assert secret == "" or secret not in str(error.value)


def test_a_missing_auth_secret_fails_fast() -> None:
    with pytest.raises(ValidationError, match="auth_secret"):
        ApiSettings(_env_file=None, env="development", database_url=UNREACHABLE_DATABASE_URL)  # type: ignore[call-arg, arg-type]


def test_the_auth_secret_does_not_show_in_the_representations_of_the_settings() -> None:
    settings = build()

    for rendering in (repr(settings), str(settings), repr(settings.model_dump())):
        assert TEST_AUTH_SECRET not in rendering
    assert "SecretStr" in repr(settings.model_dump()) or "**********" in repr(settings.model_dump())
    assert settings.auth_secret.get_secret_value() == TEST_AUTH_SECRET


def test_the_database_password_does_not_show_either() -> None:
    settings = build(database_url=DATABASE)

    assert "CanaryDbPw9Zq" not in repr(settings) + str(settings) + repr(settings.model_dump())


# --- origins --------------------------------------------------------------------------------


def test_the_development_origins_are_the_default_outside_production() -> None:
    assert build().allowed_origins == DEV_ORIGINS
    assert build(public_origins="").allowed_origins == DEV_ORIGINS


def test_configured_origins_replace_the_defaults_and_are_normalised() -> None:
    settings = build(public_origins=" HTTPS://App.Example.test/ , http://localhost:8001 ")

    assert settings.allowed_origins == ("https://app.example.test", "http://localhost:8001")


@pytest.mark.parametrize(
    "origin",
    [
        "localhost:3000",
        "ftp://example.test",
        "https://example.test/path",
        "https://user:pw@example.test",
        "https://example.test?x=1",
        "https://example.test#frag",
        "*",
        "null",
        "https://",
    ],
)
def test_an_origin_that_is_not_an_origin_is_refused(origin: str) -> None:
    with pytest.raises(ValidationError, match="PUBLIC_ORIGINS must be a comma-separated list"):
        build(public_origins=origin)


def test_the_default_port_of_a_scheme_is_the_same_origin_as_no_port() -> None:
    """Review of 2026-10-05: `https://app.example.test:443` in PUBLIC_ORIGINS never matched the
    `Origin: https://app.example.test` a browser sends, so every login was refused."""
    settings = build(
        public_origins="https://App.Example.test:443, http://plain.example.test:80, "
        "https://other.example.test:8443, https://other.example.test:80"
    )
    assert settings.allowed_origins == (
        "https://app.example.test",
        "http://plain.example.test",
        "https://other.example.test:8443",
        "https://other.example.test:80",  # 80 is not the default port of https
    )
