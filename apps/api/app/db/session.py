from collections.abc import Iterator
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings

CONNECT_TIMEOUT_SECONDS = 3


def discard_session_state(dbapi_connection: Any, connection_record: Any) -> None:
    """Pool hook: wipe EVERY piece of session state when a connection goes back to the pool.

    The pool already rolls back, which ends the transaction-local tenant context. It does not touch
    state that outlives a transaction: SET of a session variable, temporary tables (which can
    shadow a real table for whoever puts pg_temp first in the search_path), cursors WITH HOLD,
    prepared statements, advisory locks, LISTEN. DISCARD ALL clears all of it, so the next user of
    the connection starts from a clean session. A connection that cannot be cleaned is dropped.
    """
    try:
        previous = dbapi_connection.autocommit
        dbapi_connection.autocommit = True  # DISCARD ALL cannot run inside a transaction block
        try:
            dbapi_connection.execute("DISCARD ALL")
        finally:
            dbapi_connection.autocommit = previous
    except Exception:
        connection_record.invalidate()


def build_engine(settings: Settings, **engine_options: Any) -> Engine:
    """Create the engine. It connects lazily, so the API can start with the database down."""
    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        # A failing statement would otherwise print its parameters in the error: password hashes,
        # tokens. They are hidden everywhere (logs, tracebacks, problem details).
        hide_parameters=True,
        # No automatic server-side prepared statements: DISCARD ALL deallocates them behind the
        # driver's back, and the next execution would fail.
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS, "prepare_threshold": None},
        **engine_options,
    )
    event.listen(engine, "checkin", discard_session_state)
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    """Request-scoped session, taken from the factory the app built at startup."""
    with request.app.state.session_factory() as session:
        yield session
