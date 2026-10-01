"""Regression: a startup failure must never print the database password.

Settings validation errors end up in the startup log. DATABASE_URL carries the password,
so none of the failure paths below may echo any part of it.
"""

import os
import subprocess
import sys
import traceback
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

PASSWORD = "SECRETPW123"  # noqa: S105  (fake password used only as a leak canary)
API_DIR = Path(__file__).resolve().parents[1]
FRAGMENT_LENGTH = 5


def leaked_fragments(text: str) -> list[str]:
    """Pieces of the password found in text.

    Pydantic truncates long values in the middle (`'postgre...ECRETPW123@db...'`), so a
    partial leak must count too, not only the full password.
    """
    windows = (
        PASSWORD[i : i + FRAGMENT_LENGTH] for i in range(len(PASSWORD) - FRAGMENT_LENGTH + 1)
    )
    return [window for window in windows if window in text]


# (ENV or None for "unset", DATABASE_URL)
FAILING_CONFIGS = [
    pytest.param(None, f"postgresql+psycopg://apm:{PASSWORD}@db:5432/apm", id="env-missing"),
    pytest.param("staging", f"postgresql+psycopg://apm:{PASSWORD}@db:5432/apm", id="env-unknown"),
    pytest.param(
        "production", f"postgresql://apm:{PASSWORD}@db:5432/apm", id="driver-without-psycopg"
    ),
    pytest.param(
        "production", f"postgresql+psycopg://apm:{PASSWORD}@db:abc/apm", id="invalid-port"
    ),
    # No "@": SQLAlchemy reads the password as a port and would quote it in its error.
    pytest.param("production", f"postgresql+psycopg://apm:{PASSWORD}", id="password-read-as-port"),
]


@pytest.mark.parametrize(("env", "database_url"), FAILING_CONFIGS)
def test_validation_error_does_not_leak_password(
    monkeypatch: pytest.MonkeyPatch, env: str | None, database_url: str
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
    assert leaked_fragments(printed) == []


@pytest.mark.parametrize(("env", "database_url"), FAILING_CONFIGS)
def test_startup_log_does_not_leak_password(
    tmp_path: Path, env: str | None, database_url: str
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
    assert leaked_fragments(result.stdout + result.stderr) == []
