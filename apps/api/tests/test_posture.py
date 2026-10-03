"""N6: the posture check (startup, readiness, `python -m app.posture`).

Several tests tamper with the CONFIGURED database or with the shared application role and put it
back in a `finally`: run one test session at a time per cluster.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from app.core.config import AdminSettings, Settings
from app.db import posture
from app.db.posture import PostureError, assert_posture, cheap_posture_ok, check_posture
from app.main import create_app
from tests.dbsupport import API_DIR, ScratchDb, run_alembic
from tests.helpers import make_settings

UNREACHABLE = "postgresql+psycopg://apm_app:SECRETPW123@127.0.0.1:1/none"


@contextmanager
def tampered(admin_engine: Engine, apply: str, restore: str) -> Iterator[None]:
    """Run `apply` as the admin, and ALWAYS `restore` afterwards."""
    with admin_engine.begin() as connection:
        connection.execute(text(apply))
    try:
        yield
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(restore))


def _findings(app_engine: Engine) -> list[str]:
    with app_engine.connect() as connection:
        return check_posture(connection)


def _settings(admin_settings: AdminSettings, env: str = "development") -> Settings:
    return make_settings(env=env, database_url=admin_settings.database_url.get_secret_value())


def test_a_healthy_database_has_no_findings(app_engine: Engine) -> None:
    assert _findings(app_engine) == []
    with app_engine.connect() as connection:
        assert cheap_posture_ok(connection) is True
        assert_posture(connection)  # does not raise


@pytest.mark.parametrize(
    ("label", "apply", "restore", "expected"),
    [
        (
            "bypassrls",
            "ALTER ROLE apm_app BYPASSRLS",
            "ALTER ROLE apm_app NOBYPASSRLS",
            ["role_bypasses_rls"],
        ),
        (
            "superuser",
            "ALTER ROLE apm_app SUPERUSER",
            "ALTER ROLE apm_app NOSUPERUSER",
            # A superuser holds every privilege, TEMPORARY included.
            ["role_is_superuser", "temporary_allowed"],
        ),
        (
            "createrole",
            "ALTER ROLE apm_app CREATEROLE",
            "ALTER ROLE apm_app NOCREATEROLE",
            ["role_can_create_roles"],
        ),
        (
            "createdb",
            "ALTER ROLE apm_app CREATEDB",
            "ALTER ROLE apm_app NOCREATEDB",
            ["role_can_create_databases"],
        ),
        (
            "replication",
            "ALTER ROLE apm_app REPLICATION",
            "ALTER ROLE apm_app NOREPLICATION",
            ["role_can_replicate"],
        ),
        (
            "force",
            "ALTER TABLE schools NO FORCE ROW LEVEL SECURITY",
            "ALTER TABLE schools FORCE ROW LEVEL SECURITY",
            ["rls_not_forced:schools"],
        ),
        (
            "enable",
            "ALTER TABLE users DISABLE ROW LEVEL SECURITY",
            "ALTER TABLE users ENABLE ROW LEVEL SECURITY",
            ["rls_not_enabled:users"],
        ),
        (
            "member-of-a-predefined-role",
            "GRANT pg_read_all_data TO apm_app",
            "REVOKE pg_read_all_data FROM apm_app",
            ["role_membership:pg_read_all_data"],
        ),
        (
            "member-of-the-owner",
            "GRANT apm_owner TO apm_app",
            "REVOKE apm_owner FROM apm_app",
            ["role_membership:apm_owner"],
        ),
        (
            "policy-missing",
            "DROP POLICY schools_select ON schools",
            "CREATE POLICY schools_select ON schools FOR SELECT USING ("
            "organization_id = (SELECT public.app_org()) AND "
            "((SELECT public.app_school()) IS NULL OR id = (SELECT public.app_school())))",
            ["policy_missing:schools.schools_select"],
        ),
        (
            "policy-extra",
            "CREATE POLICY posture_extra ON schools FOR SELECT USING (true)",
            "DROP POLICY posture_extra ON schools",
            ["policy_unexpected:schools.posture_extra"],
        ),
        (
            "definer",
            "CREATE FUNCTION public.posture_probe() RETURNS int LANGUAGE sql SECURITY DEFINER "
            "AS 'SELECT 1'",
            "DROP FUNCTION public.posture_probe()",
            ["unexpected_security_definer:public.posture_probe()"],
        ),
    ],
)
def test_each_tampering_is_reported_with_a_fixed_code(
    admin_engine: Engine,
    app_engine: Engine,
    label: str,
    apply: str,
    restore: str,
    expected: list[str],
) -> None:
    with tampered(admin_engine, apply, restore):
        assert _findings(app_engine) == expected
    assert _findings(app_engine) == []  # and the restore really restored it


def test_a_table_owned_by_the_wrong_role_is_reported(scratch_db: ScratchDb) -> None:
    """Done on a scratch database, not the configured one: changing a table's owner moves (and
    drops) the grants of the role it is handed to, and nothing can put them back by hand."""
    assert run_alembic(scratch_db, "upgrade", "head").returncode == 0
    admin = create_engine(scratch_db.admin_url)
    app = create_engine(scratch_db.app_url)
    try:
        with admin.begin() as connection:
            connection.execute(text("ALTER TABLE schools OWNER TO apm_app"))
        with app.connect() as connection:
            assert check_posture(connection) == ["table_owner_unexpected:schools"]
    finally:
        admin.dispose()
        app.dispose()


def test_temporary_privilege_is_reported(admin_engine: Engine, app_engine: Engine) -> None:
    grant = "GRANT TEMPORARY ON DATABASE {db} TO PUBLIC"
    revoke = "REVOKE TEMPORARY ON DATABASE {db} FROM PUBLIC"
    with admin_engine.connect() as connection:
        database: Any = connection.execute(text("SELECT current_database()")).scalar_one()
    with tampered(
        admin_engine, grant.format(db=f'"{database}"'), revoke.format(db=f'"{database}"')
    ):
        assert _findings(app_engine) == ["temporary_allowed"]


def test_connecting_as_a_different_role_is_reported(admin_engine: Engine) -> None:
    """Pointing the API at the admin (a superuser in development) is the classic mistake."""
    with admin_engine.connect() as connection:
        findings = check_posture(connection)
    assert "not_application_role" in findings
    with admin_engine.connect() as connection:
        assert cheap_posture_ok(connection) is False


def test_the_closed_list_of_security_definer_functions_is_empty_for_now() -> None:
    assert frozenset() == posture.ALLOWED_SECURITY_DEFINER


# --- startup -----------------------------------------------------------------------------------


def test_the_api_starts_on_a_healthy_database(admin_settings: AdminSettings) -> None:
    with TestClient(create_app(_settings(admin_settings))) as client:
        assert client.get("/api/health/ready").status_code == 200


@pytest.mark.parametrize(
    ("apply", "restore", "code"),
    [
        (
            "ALTER TABLE schools NO FORCE ROW LEVEL SECURITY",
            "ALTER TABLE schools FORCE ROW LEVEL SECURITY",
            "rls_not_forced:schools",
        ),
        ("ALTER ROLE apm_app BYPASSRLS", "ALTER ROLE apm_app NOBYPASSRLS", "role_bypasses_rls"),
    ],
)
def test_the_api_refuses_to_start_on_a_tampered_database(
    admin_engine: Engine, admin_settings: AdminSettings, apply: str, restore: str, code: str
) -> None:
    app_password = admin_settings.database_url.get_secret_value().split(":")[2].split("@")[0]
    with (
        tampered(admin_engine, apply, restore),
        pytest.raises(PostureError) as error,
        TestClient(create_app(_settings(admin_settings))),
    ):
        pass

    message = str(error.value)
    assert code in message
    assert message.startswith("database posture check failed: ")
    assert app_password not in message


def test_the_check_is_skipped_only_in_env_test(
    admin_engine: Engine, admin_settings: AdminSettings
) -> None:
    apply = "ALTER TABLE schools NO FORCE ROW LEVEL SECURITY"
    with tampered(admin_engine, apply, "ALTER TABLE schools FORCE ROW LEVEL SECURITY"):
        with TestClient(create_app(_settings(admin_settings, env="test"))) as client:
            assert client.get("/api/health").status_code == 200
        with (
            pytest.raises(PostureError),
            TestClient(create_app(_settings(admin_settings, env="production"))),
        ):
            pass


def test_an_unreachable_database_fails_the_startup_with_a_fixed_message() -> None:
    settings = Settings(_env_file=None, env="development", database_url=UNREACHABLE)  # type: ignore[call-arg,arg-type]

    with pytest.raises(PostureError) as error, TestClient(create_app(settings)):
        pass

    assert str(error.value) == "database posture check could not run: the database is unreachable"
    assert error.value.__cause__ is None
    assert "SECRETPW123" not in str(error.value)


# --- readiness ---------------------------------------------------------------------------------


def test_readiness_goes_503_when_the_role_gains_bypassrls(
    admin_engine: Engine, admin_settings: AdminSettings
) -> None:
    with TestClient(create_app(_settings(admin_settings, env="test"))) as client:
        assert client.get("/api/health/ready").status_code == 200
        with tampered(
            admin_engine, "ALTER ROLE apm_app BYPASSRLS", "ALTER ROLE apm_app NOBYPASSRLS"
        ):
            response = client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
        assert client.get("/api/health/ready").status_code == 200


# --- the command -------------------------------------------------------------------------------


def _run_command() -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.posture"],
        cwd=API_DIR,
        env={**os.environ, "PYTHONPATH": str(API_DIR)},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_the_posture_command_reports_ok_and_failure(admin_engine: Engine) -> None:
    ok = _run_command()
    assert (ok.returncode, ok.stdout.strip()) == (0, "posture OK")

    with tampered(
        admin_engine,
        "ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE memberships FORCE ROW LEVEL SECURITY",
    ):
        failed = _run_command()
    assert failed.returncode == 1
    assert failed.stderr.strip() == "posture FAILED: rls_not_forced:memberships"
    assert failed.stdout == ""


def test_the_posture_command_is_quiet_about_secrets_when_the_database_is_down() -> None:
    env: dict[str, Any] = {**os.environ, "PYTHONPATH": str(API_DIR), "DATABASE_URL": UNREACHABLE}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.posture"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 1
    assert result.stderr.strip() == "posture check could not run: the database is unreachable"
    assert "SECRETPW123" not in result.stdout + result.stderr
