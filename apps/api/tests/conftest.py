from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
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
