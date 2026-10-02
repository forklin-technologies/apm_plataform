from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

DATABASE_URL_PREFIX = "postgresql+psycopg://"
# Static on purpose: error messages must never echo any part of the URL (it holds the password).
DATABASE_URL_HINT = (
    f"expected {DATABASE_URL_PREFIX}USER:PASSWORD@HOST:PORT/DBNAME, "
    "with special characters (@ : / ? #) percent-encoded in the password"
)
# The ONLY query parameters a database URL may carry (an allow-list: a deny-list always misses a
# key). The driver turns every query parameter into a libpq connection option, so anything else
# (user, password, host, hostaddr, dbname, service, passfile, options="-c row_security=off", ...)
# could override what the URL says, or what the checks below verified, at connect time.
ALLOWED_QUERY_KEYS = ("sslmode", "sslrootcert", "connect_timeout", "application_name")
# The Postgres role the application connects as (see docs/tenancy.md).
APP_DB_ROLE = "apm_app"


def validate_database_url(url: str, name: str) -> None:
    """Reject a database URL that is unsafe to use or to report. Shared by every URL setting.

    Messages are fixed strings: they never echo any part of the URL, because it holds the
    password and validation errors end up in the startup log.
    """
    if not url.startswith(DATABASE_URL_PREFIX):
        raise ValueError(f"{name} must start with {DATABASE_URL_PREFIX}")
    # A valid URL has at most one "@" (the userinfo/host separator). An unescaped "@" in the
    # password always adds another one, and SQLAlchemy would then read part of the password
    # as the host, database or query, or silently ignore it, and the driver's errors would
    # print it in the logs. So this rule is exact, with no per-field special cases.
    if url.count("@") > 1:
        raise ValueError(f"{name} is not valid: {DATABASE_URL_HINT}")
    try:
        parsed = make_url(url)
    except Exception:
        # SQLAlchemy parse errors can quote part of the URL (e.g. the password read as a
        # port), so drop the original exception and report a fixed message.
        raise ValueError(f"{name} is not valid: {DATABASE_URL_HINT}") from None
    if any(char.isspace() for char in parsed.host or ""):
        raise ValueError(f"{name} is not valid: {DATABASE_URL_HINT}")
    # Exact, case-sensitive match, and no repeated key (a repeated key is a tuple of values).
    query = parsed.query.items()
    if any(key not in ALLOWED_QUERY_KEYS or not isinstance(value, str) for key, value in query):
        allowed = ", ".join(ALLOWED_QUERY_KEYS)
        raise ValueError(f"{name} may only use these query parameters, each once: {allowed}")


class Settings(BaseSettings):
    """Application settings, read from environment variables.

    ENV and DATABASE_URL have no default on purpose: the app must fail to start
    when they are missing. Env files are a convenience for local runs outside
    Docker; later files override earlier ones and real environment variables win.

    Validation errors are printed in the startup log, so they must never echo the
    offending value: DATABASE_URL carries the database password.
    """

    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    env: Literal["development", "test", "production"]
    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: SecretStr) -> SecretStr:
        validate_database_url(value.get_secret_value(), "DATABASE_URL")
        return value

    @property
    def is_production(self) -> bool:
        return self.env == "production"


class AdminSettings(Settings):
    """Settings for tools that need the admin credential: Alembic, the seed and the tests.

    The API process never reads DATABASE_ADMIN_URL, so it never holds the admin password.
    """

    database_admin_url: SecretStr

    @field_validator("database_admin_url")
    @classmethod
    def _validate_database_admin_url(cls, value: SecretStr) -> SecretStr:
        validate_database_url(value.get_secret_value(), "DATABASE_ADMIN_URL")
        return value

    @model_validator(mode="after")
    def _check_roles(self) -> Self:
        # make_url(...).username IS the effective user: no query parameter can override it (above).
        app_user = make_url(self.database_url.get_secret_value()).username
        admin_user = make_url(self.database_admin_url.get_secret_value()).username
        if app_user != APP_DB_ROLE:
            raise ValueError(f"DATABASE_URL must connect as the {APP_DB_ROLE} role")
        if admin_user == APP_DB_ROLE:
            raise ValueError(f"DATABASE_ADMIN_URL must not connect as the {APP_DB_ROLE} role")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache
def get_admin_settings() -> AdminSettings:
    return AdminSettings()  # type: ignore[call-arg]
