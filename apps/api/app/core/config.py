from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DATABASE_URL_PREFIX = "postgresql+psycopg://"


class Settings(BaseSettings):
    """Application settings, read from environment variables.

    ENV and DATABASE_URL have no default on purpose: the app must fail to start
    when they are missing. Env files are a convenience for local runs outside
    Docker; later files override earlier ones and real environment variables win.
    """

    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "test", "production"]
    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def _require_psycopg_driver(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(DATABASE_URL_PREFIX):
            raise ValueError(f"DATABASE_URL must start with {DATABASE_URL_PREFIX}")
        return value

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
