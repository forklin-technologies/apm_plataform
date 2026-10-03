"""N2: a pooled connection must not carry session state to its next user.

The pool rolls back on return (which ends the transaction-local tenant context) but a session
variable, a temporary table, a cursor WITH HOLD, a prepared statement, an advisory lock or a LISTEN
outlives the transaction. `discard_session_state` (DISCARD ALL) clears all of it. The leaks are
planted as the admin (apm_app cannot create temporary tables any more), on a pool with a single
connection, and the next checkout must prove it got the same backend (otherwise it proves nothing).
"""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import sessionmaker

from app.core.config import AdminSettings, Settings
from app.db.session import build_engine, discard_session_state
from app.db.tenant import TenantContext, tenant_session
from tests.dbsupport import Tenants, transaction


@pytest.fixture
def hooked_engine(admin_settings: AdminSettings) -> Iterator[Engine]:
    """Admin credentials, ONE pooled connection, built exactly like the API builds its engine."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        database_url=admin_settings.database_admin_url.get_secret_value(),  # type: ignore[arg-type]
    )
    engine = build_engine(settings, pool_size=1, max_overflow=0)
    yield engine
    engine.dispose()


@pytest.fixture
def plain_engine(admin_settings: AdminSettings) -> Iterator[Engine]:
    """Control: the same single-connection pool WITHOUT the hook."""
    engine = create_engine(
        admin_settings.database_admin_url.get_secret_value(), pool_size=1, max_overflow=0
    )
    yield engine
    engine.dispose()


def _plant(engine: Engine) -> Any:
    """Leave every kind of session state behind on the pooled connection; return its backend pid."""
    with engine.connect() as connection:
        pid: Any = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        connection.exec_driver_sql("SET application_name = 'planted'")
        connection.exec_driver_sql("SET statement_timeout = '7s'")
        connection.exec_driver_sql("SET search_path = pg_temp, public")
        connection.exec_driver_sql("CREATE TEMP TABLE schools (planted int)")
        connection.exec_driver_sql("PREPARE planted_stmt AS SELECT 1")
        connection.exec_driver_sql("SELECT pg_advisory_lock(424242)")
        connection.exec_driver_sql("LISTEN planted_channel")
        connection.exec_driver_sql("DECLARE planted_cursor CURSOR WITH HOLD FOR SELECT 1")
        connection.commit()
    return pid


def _observe(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:

        def one(sql: str) -> Any:
            return connection.execute(text(sql)).scalar_one()

        return {
            "pid": one("SELECT pg_backend_pid()"),
            "application_name": one("SHOW application_name"),
            "statement_timeout": one("SHOW statement_timeout"),
            "search_path": one("SHOW search_path"),
            "temp_table": one("SELECT to_regclass('pg_temp.schools') IS NOT NULL"),
            "prepared": one("SELECT count(*) FROM pg_prepared_statements"),
            "cursors": one("SELECT count(*) FROM pg_cursors"),
            "advisory_locks": one(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
            ),
            "listening": one("SELECT count(*) FROM pg_listening_channels()"),
        }


CLEAN = {
    "application_name": "",
    "statement_timeout": "0",
    "search_path": '"$user", public',
    "temp_table": False,
    "prepared": 0,
    "cursors": 0,
    "advisory_locks": 0,
    "listening": 0,
}


def test_the_next_checkout_starts_from_a_clean_session(hooked_engine: Engine) -> None:
    planted_pid = _plant(hooked_engine)

    observed = _observe(hooked_engine)

    assert observed.pop("pid") == planted_pid, "the proof needs the very same backend connection"
    assert observed == CLEAN


def test_control_without_the_hook_the_state_does_leak(plain_engine: Engine) -> None:
    """Why the hook exists: the plain pool hands the planted state to the next user."""
    planted_pid = _plant(plain_engine)

    observed = _observe(plain_engine)

    assert observed["pid"] == planted_pid
    assert observed["application_name"] == "planted"
    assert observed["statement_timeout"] == "7s"
    assert observed["temp_table"] is True
    assert observed["prepared"] == 1
    assert observed["advisory_locks"] == 1
    assert observed["listening"] == 1
    assert observed["cursors"] == 1


def test_a_planted_advisory_lock_is_released_for_other_connections(
    hooked_engine: Engine, admin_engine: Engine
) -> None:
    _plant(hooked_engine)
    _observe(hooked_engine)  # next checkout

    with admin_engine.connect() as other:
        assert other.execute(text("SELECT pg_try_advisory_lock(424242)")).scalar_one() is True
        other.execute(text("SELECT pg_advisory_unlock(424242)"))


def test_a_temp_table_planted_with_pg_temp_first_cannot_shadow_the_real_table(
    hooked_engine: Engine,
) -> None:
    _plant(hooked_engine)

    with hooked_engine.connect() as connection:
        # Resolves to the real public.schools, not to the planted temporary table.
        columns: Any = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE table_name = 'schools' "
                "AND table_schema = 'public' AND column_name = 'organization_id'"
            )
        ).scalar_one()
        assert columns == 1
        connection.execute(text("SELECT organization_id FROM schools LIMIT 0"))


def test_the_tenant_context_is_still_gone_after_the_hook(
    hooked_engine: Engine, tenants: Any
) -> None:
    from app.db.tenant import TenantContext, apply_tenant_context

    with hooked_engine.connect() as connection:
        apply_tenant_context(connection, TenantContext(tenants.org_a))
        connection.commit()
    with hooked_engine.connect() as connection:
        setting: Any = connection.execute(
            text("SELECT current_setting('app.organization_id', true)")
        ).scalar_one()
    assert setting in (None, "")


def test_the_engine_still_works_after_many_cycles(hooked_engine: Engine) -> None:
    """DISCARD ALL deallocates prepared statements: the driver must not rely on them."""
    for _ in range(25):
        with hooked_engine.connect() as connection:
            assert connection.execute(text("SELECT 1 + 1")).scalar_one() == 2


# --- the application role cannot create temporary tables at all --------------------------------


def test_application_role_has_no_temporary_privilege(
    admin_engine: Engine, app_engine: Engine
) -> None:
    with admin_engine.connect() as connection:
        allowed: Any = connection.execute(
            text("SELECT has_database_privilege('apm_app', current_database(), 'TEMPORARY')")
        ).scalar_one()
        public_grants: Any = connection.execute(
            text(
                "SELECT count(*) FROM pg_database d, aclexplode(d.datacl) a "
                "WHERE d.datname = current_database() AND a.grantee = 0 "
                "AND a.privilege_type = 'TEMPORARY'"
            )
        ).scalar_one()
    assert allowed is False
    assert public_grants == 0

    with transaction(app_engine) as connection, pytest.raises(ProgrammingError) as error:
        connection.execute(text("CREATE TEMP TABLE planted (i int)"))
    assert "permission denied" in str(error.value.orig)


# --- P4: no automatic prepared statements ------------------------------------------------------


def test_the_same_query_past_the_prepare_threshold_keeps_working_between_checkouts(
    hooked_engine: Engine,
) -> None:
    """psycopg prepares a statement after 5 executions of the same query, and DISCARD ALL then
    deallocates it behind the driver's back: the next execution would name a prepared statement
    that no longer exists. `prepare_threshold=None` keeps that from ever happening. Run the SAME
    parameterised query well past the default threshold, every time on a new checkout of the one
    pooled connection."""
    pids: set[Any] = set()
    for value in range(15):
        with hooked_engine.connect() as connection:
            result: Any = connection.exec_driver_sql("SELECT %s::int + 1", (value,)).scalar_one()
            assert result == value + 1
            pids.add(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())

    assert len(pids) == 1, "the test needs the very same backend connection every time"


def test_a_deallocate_the_driver_cannot_see_does_not_break_the_next_execution(
    hooked_engine: Engine,
) -> None:
    """psycopg clears its prepared-statement cache when IT runs DISCARD ALL or DEALLOCATE ALL, but
    not when a statement does it behind its back (here a DO block, which anyone with SQL access
    can run). With automatic preparation on, the next execution of an already prepared query would
    then name a statement that no longer exists and fail: one session could break the connection
    for the next user. `prepare_threshold=None` removes the cache the driver would trust."""
    with hooked_engine.connect() as connection:
        for value in range(8):  # past the default threshold of 5: it WOULD be prepared
            assert (
                connection.exec_driver_sql("SELECT %s::int + 1", (value,)).scalar_one() == value + 1
            )
        connection.exec_driver_sql("DO $$ BEGIN EXECUTE 'DEALLOCATE ALL'; END $$")
        assert connection.exec_driver_sql("SELECT %s::int + 1", (41,)).scalar_one() == 42


# --- P5: the hook gives the connection back in the mode the next user expects --------------------


def test_the_second_checkout_applies_the_tenant_context(
    admin_settings: AdminSettings, tenants: Tenants
) -> None:
    """The hook turns autocommit ON to run DISCARD ALL and must turn it back OFF. If it did not,
    the tenant setting (transaction-local) would last one statement and every later query would see
    no tenant at all."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        database_url=admin_settings.database_url.get_secret_value(),  # type: ignore[arg-type]
    )
    engine = build_engine(settings, pool_size=1, max_overflow=0)
    try:
        with engine.connect() as connection:  # first use: its check-in runs the hook
            connection.execute(text("SELECT 1"))
        factory = sessionmaker(engine)
        with tenant_session(factory, TenantContext(tenants.org_a)) as session:
            raw = session.connection().connection.driver_connection
            assert raw is not None and raw.autocommit is False
            count: Any = session.execute(
                text("SELECT count(*) FROM schools WHERE id = ANY(:ids)"),
                {"ids": [tenants.school_a1, tenants.school_a2, tenants.school_b1]},
            ).scalar_one()
            assert count == 2
            # And the context is still there for a SECOND statement of the same transaction.
            organization: Any = session.execute(
                text("SELECT current_setting('app.organization_id', true)")
            ).scalar_one()
            assert organization == str(tenants.org_a)
    finally:
        engine.dispose()


# --- P3: a connection that cannot be cleaned is thrown away -------------------------------------


class _Record:
    def __init__(self) -> None:
        self.invalidated = False

    def invalidate(self, *args: Any, **kwargs: Any) -> None:
        self.invalidated = True


class _ConnectionThatCannotDiscard:
    def __init__(self) -> None:
        self.autocommit = False
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)
        raise RuntimeError("DISCARD ALL failed")


def test_a_connection_whose_discard_fails_is_invalidated_and_never_reused() -> None:
    record = _Record()
    connection = _ConnectionThatCannotDiscard()

    discard_session_state(connection, record)  # must not raise

    assert connection.statements == ["DISCARD ALL"]
    assert record.invalidated is True
    assert connection.autocommit is False  # restored even though the statement failed


def test_a_clean_discard_does_not_invalidate_the_connection() -> None:
    class Healthy(_ConnectionThatCannotDiscard):
        def execute(self, statement: str) -> None:
            self.statements.append(statement)

    record = _Record()
    connection = Healthy()

    discard_session_state(connection, record)

    assert connection.statements == ["DISCARD ALL"]
    assert record.invalidated is False
    assert connection.autocommit is False
