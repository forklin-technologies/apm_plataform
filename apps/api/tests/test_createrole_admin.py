"""H1: the migrations must work for the admin of a managed service: a role with CREATEROLE that owns
the database but is NOT a superuser.

PostgreSQL 16 lets such a role create and administer the roles it created, but not set the
privileged attributes (SUPERUSER, BYPASSRLS, REPLICATION, CREATEDB), even to "off". These tests run
in a SECOND, throwaway cluster (`db-clean`), so the roles do not exist yet and are created by that
very admin, exactly as in a managed service. The main cluster could not show that: its roles were
created by a superuser.
"""

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db.posture import check_posture
from tests.dbsupport import ScratchDb, run_alembic

CI_ADMIN = "apm_ci_admin"
DATABASE = "apm_h1"
EXPECTED_TABLES = {
    "alembic_version",
    "invitations",
    "login_attempts",
    "memberships",
    "organizations",
    "schools",
    "sessions",
    "users",
}
EXPECTED_POLICIES = 30
EXPECTED_FUNCTIONS = 7
CREATED_ROLES = ("apm_app", "apm_owner", "apm_definer")


@dataclass(frozen=True)
class CleanCluster:
    superuser_url: str
    admin_url: str
    app_url: str

    @property
    def database(self) -> ScratchDb:
        return ScratchDb(DATABASE, self.admin_url, self.app_url)


def _wipe(superuser_url: str) -> None:
    engine = create_engine(superuser_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)'))
            for role in (*CREATED_ROLES, CI_ADMIN):
                exists = connection.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
                ).scalar_one_or_none()
                if exists:
                    connection.execute(text(f'DROP OWNED BY "{role}"'))
                    connection.execute(text(f'DROP ROLE "{role}"'))
    finally:
        engine.dispose()


@pytest.fixture
def clean_cluster() -> Iterator[CleanCluster]:
    superuser_url = os.environ.get("CLEAN_CLUSTER_URL")
    if not superuser_url:
        pytest.fail(
            "CLEAN_CLUSTER_URL is required (the throwaway cluster `db-clean`). "
            "Run the tests with: docker compose run --rm tools pytest"
        )
    _wipe(superuser_url)
    admin_password = secrets.token_hex(16)
    app_password = secrets.token_hex(16)
    engine = create_engine(superuser_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    f"CREATE ROLE {CI_ADMIN} LOGIN CREATEROLE NOSUPERUSER "
                    f"PASSWORD '{admin_password}'"
                )
            )
            connection.execute(text(f'CREATE DATABASE "{DATABASE}" OWNER {CI_ADMIN}'))
    finally:
        engine.dispose()
    base = make_url(superuser_url)
    cluster = CleanCluster(
        superuser_url=superuser_url,
        admin_url=base.set(
            username=CI_ADMIN, password=admin_password, database=DATABASE
        ).render_as_string(hide_password=False),
        app_url=base.set(
            username="apm_app", password=app_password, database=DATABASE
        ).render_as_string(hide_password=False),
    )
    yield cluster
    _wipe(superuser_url)


def _query(url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(sql), params)]
    finally:
        engine.dispose()


def _roles(cluster: CleanCluster) -> dict[str, tuple[Any, ...]]:
    rows = _query(
        cluster.superuser_url,
        "SELECT rolname, rolsuper, rolbypassrls, rolreplication, rolcreatedb, rolcreaterole, "
        "rolcanlogin, rolinherit FROM pg_roles "
        "WHERE rolname IN ('apm_app', 'apm_owner', 'apm_definer')",
    )
    return {row[0]: row[1:] for row in rows}


def _catalog(cluster: CleanCluster) -> dict[str, Any]:
    tables = {
        r[0]
        for r in _query(
            cluster.admin_url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    }
    (policies,) = _query(cluster.admin_url, "SELECT count(*) FROM pg_policies")[0]
    (functions,) = _query(
        cluster.admin_url,
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public'",
    )[0]
    return {"tables": tables, "policies": policies, "functions": functions}


def test_the_test_admin_is_really_not_a_superuser(clean_cluster: CleanCluster) -> None:
    (row,) = _query(
        clean_cluster.admin_url,
        "SELECT rolsuper, rolcreaterole, rolbypassrls, current_setting('is_superuser') "
        "FROM pg_roles WHERE rolname = current_user",
    )
    assert row == (False, True, False, "off")
    assert _roles(clean_cluster) == {}  # and the roles do not exist yet


def test_upgrade_creates_everything_as_a_createrole_admin(clean_cluster: CleanCluster) -> None:
    result = run_alembic(clean_cluster.database, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert _catalog(clean_cluster) == {
        "tables": EXPECTED_TABLES,
        "policies": EXPECTED_POLICIES,
        "functions": EXPECTED_FUNCTIONS,
    }
    # (superuser, bypassrls, replication, createdb, createrole, login, inherit)
    assert _roles(clean_cluster) == {
        "apm_app": (False, False, False, False, False, True, False),
        "apm_owner": (False, False, False, False, False, False, True),
        "apm_definer": (False, False, False, False, False, False, True),
    }
    owners = {
        r[0]: r[1]
        for r in _query(
            clean_cluster.admin_url,
            "SELECT tablename, tableowner FROM pg_tables WHERE tablename <> 'alembic_version' "
            "AND schemaname = 'public'",
        )
    }
    assert set(owners.values()) == {"apm_owner"}
    function_owners = {
        row[0]: row[1]
        for row in _query(
            clean_cluster.admin_url,
            "SELECT p.proname, pg_get_userbyid(p.proowner) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.prosecdef",
        )
    }
    assert function_owners == {
        "find_login_identity": "apm_definer",
        "list_memberships_for_user": "apm_definer",
        "accept_invitation": "apm_definer",
    }

    # The application role can log in with the password from DATABASE_URL and passes the posture.
    engine = create_engine(clean_cluster.app_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == "apm_app"
            assert check_posture(connection) == []
    finally:
        engine.dispose()


def test_downgrade_and_upgrade_again_work_for_that_admin(clean_cluster: CleanCluster) -> None:
    assert run_alembic(clean_cluster.database, "upgrade", "head").returncode == 0

    down = run_alembic(clean_cluster.database, "downgrade", "base")

    assert down.returncode == 0, down.stderr
    assert _catalog(clean_cluster) == {"tables": {"alembic_version"}, "policies": 0, "functions": 0}
    assert _roles(clean_cluster) == {}  # the admin created them, so it can drop them
    again = run_alembic(clean_cluster.database, "upgrade", "head")
    assert again.returncode == 0, again.stderr
    assert _catalog(clean_cluster) == {
        "tables": EXPECTED_TABLES,
        "policies": EXPECTED_POLICIES,
        "functions": EXPECTED_FUNCTIONS,
    }
    repeated = run_alembic(clean_cluster.database, "upgrade", "head")
    assert repeated.returncode == 0 and "Running upgrade" not in repeated.stderr


def _tamper(cluster: CleanCluster, *statements: str) -> None:
    engine = create_engine(cluster.superuser_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            for statement in statements:
                connection.execute(text(statement))
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("tamper", "reported"),
    [
        ("ALTER ROLE apm_app BYPASSRLS", "apm_app has BYPASSRLS"),
        ("ALTER ROLE apm_owner SUPERUSER", "apm_owner has SUPERUSER"),
        ("ALTER ROLE apm_app REPLICATION", "apm_app has REPLICATION"),
        ("ALTER ROLE apm_app CREATEDB", "apm_app has CREATEDB"),
    ],
)
def test_a_loosened_role_that_cannot_be_dropped_makes_the_migration_fail_clearly(
    clean_cluster: CleanCluster, tamper: str, reported: str
) -> None:
    """A role another database still depends on survives the downgrade, so re-running revision
    0002 meets it loosened: it must stop and say what is wrong (a cluster administrator has to
    fix it), not carry on or repair it silently."""
    assert run_alembic(clean_cluster.database, "upgrade", "head").returncode == 0
    # A privilege in another database is a shared dependency: DROP ROLE then refuses.
    _tamper(clean_cluster, "GRANT CONNECT ON DATABASE postgres TO apm_app, apm_owner", tamper)
    # Re-running revision 0002 is the only thing that notices (upgrade at head runs nothing).
    assert run_alembic(clean_cluster.database, "downgrade", "0001_baseline").returncode == 0
    assert set(_roles(clean_cluster)) == {"apm_app", "apm_owner"}  # kept, as designed

    result = run_alembic(clean_cluster.database, "upgrade", "head")

    assert result.returncode != 0
    assert reported in result.stderr
    assert "cluster administrator must correct them" in result.stderr
    current = run_alembic(clean_cluster.database, "current")
    assert "0001_baseline" in current.stdout + current.stderr
    assert _catalog(clean_cluster)["policies"] == 0  # nothing half-applied


def test_a_loosened_role_nothing_else_depends_on_is_dropped_and_recreated_clean(
    clean_cluster: CleanCluster,
) -> None:
    """The other outcome: when the downgrade can drop the roles, the next upgrade creates them
    with the safe defaults, so downgrade + upgrade is a repair for a CREATEROLE admin too."""
    assert run_alembic(clean_cluster.database, "upgrade", "head").returncode == 0
    _tamper(clean_cluster, "ALTER ROLE apm_app BYPASSRLS")
    assert run_alembic(clean_cluster.database, "downgrade", "0001_baseline").returncode == 0
    assert _roles(clean_cluster) == {}

    result = run_alembic(clean_cluster.database, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert _roles(clean_cluster)["apm_app"][1] is False  # rolbypassrls


def test_roles_created_by_somebody_else_are_refused_with_a_clear_message(
    clean_cluster: CleanCluster,
) -> None:
    """Pre-existing roles the admin has no ADMIN OPTION on cannot be administered by it."""
    engine = create_engine(clean_cluster.superuser_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE ROLE apm_app LOGIN"))
            connection.execute(text("CREATE ROLE apm_owner NOLOGIN"))
    finally:
        engine.dispose()

    result = run_alembic(clean_cluster.database, "upgrade", "head")

    assert result.returncode != 0
    assert "cannot administer the roles apm_app and apm_owner" in result.stderr
    assert "run the migration as a superuser, or as the role that created them" in result.stderr


def test_the_documented_remedy_works_admin_option_on_the_existing_roles(
    clean_cluster: CleanCluster,
) -> None:
    engine = create_engine(clean_cluster.superuser_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE ROLE apm_app LOGIN"))
            connection.execute(text("CREATE ROLE apm_owner NOLOGIN"))
            connection.execute(text(f"GRANT apm_app, apm_owner TO {CI_ADMIN} WITH ADMIN OPTION"))
    finally:
        engine.dispose()

    result = run_alembic(clean_cluster.database, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert _catalog(clean_cluster)["policies"] == EXPECTED_POLICIES


def _upgrade_to_0005_then_create_definer_as_superuser(clean_cluster: CleanCluster) -> None:
    upgraded = run_alembic(clean_cluster.database, "upgrade", "0005_tenancy_write_grants")
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(clean_cluster.superuser_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE ROLE apm_definer NOLOGIN"))
    finally:
        engine.dispose()


def test_a_definer_role_created_by_somebody_else_is_refused_with_a_clear_message(
    clean_cluster: CleanCluster,
) -> None:
    """The role of revision 0006 follows the same rule as the two before it: the admin must be able
    to administer it, and when it cannot, the message says so (nothing half-applied)."""
    _upgrade_to_0005_then_create_definer_as_superuser(clean_cluster)

    result = run_alembic(clean_cluster.database, "upgrade", "head")

    assert result.returncode != 0
    assert "cannot create or administer the role apm_definer" in result.stderr
    current = run_alembic(clean_cluster.database, "current")
    assert "0005_tenancy_write_grants" in current.stdout + current.stderr


def test_the_definer_role_remedy_works_admin_option_on_the_existing_role(
    clean_cluster: CleanCluster,
) -> None:
    _upgrade_to_0005_then_create_definer_as_superuser(clean_cluster)
    _tamper(clean_cluster, f"GRANT apm_definer TO {CI_ADMIN} WITH ADMIN OPTION")

    result = run_alembic(clean_cluster.database, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert _catalog(clean_cluster)["policies"] == EXPECTED_POLICIES
    assert _roles(clean_cluster)["apm_definer"][:6] == (False, False, False, False, False, False)
