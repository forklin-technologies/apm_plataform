"""Regression: a startup failure must never print the database password.

Settings validation errors end up in the startup log. DATABASE_URL carries the password,
so none of the failure paths below may echo any part of it.
"""

import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings

PASSWORD = "SECRETPW123"  # noqa: S105  (fake password used only as a leak canary)
# Unescaped "@": SQLAlchemy reads the password as "Wq8Zk" and the host as "3Xp9Lm@db".
PASSWORD_WITH_AT = "Wq8Zk@3Xp9Lm"  # noqa: S105  (fake password used only as a leak canary)
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


def _case(
    case_id: str, env: str | None, database_url: str, secret: str, expected_message: str
) -> Any:
    return pytest.param(env, database_url, secret, expected_message, id=case_id)


# (id, ENV or None for "unset", DATABASE_URL, secret that must not leak, text the error must carry)
FAILING_CONFIGS = [
    _case(
        "env-missing",
        None,
        f"postgresql+psycopg://apm:{PASSWORD}@db:5432/apm",
        PASSWORD,
        "Field required",
    ),
    _case(
        "env-unknown",
        "staging",
        f"postgresql+psycopg://apm:{PASSWORD}@db:5432/apm",
        PASSWORD,
        "Input should be",
    ),
    _case(
        "driver-without-psycopg",
        "production",
        f"postgresql://apm:{PASSWORD}@db:5432/apm",
        PASSWORD,
        DRIVER_MESSAGE,
    ),
    # "postgresql+psycopg:" without "//" must still be rejected as a driver/prefix problem.
    _case(
        "driver-missing-slashes",
        "production",
        f"postgresql+psycopg:apm:{PASSWORD}@db:5432/apm",
        PASSWORD,
        DRIVER_MESSAGE,
    ),
    _case(
        "invalid-port",
        "production",
        f"postgresql+psycopg://apm:{PASSWORD}@db:abc/apm",
        PASSWORD,
        HINT_MESSAGE,
    ),
    # No "@": SQLAlchemy reads the password as a port and would quote it in its error.
    _case(
        "password-read-as-port",
        "production",
        f"postgresql+psycopg://apm:{PASSWORD}",
        PASSWORD,
        HINT_MESSAGE,
    ),
    # The URL parses and the app would start, but the driver's DNS error would print the host.
    _case(
        "unescaped-at-in-password",
        "production",
        f"postgresql+psycopg://apm:{PASSWORD_WITH_AT}@db:5432/apm",
        PASSWORD_WITH_AT,
        HINT_MESSAGE,
    ),
    # "@" then "/": the rest of the password is read as the database ("word9@db:5432/apm").
    _case(
        "unescaped-at-then-slash",
        "production",
        "postgresql+psycopg://apm:Pa@ss/word9@db:5432/apm",
        "Pa@ss/word9",
        HINT_MESSAGE,
    ),
    # "@" then "?": the real "@" lands in the query, which SQLAlchemy drops, so the tail of the
    # password ("zQ7mKt") would be read as the host and printed by the DNS error.
    _case(
        "unescaped-at-then-question-mark",
        "production",
        "postgresql+psycopg://apm:Pa@zQ7mKt?x9@db:5432/apm",
        "Pa@zQ7mKt?x9",
        HINT_MESSAGE,
    ),
    _case(
        "whitespace-in-host",
        "production",
        f"postgresql+psycopg://apm:{PASSWORD}@my host:5432/apm",
        PASSWORD,
        HINT_MESSAGE,
    ),
]
PARAMS = ("env", "database_url", "secret", "expected_message")


@pytest.mark.parametrize(PARAMS, FAILING_CONFIGS)
def test_validation_error_does_not_leak_password(
    monkeypatch: pytest.MonkeyPatch,
    env: str | None,
    database_url: str,
    secret: str,
    expected_message: str,
) -> None:
    monkeypatch.delenv("ENV", raising=False)
    if env is not None:
        monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

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
    tmp_path: Path, env: str | None, database_url: str, secret: str, expected_message: str
) -> None:
    """Same check against a real process, which is what ends up in `docker logs`."""
    process_env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(API_DIR),
        "DATABASE_URL": database_url,
    }
    if env is not None:
        process_env["ENV"] = env

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "from app.main import create_app; create_app()"],
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
