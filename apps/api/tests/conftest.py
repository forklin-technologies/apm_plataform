from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import AdminSettings, Settings, get_admin_settings
from app.db.session import get_db
from app.main import create_app
from tests.dbsupport import (
    ScratchDb,
    Tenants,
    create_tenants,
    delete_tenants,
    run_alembic,
    scratch_database,
)
from tests.helpers import UNREACHABLE_DATABASE_URL, make_settings


@pytest.fixture
def isolated_settings() -> Settings:
    """Settings that never read env files or the real database."""
    return make_settings()


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    """App without a usable database. Good for anything that must not touch Postgres."""
    with TestClient(create_app(isolated_settings)) as test_client:
        yield test_client


@pytest.fixture
def db_client() -> Iterator[TestClient]:
    """App wired to the real database from the environment (DATABASE_URL)."""
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def db_down_client(isolated_settings: Settings) -> Iterator[TestClient]:
    """App whose get_db dependency is overridden with an engine that cannot connect."""
    app = create_app(isolated_settings)

    def broken_db() -> Iterator[Session]:
        engine = create_engine(UNREACHABLE_DATABASE_URL, connect_args={"connect_timeout": 1})
        with Session(engine) as session:
            yield session
        engine.dispose()

    app.dependency_overrides[get_db] = broken_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def admin_settings() -> AdminSettings:
    """Needs DATABASE_URL (apm_app) and DATABASE_ADMIN_URL, which only the `tools` service has."""
    try:
        return get_admin_settings()
    except Exception:  # noqa: BLE001  (the message is fixed and never echoes a value)
        pytest.fail(
            "DATABASE_URL and DATABASE_ADMIN_URL are required for the database tests. "
            "Run them with: docker compose run --rm tools pytest"
        )


@pytest.fixture(scope="session")
def admin_engine(admin_settings: AdminSettings) -> Iterator[Engine]:
    """The admin (a superuser in development) on the configured database."""
    engine = create_engine(admin_settings.database_admin_url.get_secret_value())
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(admin_settings: AdminSettings) -> Iterator[Engine]:
    """The application role (apm_app) on the configured database."""
    engine = create_engine(admin_settings.database_url.get_secret_value())
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def tenants(admin_engine: Engine) -> Iterator[Tenants]:
    """Fresh rows for two organizations on the CONFIGURED database, removed at the end.

    The isolation tests run against the real database (not a scratch copy) so that changing it
    (dropping a policy, removing FORCE, granting BYPASSRLS) makes them fail.
    """
    with admin_engine.begin() as connection:
        created = create_tenants(connection)
    try:
        yield created
    finally:
        with admin_engine.begin() as connection:
            delete_tenants(connection, created)


@pytest.fixture
def scratch_db(admin_settings: AdminSettings) -> Iterator[ScratchDb]:
    """An empty throwaway database (dropped afterwards) for tests that run migrations on it."""
    with scratch_database(
        admin_settings.database_admin_url.get_secret_value(),
        admin_settings.database_url.get_secret_value(),
    ) as database:
        yield database


@pytest.fixture(scope="session")
def migrated_scratch_db(admin_settings: AdminSettings) -> Iterator[ScratchDb]:
    """A throwaway database already at `alembic upgrade head`. Tests may add rows, not DDL."""
    with scratch_database(
        admin_settings.database_admin_url.get_secret_value(),
        admin_settings.database_url.get_secret_value(),
    ) as database:
        result = run_alembic(database, "upgrade", "head")
        assert result.returncode == 0, result.stderr
        yield database
