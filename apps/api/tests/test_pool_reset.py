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

from app.core.config import AdminSettings, Settings
from app.db.session import build_engine
from tests.dbsupport import transaction


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
        pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
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
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
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
        columns = connection.execute(
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
        setting = connection.execute(
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
        allowed = connection.execute(
            text("SELECT has_database_privilege('apm_app', current_database(), 'TEMPORARY')")
        ).scalar_one()
        public_grants = connection.execute(
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
