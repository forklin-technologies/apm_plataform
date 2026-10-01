from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

DATABASE_URL_PREFIX = "postgresql+psycopg://"
# Static on purpose: error messages must never echo any part of the URL (it holds the password).
DATABASE_URL_HINT = (
    f"expected {DATABASE_URL_PREFIX}USER:PASSWORD@HOST:PORT/DBNAME, "
    "with special characters (@ : / ? #) percent-encoded in the password"
)
INVALID_DATABASE_URL_MESSAGE = f"DATABASE_URL is not valid: {DATABASE_URL_HINT}"


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
    def _require_psycopg_driver(cls, value: SecretStr) -> SecretStr:
        url = value.get_secret_value()
        if not url.startswith(DATABASE_URL_PREFIX):
            raise ValueError(f"DATABASE_URL must start with {DATABASE_URL_PREFIX}")
        try:
            parsed = make_url(url)
        except Exception:
            # SQLAlchemy parse errors can quote part of the URL (e.g. the password read as a
            # port), so drop the original exception and report a fixed message.
            raise ValueError(INVALID_DATABASE_URL_MESSAGE) from None
        host = parsed.host or ""
        # An unescaped "@" in the password makes SQLAlchemy read the rest of the URL wrongly: the
        # tail of the password becomes the host, or the database when a "/" follows it, or is
        # thrown into the query (which SQLAlchemy then drops) when a "?" follows it. The driver's
        # errors would print those pieces in the logs, so reject any "@" outside the userinfo.
        misread_parts = (host, parsed.database or "", url.partition("?")[2])
        if any("@" in part for part in misread_parts) or any(char.isspace() for char in host):
            raise ValueError(INVALID_DATABASE_URL_MESSAGE)
        return value

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
