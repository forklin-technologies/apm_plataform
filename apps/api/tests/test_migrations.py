"""T1: migrations from an empty database, and the password handling in them."""

import importlib.util
import traceback
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app import models  # noqa: F401  (the models must be imported to fill Base.metadata)
from app.core.config import AdminSettings
from app.db.base import Base
from tests.dbsupport import API_DIR, ScratchDb, run_alembic

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
# 11 of TASK-003 + those of 0006 (sessions, login_attempts, invitations, users self, apm_definer)
EXPECTED_POLICIES = 30
EXPECTED_FUNCTIONS = 7  # app_org, app_school, app_session_id, app_user_id + the 3 SECURITY DEFINER
EXPECTED_CATALOG = {
    "tables": EXPECTED_TABLES,
    "policies": EXPECTED_POLICIES,
    "functions": EXPECTED_FUNCTIONS,
}
CANARY_PASSWORD = "CanaryPw7Hx3Zq"  # noqa: S105  (fake, only ever used offline or in a unit test)


def _admin_engine(database: ScratchDb) -> Engine:
    return create_engine(database.admin_url)


def _catalog(database: ScratchDb) -> dict[str, Any]:
    engine = _admin_engine(database)
    try:
        with engine.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
            policies: Any = connection.execute(
                text("SELECT count(*) FROM pg_policies")
            ).scalar_one()
            functions: Any = connection.execute(
                text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public'"
                )
            ).scalar_one()
        return {"tables": tables, "policies": policies, "functions": functions}
    finally:
        engine.dispose()


def _role(admin: Engine, name: str) -> tuple[Any, ...] | None:
    with admin.connect() as connection:
        row = connection.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcanlogin, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = :n"
            ),
            {"n": name},
        ).one_or_none()
    return tuple(row) if row is not None else None


def test_upgrade_from_an_empty_database_creates_everything(scratch_db: ScratchDb) -> None:
    assert _catalog(scratch_db) == {"tables": set(), "policies": 0, "functions": 0}

    result = run_alembic(scratch_db, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert _catalog(scratch_db) == {
        "tables": EXPECTED_TABLES,
        "policies": EXPECTED_POLICIES,
        "functions": EXPECTED_FUNCTIONS,
    }
    current = run_alembic(scratch_db, "current")
    assert "0006_auth_sessions (head)" in current.stdout + current.stderr
    admin = _admin_engine(scratch_db)
    try:
        assert _role(admin, "apm_app") == (False, False, True, False, False)
        assert _role(admin, "apm_owner") == (False, False, False, False, False)
        # apm_definer: not a superuser, no BYPASSRLS, cannot log in, no CREATEROLE/CREATEDB
        assert _role(admin, "apm_definer") == (False, False, False, False, False)
    finally:
        admin.dispose()


def test_downgrade_to_base_undoes_everything_in_that_database(scratch_db: ScratchDb) -> None:
    assert run_alembic(scratch_db, "upgrade", "head").returncode == 0

    result = run_alembic(scratch_db, "downgrade", "base")

    assert result.returncode == 0, result.stderr
    assert _catalog(scratch_db) == {"tables": {"alembic_version"}, "policies": 0, "functions": 0}
    admin = _admin_engine(scratch_db)
    try:
        with admin.connect() as connection:
            # Roles are cluster-wide and may survive because the development database still uses
            # them, but they must hold no privilege left in the database that was downgraded.
            for role in ("apm_app", "apm_owner", "apm_definer"):
                if _role(admin, role) is None:
                    continue
                grants = connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM pg_namespace n, aclexplode(n.nspacl) a "
                        "         WHERE n.nspname = 'public' AND a.grantee = r.oid), "
                        "       (SELECT count(*) FROM pg_database d, aclexplode(d.datacl) a "
                        "         WHERE d.datname = current_database() AND a.grantee = r.oid), "
                        "       has_schema_privilege(r.oid, 'public', 'CREATE') "
                        "FROM pg_roles r WHERE r.rolname = :r"
                    ),
                    {"r": role},
                ).one()
                # No explicit ACL entry left on the schema or the database, and no CREATE.
                assert tuple(grants) == (0, 0, False), role
    finally:
        admin.dispose()


def test_upgrade_is_idempotent_and_repeatable(scratch_db: ScratchDb) -> None:
    assert run_alembic(scratch_db, "upgrade", "head").returncode == 0
    again = run_alembic(scratch_db, "upgrade", "head")  # already at head: a no-op
    assert again.returncode == 0, again.stderr
    assert run_alembic(scratch_db, "downgrade", "base").returncode == 0

    result = run_alembic(scratch_db, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert _catalog(scratch_db) == {
        "tables": EXPECTED_TABLES,
        "policies": EXPECTED_POLICIES,
        "functions": EXPECTED_FUNCTIONS,
    }


def test_upgrade_repairs_a_tampered_application_role(scratch_db: ScratchDb) -> None:
    """Attributes are re-asserted on every run, so a role someone loosened is fixed again."""
    assert run_alembic(scratch_db, "upgrade", "head").returncode == 0
    admin = _admin_engine(scratch_db)
    try:
        try:
            with admin.begin() as connection:
                connection.execute(text("ALTER ROLE apm_app BYPASSRLS"))
            assert _role(admin, "apm_app") == (False, True, True, False, False)

            assert run_alembic(scratch_db, "downgrade", "0001_baseline").returncode == 0
            assert run_alembic(scratch_db, "upgrade", "head").returncode == 0

            assert _role(admin, "apm_app") == (False, False, True, False, False)
        finally:
            with admin.begin() as connection:  # never leave the shared role loosened
                connection.execute(text("ALTER ROLE apm_app NOBYPASSRLS"))
    finally:
        admin.dispose()


def test_0005_closes_the_membership_hole_on_a_database_already_at_0004(
    scratch_db: ScratchDb,
) -> None:
    """A database that ran 0004 (INSERT on memberships, table-level UPDATE) is fixed by 0005."""
    assert run_alembic(scratch_db, "upgrade", "head").returncode == 0
    admin = _admin_engine(scratch_db)
    query = text(
        "SELECT has_table_privilege('apm_app', 'memberships', 'INSERT'), "
        "has_column_privilege('apm_app', 'memberships', 'user_id', 'UPDATE'), "
        "has_column_privilege('apm_app', 'schools', 'organization_id', 'UPDATE'), "
        "has_column_privilege('apm_app', 'organizations', 'id', 'UPDATE')"
    )
    try:
        with admin.connect() as connection:
            assert tuple(connection.execute(query).one()) == (False, False, False, False)

        assert run_alembic(scratch_db, "downgrade", "0004_tenancy_rls").returncode == 0
        with admin.connect() as connection:  # what 0004 left behind
            assert tuple(connection.execute(query).one()) == (True, True, True, True)

        assert run_alembic(scratch_db, "upgrade", "head").returncode == 0
        with admin.connect() as connection:
            assert tuple(connection.execute(query).one()) == (False, False, False, False)
    finally:
        admin.dispose()


def test_models_match_the_migrated_schema(migrated_scratch_db: ScratchDb) -> None:
    engine = _admin_engine(migrated_scratch_db)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": False},
            )
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert differences == []


# --- the password of apm_app never shows up --------------------------------------------------


def test_offline_sql_never_contains_the_password(scratch_db: ScratchDb) -> None:
    app_url = (
        make_url(scratch_db.app_url)
        .set(password=CANARY_PASSWORD)
        .render_as_string(hide_password=False)
    )

    result = run_alembic(scratch_db, "upgrade", "head", "--sql", app_url=app_url)

    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE organizations" in result.stdout
    assert "ALTER ROLE apm_app PASSWORD" not in result.stdout
    assert CANARY_PASSWORD not in result.stdout + result.stderr
    assert "only set when the migration runs online" in result.stdout


def test_online_run_never_prints_the_passwords_and_the_login_works(
    scratch_db: ScratchDb, admin_settings: AdminSettings
) -> None:
    app_password = make_url(admin_settings.database_url.get_secret_value()).password
    admin_password = make_url(admin_settings.database_admin_url.get_secret_value()).password
    assert app_password and admin_password

    result = run_alembic(scratch_db, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    for secret in (app_password, admin_password):
        assert secret not in output
        assert not [w for i in range(len(secret) - 7) if (w := secret[i : i + 8]) in output]
    admin = _admin_engine(scratch_db)
    app = create_engine(scratch_db.app_url)
    try:
        with admin.connect() as connection:
            stored: Any = connection.execute(
                text("SELECT rolpassword FROM pg_authid WHERE rolname = 'apm_app'")
            ).scalar_one()
        # Stored as a SCRAM verifier: the plain password was never sent to the server.
        assert stored.startswith("SCRAM-SHA-256$")
        assert app_password not in stored
        with app.connect() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == "apm_app"
    finally:
        admin.dispose()
        app.dispose()


def _load_roles_migration() -> ModuleType:
    path = API_DIR / "migrations" / "versions" / "0002_tenancy_roles.py"
    spec = importlib.util.spec_from_file_location("roles_migration_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FailingDriver:
    """Stands in for the psycopg connection and fails with a message that quotes everything."""

    def __init__(self, password: str, fail_in: str) -> None:
        self.password = password
        self.fail_in = fail_in
        self.pgconn = SimpleNamespace(encrypt_password=self._encrypt)

    def _encrypt(self, password: bytes, user: bytes, algorithm: bytes) -> bytes:
        if self.fail_in == "encrypt":
            raise RuntimeError(f"encrypt failed for {password.decode()}")
        return b"SCRAM-SHA-256$4096:c2FsdA==$c3RvcmVk:c2VydmVy"

    def execute(self, statement: object) -> None:
        raise RuntimeError(f"ALTER ROLE apm_app PASSWORD '{self.password}' failed: {statement!r}")


@pytest.mark.parametrize("fail_in", ["encrypt", "execute"])
def test_a_failure_while_setting_the_password_does_not_reveal_it(
    monkeypatch: pytest.MonkeyPatch, fail_in: str
) -> None:
    migration = _load_roles_migration()
    url = f"postgresql+psycopg://apm_app:{CANARY_PASSWORD}@db:5432/apm"
    driver = _FailingDriver(CANARY_PASSWORD, fail_in)
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(
        migration,
        "get_admin_settings",
        lambda: SimpleNamespace(database_url=SimpleNamespace(get_secret_value=lambda: url)),
    )
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(connection=SimpleNamespace(driver_connection=driver)),
        raising=False,
    )

    with pytest.raises(RuntimeError) as error:
        migration._set_app_password()

    rendered = "".join(traceback.format_exception(error.value))
    assert CANARY_PASSWORD not in rendered
    assert CANARY_PASSWORD not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


class _RecordingDriver:
    """Records what the migration sends to the server instead of sending it."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.pgconn = SimpleNamespace(encrypt_password=self._encrypt)

    @staticmethod
    def _encrypt(password: bytes, user: bytes, algorithm: bytes) -> bytes:
        assert user == b"apm_app"
        assert algorithm == b"scram-sha-256"
        return b"SCRAM-SHA-256$4096:c2FsdA==$c3RvcmVk:c2VydmVy"

    def execute(self, statement: Any) -> None:
        self.statements.append(statement.as_string())


def test_the_statement_sent_to_the_server_carries_a_verifier_never_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_roles_migration()
    url = f"postgresql+psycopg://apm_app:{CANARY_PASSWORD}@db:5432/apm"
    driver = _RecordingDriver()
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(
        migration,
        "get_admin_settings",
        lambda: SimpleNamespace(database_url=SimpleNamespace(get_secret_value=lambda: url)),
    )
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(connection=SimpleNamespace(driver_connection=driver)),
        raising=False,
    )

    migration._set_app_password()

    (statement,) = driver.statements
    assert statement.startswith('ALTER ROLE "apm_app" PASSWORD \'SCRAM-SHA-256$4096:')
    assert CANARY_PASSWORD not in statement


def test_database_url_without_a_password_is_refused_with_a_fixed_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_roles_migration()
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(
        migration,
        "get_admin_settings",
        lambda: SimpleNamespace(
            database_url=SimpleNamespace(
                get_secret_value=lambda: "postgresql+psycopg://apm_app@db/apm"
            )
        ),
    )

    with pytest.raises(RuntimeError, match="must carry the password"):
        migration._set_app_password()
