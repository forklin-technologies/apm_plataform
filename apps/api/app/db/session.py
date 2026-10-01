from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings

CONNECT_TIMEOUT_SECONDS = 3


def build_engine(settings: Settings) -> Engine:
    """Create the engine. It connects lazily, so the API can start with the database down."""
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    """Request-scoped session, taken from the factory the app built at startup."""
    with request.app.state.session_factory() as session:
        yield session
