from typing import Any

from pydantic import SecretStr

from app.core.config import ApiSettings

# Nothing listens on port 1, so connecting fails immediately.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://nobody@127.0.0.1:1/none"
# Not a secret: a fixed value for tests only (the real one comes from the environment).
TEST_AUTH_SECRET = "test-only-auth-secret-0123456789abcdef-not-for-real-use"  # noqa: S105
TEST_SECRET = SecretStr(TEST_AUTH_SECRET)
TEST_ORIGIN = "http://localhost:3000"


def make_settings(
    env: str = "test", database_url: str = UNREACHABLE_DATABASE_URL, **overrides: Any
) -> ApiSettings:
    """Build ApiSettings from explicit values, ignoring env files and the real environment.

    `env="production"` is built without validation (`model_construct`) because the production
    rules (https origins, a real e-mail sender) cannot be met by the test setup; the tests that
    exercise those rules call the validators themselves.
    """
    values: dict[str, Any] = {
        "env": env,
        "database_url": database_url,
        "auth_secret": TEST_AUTH_SECRET,
        "cookie_secure": False,
        "outbox_dir": "/nonexistent-outbox",
        **overrides,
    }
    if env == "production":
        secret_values = {
            "database_url": SecretStr(database_url),
            "auth_secret": SecretStr(TEST_AUTH_SECRET),
        }
        return ApiSettings.model_construct(**{**values, **secret_values})
    return ApiSettings(_env_file=None, **values)  # type: ignore[call-arg]
