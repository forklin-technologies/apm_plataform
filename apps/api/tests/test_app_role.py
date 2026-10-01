"""T4: what the application role can and cannot do, one assertion per promise in ADR-014."""

from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.config import AdminSettings
from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction

TABLES = ["organizations", "schools", "users", "memberships"]


def test_application_role_is_unprivileged(app_engine: Engine) -> None:
    with app_engine.connect() as connection:
        role = connection.execute(
            text(
                "SELECT current_user, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, "
                "rolreplication, rolinherit FROM pg_roles WHERE rolname = current_user"
            )
        ).one()

    assert role == ("apm_app", False, False, False, False, False, False)


def test_application_role_owns_nothing(app_engine: Engine) -> None:
    with app_engine.connect() as connection:
        owned = connection.execute(
            text(
                "SELECT c.relname FROM pg_class c WHERE c.relowner = "
                "(SELECT oid FROM pg_roles WHERE rolname = 'apm_app')"
            )
        ).all()
        owners = {
            row[0]: row[1]
            for row in connection.execute(
                text("SELECT tablename, tableowner FROM pg_tables WHERE tablename = ANY(:t)"),
                {"t": TABLES},
            )
        }

    assert owned == []
    assert owners == dict.fromkeys(TABLES, "apm_owner")


def test_tables_have_rls_enabled_and_forced(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = ANY(:t) AND relkind = 'r' ORDER BY relname"
            ),
            {"t": TABLES},
        ).all()

    assert rows == [(name, True, True) for name in sorted(TABLES)]


def test_owner_role_cannot_log_in_and_is_not_a_superuser(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        role = connection.execute(
            text(
                "SELECT rolcanlogin, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = 'apm_owner'"
            )
        ).one()

    assert role == (False, False, False)


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE schools DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE schools NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE schools ADD COLUMN extra text",
        "ALTER TABLE schools OWNER TO apm_app",
        "ALTER TABLE users DISABLE ROW LEVEL SECURITY",
        "DROP POLICY schools_select ON schools",
        "CREATE POLICY open_door ON schools USING (true)",
        "DROP TABLE schools",
        "TRUNCATE schools",
        "CREATE TABLE intruder (id int)",
        "CREATE FUNCTION public.app_org() RETURNS uuid LANGUAGE sql AS 'SELECT NULL::uuid'",
        "ALTER ROLE apm_app BYPASSRLS",
        "ALTER ROLE apm_app SUPERUSER",
        "CREATE ROLE sneaky LOGIN",
    ],
)
def test_application_role_cannot_change_schema_or_security(
    app_engine: Engine, statement: str
) -> None:
    with transaction(app_engine) as connection, pytest.raises(ProgrammingError):
        connection.execute(text(statement))


def test_application_role_cannot_grant_itself_more_privileges(app_engine: Engine) -> None:
    """Postgres only WARNS when a non-owner runs GRANT, so check that nothing was granted."""
    with transaction(app_engine) as connection:
        connection.execute(text("GRANT ALL ON schools TO apm_app"))
        connection.execute(text("GRANT ALL ON organizations TO apm_app"))
        granted = connection.execute(
            text(
                "SELECT has_table_privilege('apm_app', 'schools', 'DELETE'), "
                "has_table_privilege('apm_app', 'schools', 'TRUNCATE'), "
                "has_table_privilege('apm_app', 'organizations', 'INSERT'), "
                "has_table_privilege('apm_app', 'organizations', 'DELETE')"
            )
        ).one()

    assert granted == (False, False, False, False)


def test_cannot_set_role_to_owner_or_admin(
    app_engine: Engine, admin_settings: AdminSettings
) -> None:
    from sqlalchemy.engine import make_url

    admin_user = make_url(admin_settings.database_admin_url.get_secret_value()).username
    assert admin_user is not None
    for target in ("apm_owner", admin_user):
        with transaction(app_engine) as connection, pytest.raises(DBAPIError):
            connection.exec_driver_sql(f'SET ROLE "{target}"')
        with transaction(app_engine) as connection, pytest.raises(DBAPIError):
            connection.exec_driver_sql(f'SET SESSION AUTHORIZATION "{target}"')


def test_row_security_off_never_leaks(app_engine: Engine, tenants: Tenants) -> None:
    """SET row_security = off must either error or stay filtered, never show another tenant."""
    own = TenantContext(tenants.org_a)
    for table in ("schools", "memberships", "organizations"):
        with transaction(app_engine, context=own) as connection:
            connection.exec_driver_sql("SET LOCAL row_security = off")
            try:
                rows = connection.execute(text(f"SELECT id FROM {table}")).all()  # noqa: S608
            except DBAPIError:
                continue  # an error is the expected outcome for a role without BYPASSRLS
            foreign: Any = connection.execute(
                text("SELECT 1")
            ).scalar_one()  # connection still usable
            assert foreign == 1
        shown = {row[0] for row in rows}
        assert tenants.org_b not in shown
        assert tenants.school_b1 not in shown
        assert tenants.membership_b1 not in shown


def test_password_hash_is_not_readable(app_engine: Engine, tenants: Tenants) -> None:
    own = TenantContext(tenants.org_a)
    with (
        transaction(app_engine, context=own) as connection,
        pytest.raises(ProgrammingError),
    ):
        connection.execute(text("SELECT password_hash FROM users"))
    with (
        transaction(app_engine, context=own) as connection,
        pytest.raises(ProgrammingError),
    ):
        connection.execute(text("SELECT * FROM users"))
    with transaction(app_engine, context=TenantContext(tenants.org_a)) as connection:
        columns = connection.execute(
            text("SELECT id, email, full_name, is_active, created_at, updated_at FROM users")
        ).all()
    assert columns


def test_role_password_hashes_are_not_readable(app_engine: Engine) -> None:
    with transaction(app_engine) as connection, pytest.raises(ProgrammingError):
        connection.execute(text("SELECT rolpassword FROM pg_authid"))


def test_no_security_definer_function_exists(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        definers = connection.execute(
            text(
                "SELECT n.nspname || '.' || p.proname FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
            )
        ).all()

    assert definers == []


def test_context_functions_are_stable_invoker_with_a_fixed_search_path(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT p.proname, p.provolatile, p.prosecdef, p.proconfig, "
                "pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid = p.pronamespace WHERE n.nspname = 'public' ORDER BY p.proname"
            )
        ).all()

    assert [row[0] for row in rows] == ["app_org", "app_school"]
    for _name, volatility, security_definer, config, owner in rows:
        assert volatility == "s"  # STABLE
        assert security_definer is False
        assert config == ["search_path=pg_catalog"]
        assert owner == "apm_owner"


def test_there_is_no_system_mode_or_open_policy(admin_engine: Engine) -> None:
    """No bypass switch: no policy is open to everyone and none mentions a system flag."""
    with admin_engine.connect() as connection:
        policies = connection.execute(
            text("SELECT policyname, coalesce(qual, ''), coalesce(with_check, '') FROM pg_policies")
        ).all()

    assert len(policies) == 11
    for name, qual, check in policies:
        assert qual.strip().lower() != "true", name
        assert check.strip().lower() != "true", name
        assert "sistema" not in qual + check, name
        assert "system" not in qual + check, name
