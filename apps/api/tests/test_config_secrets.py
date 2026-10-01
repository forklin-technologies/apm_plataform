"""Regression: a startup failure must never print the database password.

Settings validation errors end up in the startup log. DATABASE_URL and DATABASE_ADMIN_URL carry
passwords, so none of the failure paths below may echo any part of them. Every case runs against
both variables, at two levels: in process (message, repr and chained traceback) and in a real
subprocess (what `docker logs` would show).
"""

import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import AdminSettings, Settings

PASSWORD = "SECRETPW123"  # noqa: S105  (fake password used only as a leak canary)
# Unescaped "@": SQLAlchemy reads the password as "Wq8Zk" and the host as "3Xp9Lm@db".
PASSWORD_WITH_AT = "Wq8Zk@3Xp9Lm"  # noqa: S105  (fake password used only as a leak canary)
# A valid value for the variable that is NOT under test (and a password to ignore in its output).
OTHER_APP_URL = "postgresql+psycopg://apm_app:okAppPw0@db:5432/apm"
OTHER_ADMIN_URL = "postgresql+psycopg://apm:okAdminPw0@db:5432/apm"
API_DIR = Path(__file__).resolve().parents[1]
FRAGMENT_LENGTH = 5

DRIVER_MESSAGE = "must start with postgresql+psycopg://"
HINT_MESSAGE = "percent-encoded in the password"


def leaked_fragments(text: str, secret: str) -> list[str]:
    """Pieces of the secret found in text.

    Pydantic truncates long values in the middle (`'postgre...ECRETPW123@db...'`), so a
    partial leak must count too, not only the full password.
    """
    windows = (secret[i : i + FRAGMENT_LENGTH] for i in range(len(secret) - FRAGMENT_LENGTH + 1))
    return [window for window in windows if window in text]


# (id, ENV or None for "unset", failing URL, secret that must not leak, text the error must carry)
URL_CASES: list[tuple[str, str | None, str, str, str]] = [
    (
        "env-missing",
        None,
        f"postgresql+psycopg://apm:{PASSWORD}@db:5432/apm",
        PASSWORD,
        "Field required",
    ),
    (
        "env-unknown",
        "staging",
        f"postgresql+psycopg://apm:{PASSWORD}@db:5432/apm",
        PASSWORD,
        "Input should be",
    ),
    (
        "driver-without-psycopg",
        "production",
        f"postgresql://apm:{PASSWORD}@db:5432/apm",
        PASSWORD,
        DRIVER_MESSAGE,
    ),
    # "postgresql+psycopg:" without "//" must still be rejected as a driver/prefix problem.
    (
        "driver-missing-slashes",
        "production",
        f"postgresql+psycopg:apm:{PASSWORD}@db:5432/apm",
        PASSWORD,
        DRIVER_MESSAGE,
    ),
    (
        "invalid-port",
        "production",
        f"postgresql+psycopg://apm:{PASSWORD}@db:abc/apm",
        PASSWORD,
        HINT_MESSAGE,
    ),
    # No "@": SQLAlchemy reads the password as a port and would quote it in its error.
    (
        "password-read-as-port",
        "production",
        f"postgresql+psycopg://apm:{PASSWORD}",
        PASSWORD,
        HINT_MESSAGE,
    ),
    # The URL parses and the app would start, but the driver's DNS error would print the host.
    (
        "unescaped-at-in-password",
        "production",
        f"postgresql+psycopg://apm:{PASSWORD_WITH_AT}@db:5432/apm",
        PASSWORD_WITH_AT,
        HINT_MESSAGE,
    ),
    # "@" then "/": the rest of the password is read as the database ("word9@db:5432/apm").
    (
        "unescaped-at-then-slash",
        "production",
        "postgresql+psycopg://apm:Pa@ss/word9@db:5432/apm",
        "Pa@ss/word9",
        HINT_MESSAGE,
    ),
    # "@" then "?": the real "@" lands in the query, which SQLAlchemy drops, so the tail of the
    # password ("zQ7mKt") would be read as the host and printed by the DNS error.
    (
        "unescaped-at-then-question-mark",
        "production",
        "postgresql+psycopg://apm:Pa@zQ7mKt?x9@db:5432/apm",
        "Pa@zQ7mKt?x9",
        HINT_MESSAGE,
    ),
    # "@" then "[...]": SQLAlchemy reads "[PWDTOKEN]" as an IPv6 host and silently ignores the
    # rest of the string, so the host would be "PWDTOKEN" and the DNS error would print it.
    (
        "unescaped-at-then-brackets",
        "production",
        "postgresql+psycopg://apm:@[PWDTOKEN]@db:5432/apm",
        "@[PWDTOKEN]",
        HINT_MESSAGE,
    ),
    (
        "whitespace-in-host",
        "production",
        f"postgresql+psycopg://apm:{PASSWORD}@my host:5432/apm",
        PASSWORD,
        HINT_MESSAGE,
    ),
]

APP_VAR = "DATABASE_URL"
ADMIN_VAR = "DATABASE_ADMIN_URL"
FAILING_CONFIGS: list[Any] = []
# "api" = Settings (read by the API process); "admin" = AdminSettings (Alembic, seed, tests).
for _variable, _kind in ((APP_VAR, "api"), (ADMIN_VAR, "admin")):
    for _id, _env, _url, _secret, _expected in URL_CASES:
        FAILING_CONFIGS.append(
            pytest.param(_variable, _kind, _env, _url, _secret, _expected, id=f"{_kind}-{_id}")
        )
# Role checks only exist for the pair of URLs read by the admin tools. The password is in the URL
# that must be rejected, and must not leak either.
FAILING_CONFIGS += [
    pytest.param(
        APP_VAR,
        "admin",
        "development",
        f"postgresql+psycopg://apm:{PASSWORD}@db:5432/apm",
        PASSWORD,
        "DATABASE_URL must connect as the apm_app role",
        id="admin-tools-app-url-wrong-role",
    ),
    pytest.param(
        ADMIN_VAR,
        "admin",
        "development",
        f"postgresql+psycopg://apm_app:{PASSWORD}@db:5432/apm",
        PASSWORD,
        "DATABASE_ADMIN_URL must not connect as the apm_app role",
        id="admin-tools-admin-url-is-app-role",
    ),
]
PARAMS = ("variable", "kind", "env", "url", "secret", "expected_message")


def _settings_env(variable: str, env: str | None, url: str) -> dict[str, str]:
    values = {"DATABASE_URL": OTHER_APP_URL, "DATABASE_ADMIN_URL": OTHER_ADMIN_URL}
    values[variable] = url
    if env is not None:
        values["ENV"] = env
    return values


def _settings_class(kind: str) -> type[Settings]:
    return Settings if kind == "api" else AdminSettings


@pytest.mark.parametrize(PARAMS, FAILING_CONFIGS)
def test_validation_error_does_not_leak_password(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    kind: str,
    env: str | None,
    url: str,
    secret: str,
    expected_message: str,
) -> None:
    monkeypatch.delenv("ENV", raising=False)
    for name, value in _settings_env(variable, env, url).items():
        monkeypatch.setenv(name, value)
    settings_class = _settings_class(kind)

    with pytest.raises(ValidationError) as exc_info:
        settings_class(_env_file=None)  # type: ignore[call-arg]

    # Everything a log handler could print: message, repr and the full chained traceback.
    printed = "\n".join(
        [
            str(exc_info.value),
            repr(exc_info.value),
            "".join(traceback.format_exception(exc_info.value)),
        ]
    )
    assert expected_message in str(exc_info.value)
    assert leaked_fragments(printed, secret) == []


@pytest.mark.parametrize(PARAMS, FAILING_CONFIGS)
def test_startup_log_does_not_leak_password(
    tmp_path: Path,
    variable: str,
    kind: str,
    env: str | None,
    url: str,
    secret: str,
    expected_message: str,
) -> None:
    """Same check against a real process, which is what ends up in `docker logs`."""
    process_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(API_DIR)}
    process_env.update(_settings_env(variable, env, url))
    code = (
        "from app.main import create_app; create_app()"
        if kind == "api"
        else "from app.core.config import get_admin_settings; get_admin_settings()"
    )

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=tmp_path,  # empty directory: no .env file can fill in what the case leaves out
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode != 0, "startup should have failed"
    assert "ValidationError" in result.stderr
    assert expected_message in result.stderr
    assert leaked_fragments(result.stdout + result.stderr, secret) == []
