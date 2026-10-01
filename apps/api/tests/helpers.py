from app.core.config import Settings

# Nothing listens on port 1, so connecting fails immediately.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://nobody@127.0.0.1:1/none"


def make_settings(env: str = "test", database_url: str = UNREACHABLE_DATABASE_URL) -> Settings:
    """Build Settings from explicit values, ignoring env files and the real environment."""
    # The type: ignore is needed because pydantic-settings accepts `_env_file` and coerces
    # plain strings, which mypy (without the pydantic plugin) cannot see.
    return Settings(_env_file=None, env=env, database_url=database_url)  # type: ignore[call-arg, arg-type]
