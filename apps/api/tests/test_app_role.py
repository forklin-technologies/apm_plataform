"""T4: what the application role can and cannot do, one assertion per promise in ADR-014."""

from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.config import AdminSettings
from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction

TABLES = ["organizations", "schools", "users", "memberships"]
AUTH_TABLES = ["sessions", "login_attempts", "invitations"]


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
            {"t": [*TABLES, *AUTH_TABLES]},
        ).all()

    assert rows == [(name, True, True) for name in sorted([*TABLES, *AUTH_TABLES])]


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


# The closed list (ADR-016): every function in the public schema, with everything that decides what
# it can do. Adding a SECURITY DEFINER function, changing the owner, the search_path or who may
# execute one, or adding a policy that lets apm_definer through, fails here until the list (and the
# ADR) change on purpose.
FUNCTIONS = {
    # signature: (owner, security definer, proconfig, ACL, volatility)
    "accept_invitation(bytea,text,text,uuid)": (
        "apm_definer",
        True,
        ["search_path=pg_catalog"],
        {"apm_definer=X/apm_definer", "apm_app=X/apm_definer"},
        "v",
    ),
    "find_login_identity(text)": (
        "apm_definer",
        True,
        ["search_path=pg_catalog"],
        {"apm_definer=X/apm_definer", "apm_app=X/apm_definer"},
        "s",
    ),
    "list_memberships_for_user(uuid)": (
        "apm_definer",
        True,
        ["search_path=pg_catalog"],
        {"apm_definer=X/apm_definer", "apm_app=X/apm_definer"},
        "s",
    ),
    **{
        f"{name}()": (
            "apm_owner",
            False,
            ["search_path=pg_catalog"],
            {"apm_owner=X/apm_owner", "apm_app=X/apm_owner", "apm_definer=X/apm_owner"},
            "s",
        )
        for name in ("app_org", "app_school", "app_session_id", "app_user_id")
    },
}
# table.policy for every permissive policy that applies to apm_definer (TO apm_definer).
DEFINER_POLICIES = {
    "invitations.invitations_definer_select",
    "invitations.invitations_definer_update",
    "memberships.memberships_definer_insert",
    "memberships.memberships_definer_select",
    "organizations.organizations_definer_select",
    "schools.schools_definer_select",
    "users.users_definer_insert",
    "users.users_definer_select",
}


def test_the_functions_of_the_public_schema_are_exactly_the_closed_list(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT p.oid::regprocedure::text, pg_get_userbyid(p.proowner), p.prosecdef, "
                "p.proconfig, p.proacl::text[], p.provolatile::text FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
                "AND p.prokind = 'f'"
            )
        ).all()

    found = {
        row[0].removeprefix("public."): (row[1], row[2], row[3], set(row[4] or []), row[5])
        for row in rows
    }
    assert found == FUNCTIONS


def test_no_security_definer_function_is_owned_by_a_role_that_bypasses_row_level_security(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as connection:
        owners = connection.execute(
            text(
                "SELECT DISTINCT r.rolname, r.rolsuper, r.rolbypassrls, r.rolcanlogin "
                "FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner WHERE p.prosecdef "
                "AND p.pronamespace = 'public'::regnamespace"
            )
        ).all()

    assert [tuple(row) for row in owners] == [("apm_definer", False, False, False)]


def test_the_policies_that_let_apm_definer_through_are_exactly_the_closed_list(
    admin_engine: Engine,
) -> None:
    """Row level security is not bypassed anywhere: apm_definer sees a row only through one of
    these policies, each of them written for it (`TO apm_definer`)."""
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT tablename || '.' || policyname, roles::text[], permissive FROM pg_policies "
                "WHERE roles::text[] && ARRAY['apm_definer', 'public']"
            )
        ).all()

    to_definer = {r[0] for r in rows if r[1] == ["apm_definer"]}
    assert to_definer == DEFINER_POLICIES
    assert {r[2] for r in rows} == {"PERMISSIVE"}  # and no RESTRICTIVE one hides behind the list


def test_apm_definer_owns_nothing_but_its_functions_and_has_no_other_membership(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as connection:
        owned_relations = connection.execute(
            text(
                "SELECT c.relname FROM pg_class c WHERE c.relowner = "
                "(SELECT oid FROM pg_roles WHERE rolname = 'apm_definer')"
            )
        ).all()
        members_of = connection.execute(
            text(
                "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
                "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = 'apm_definer'"
            )
        ).all()

    assert owned_relations == []
    assert members_of == []


def test_no_function_is_executable_by_public(app_engine: Engine) -> None:
    """EXECUTE is granted to named roles only: PostgreSQL grants it to PUBLIC by default, and
    every function of the schema must have had that taken away."""
    with app_engine.connect() as connection:
        public_can: Any = connection.execute(
            text(
                "SELECT count(*) FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a "
                "WHERE p.pronamespace = 'public'::regnamespace AND a.grantee = 0"
            )
        ).scalar_one()
    assert public_can == 0


def test_context_functions_are_stable_invoker_with_a_fixed_search_path(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT p.proname, p.provolatile, p.prosecdef, p.proconfig, "
                "pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND NOT p.prosecdef "
                "ORDER BY p.proname"
            )
        ).all()

    # The financial functions of 0007 are covered by tests/financial/test_triggers.py: here, the
    # context functions of the tenancy core and of the authentication (0006).
    context = ("app_org", "app_school", "app_session_id", "app_user_id")
    rows = [row for row in rows if row[0] in context]
    assert [row[0] for row in rows] == list(context)
    for _name, volatility, security_definer, config, owner in rows:
        assert volatility == "s"  # STABLE
        assert security_definer is False
        assert config == ["search_path=pg_catalog"]
        assert owner == "apm_owner"


# Policies whose condition is literally `true`: the ONE open read is the lookup of a login identity
# (find_login_identity must find a user by e-mail with no tenant yet), limited to apm_definer, four
# columns, and reachable only through that function.
OPEN_POLICIES = {"users_definer_select"}


def test_there_is_no_system_mode_and_the_only_open_policy_is_the_login_lookup(
    admin_engine: Engine,
) -> None:
    """No bypass switch: no policy is open to everyone and none mentions a system flag."""
    with admin_engine.connect() as connection:
        policies = connection.execute(
            text(
                "SELECT policyname, coalesce(qual, ''), coalesce(with_check, ''), roles::text[] "
                "FROM pg_policies"
            )
        ).all()

    # 30 of TASK-003 and 0006 + 34 of the financial schema (0007)
    assert len(policies) == 30 + 34
    open_ones = set()
    for name, qual, check, roles in policies:
        if qual.strip().lower() == "true" or check.strip().lower() == "true":
            open_ones.add(name)
            assert roles == ["apm_definer"], name  # never open to PUBLIC or to the application
        assert "sistema" not in qual + check, name
        assert "system" not in qual + check, name
    assert open_ones == OPEN_POLICIES


# --- the grants of apm_app, exactly (a grant added by mistake or by tampering fails here) ---------

ALL_COLUMNS = {
    "organizations": {"id", "name", "slug", "created_at", "updated_at"},
    "schools": {"id", "organization_id", "name", "slug", "created_at", "updated_at"},
    "memberships": {
        "id",
        "user_id",
        "organization_id",
        "school_id",
        "role",
        "status",
        "created_at",
        "updated_at",
    },
    "users": {"id", "email", "full_name", "password_hash", "is_active", "created_at", "updated_at"},
    "sessions": {
        "id",
        "user_id",
        "membership_id",
        "created_at",
        "last_seen_at",
        "expires_at",
        "revoked_at",
        "revoked_reason",
        "ip",
        "user_agent_hash",
    },
    "login_attempts": {"id", "kind", "subject_hmac", "ip_hmac", "succeeded", "attempted_at"},
    "invitations": {
        "id",
        "organization_id",
        "school_id",
        "email",
        "role",
        "token_hash",
        "invited_by_user_id",
        "expires_at",
        "accepted_at",
        "accepted_user_id",
        "revoked_at",
        "created_at",
        "updated_at",
    },
}
# Effective privilege (table-level or column-level) per column, per privilege, for apm_app.
SELECTABLE = {
    "organizations": ALL_COLUMNS["organizations"],
    "schools": ALL_COLUMNS["schools"],
    "memberships": ALL_COLUMNS["memberships"],
    # Everything but password_hash: only the SECURITY DEFINER lookup reads it.
    "users": ALL_COLUMNS["users"] - {"password_hash"},
    "sessions": ALL_COLUMNS["sessions"],
    "login_attempts": ALL_COLUMNS["login_attempts"],
    # Everything but token_hash: an invitation is found by its hash, never listed.
    "invitations": ALL_COLUMNS["invitations"] - {"token_hash"},
}
INSERTABLE = {
    "organizations": set(),
    "schools": ALL_COLUMNS["schools"],
    "memberships": set(),
    "users": set(),
    "sessions": ALL_COLUMNS["sessions"],
    "login_attempts": ALL_COLUMNS["login_attempts"],
    "invitations": ALL_COLUMNS["invitations"],
}
# Never id, organization_id, school_id, user_id or created_at (M1): only what changes business data.
UPDATABLE = {
    "organizations": {"name", "updated_at"},
    "schools": {"name", "updated_at"},
    "memberships": {"role", "status", "updated_at"},
    "users": {"password_hash", "updated_at"},  # the user's own row, by policy
    "sessions": {"last_seen_at", "revoked_at", "revoked_reason"},
    "login_attempts": set(),
    "invitations": {"revoked_at", "updated_at"},
}
DELETABLE = {"memberships", "login_attempts"}


def _column_privilege(admin_engine: Engine, privilege: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {table: set() for table in ALL_COLUMNS}
    with admin_engine.connect() as connection:
        for table, columns in ALL_COLUMNS.items():
            for column in columns:
                allowed: Any = connection.execute(
                    text("SELECT has_column_privilege('apm_app', :t, :c, :p)"),
                    {"t": table, "c": column, "p": privilege},
                ).scalar_one()
                if allowed:
                    result[table].add(column)
    return result


@pytest.mark.parametrize(
    ("privilege", "expected"),
    [("SELECT", SELECTABLE), ("INSERT", INSERTABLE), ("UPDATE", UPDATABLE)],
)
def test_application_role_column_privileges_are_exactly_these(
    admin_engine: Engine, privilege: str, expected: dict[str, set[str]]
) -> None:
    assert _column_privilege(admin_engine, privilege) == expected


def test_application_role_table_privileges_are_exactly_these(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name, privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = 'apm_app' AND table_schema = 'public' "
                "AND table_name = ANY(:tables) ORDER BY 1, 2"
            ),
            {"tables": list(ALL_COLUMNS)},
        ).all()

    assert {tuple(row) for row in rows} == {
        ("organizations", "SELECT"),
        ("schools", "SELECT"),
        ("schools", "INSERT"),
        ("memberships", "SELECT"),
        ("memberships", "DELETE"),
        ("sessions", "SELECT"),
        ("sessions", "INSERT"),
        ("login_attempts", "SELECT"),
        ("login_attempts", "INSERT"),
        ("login_attempts", "DELETE"),
        ("invitations", "INSERT"),
    }
