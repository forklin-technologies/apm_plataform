from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit

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


DEV_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8001",
    "http://127.0.0.1:8001",
)
MIN_AUTH_SECRET_LENGTH = 32


_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_origin(scheme: str, netloc: str) -> str:
    """scheme://host[:port], lower-case, without the port the scheme already implies. A browser
    sends `Origin: https://app.example.com` (no `:443`), so both spellings are the same origin."""
    scheme, netloc = scheme.lower(), netloc.lower()
    default = _DEFAULT_PORTS.get(scheme)
    if default is not None and netloc.endswith(f":{default}"):
        netloc = netloc[: -len(f":{default}")]
    return f"{scheme}://{netloc}"


def _normalise_origin(origin: str) -> str:
    parts = urlsplit(origin.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/"):
        raise ValueError(
            "PUBLIC_ORIGINS must be a comma-separated list of origins (scheme://host[:port])"
        )
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError(
            "PUBLIC_ORIGINS must be a comma-separated list of origins (scheme://host[:port])"
        )
    return canonical_origin(parts.scheme, parts.netloc)


class ApiSettings(Settings):
    """Settings of the API process: everything in `Settings` plus authentication.

    The tools that only need the database (Alembic, the seed, the posture command) keep using
    `Settings`/`AdminSettings` and do not need the authentication secret.
    """

    # HMAC key for the CSRF token and for the rate-limit keys (e-mail and IP are stored only as
    # HMACs). Long and random; `openssl rand -hex 32`.
    auth_secret: SecretStr
    # Cookies: with True the names carry the `__Host-` prefix and the cookies are `Secure`. False
    # exists only because WebKit does not accept a Secure cookie over plain http in development.
    cookie_secure: bool = True
    # The origins the site is served from, comma-separated. Required in production; in development
    # it defaults to the local dev origins. Every state-changing request must come from one of them.
    public_origins: str = ""
    # Where the links in e-mails point (the web app).
    public_base_url: str = "http://localhost:3000"
    # The only sender implemented writes e-mails to files (development); production needs one.
    email_sender: Literal["file"] = "file"
    outbox_dir: str = "/outbox"

    @field_validator("auth_secret")
    @classmethod
    def _validate_auth_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_AUTH_SECRET_LENGTH:
            raise ValueError(f"AUTH_SECRET must be at least {MIN_AUTH_SECRET_LENGTH} characters")
        return value

    @field_validator("public_origins")
    @classmethod
    def _validate_public_origins(cls, value: str) -> str:
        for origin in (part for part in value.split(",") if part.strip()):
            _normalise_origin(origin)
        return value

    @model_validator(mode="after")
    def _production_rules(self) -> Self:
        if not self.is_production:
            return self
        if not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when ENV=production")
        origins = self.allowed_origins
        if not origins or any(not origin.startswith("https://") for origin in origins):
            raise ValueError(
                "PUBLIC_ORIGINS must list the https origins of the site when ENV=production"
            )
        if self.email_sender == "file":
            raise ValueError(
                "no e-mail sender is available for ENV=production yet: the API cannot start there"
            )
        return self

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        configured = tuple(
            _normalise_origin(part) for part in self.public_origins.split(",") if part.strip()
        )
        if configured or self.is_production:
            return configured
        return DEV_ORIGINS

    @property
    def session_cookie_name(self) -> str:
        return "__Host-apm_session" if self.cookie_secure else "apm_session"

    @property
    def csrf_cookie_name(self) -> str:
        return "__Host-apm_csrf" if self.cookie_secure else "apm_csrf"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache
def get_admin_settings() -> AdminSettings:
    return AdminSettings()  # type: ignore[call-arg]


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()  # type: ignore[call-arg]
