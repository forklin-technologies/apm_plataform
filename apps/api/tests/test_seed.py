"""T6: the seed refuses production and only ever creates fake, repeatable data."""

import os
import subprocess
import sys
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db.tenant import TenantContext, tenant_session
from app.models import Organization, School
from app.seed import FAKE_EMAIL_DOMAIN, MEMBERSHIPS, ORGANIZATIONS, SCHOOLS, USERS, seed
from tests.dbsupport import API_DIR, ScratchDb, run_alembic


def _run_seed(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(API_DIR)}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.seed"],
        cwd=API_DIR,
        env={**base, **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


UNREACHABLE_ADMIN = "postgresql+psycopg://apm:unreachablePw1@127.0.0.1:1/apm"
APP_URL = "postgresql+psycopg://apm_app:unreachablePw2@127.0.0.1:1/apm"


@pytest.mark.parametrize(
    "env",
    [
        {"ENV": "production"},  # nothing else set: refused before the settings are even loaded
        {"ENV": "production", "DATABASE_URL": APP_URL, "DATABASE_ADMIN_URL": UNREACHABLE_ADMIN},
        {"ENV": " Production ", "DATABASE_URL": APP_URL, "DATABASE_ADMIN_URL": UNREACHABLE_ADMIN},
    ],
    ids=["no-settings", "with-settings", "case-and-spaces"],
)
def test_seed_refuses_production_before_touching_any_database(env: dict[str, str]) -> None:
    result = _run_seed(env)

    assert result.returncode == 1
    assert "refusing to seed: ENV=production" in result.stderr
    # It never tried to connect: no connection error, no traceback.
    assert "Traceback" not in result.stderr
    assert "connect" not in result.stderr.lower()
    assert result.stdout == ""


def test_seed_outside_production_does_try_to_connect() -> None:
    """Control for the test above: only ENV=production stops it before the connection."""
    result = _run_seed(
        {"ENV": "development", "DATABASE_URL": APP_URL, "DATABASE_ADMIN_URL": UNREACHABLE_ADMIN}
    )

    assert result.returncode != 0
    assert "refusing to seed" not in result.stderr
    assert "unreachablePw1" not in result.stderr + result.stdout


def test_seed_creates_only_fake_data_and_is_repeatable(scratch_db: ScratchDb) -> None:
    assert run_alembic(scratch_db, "upgrade", "head").returncode == 0
    env = {
        "ENV": "development",
        "DATABASE_URL": scratch_db.app_url,
        "DATABASE_ADMIN_URL": scratch_db.admin_url,
    }

    first = _run_seed(env)
    second = _run_seed(env)

    assert first.returncode == 0, first.stderr
    assert f"organizations: {len(ORGANIZATIONS)} created" in first.stdout
    assert f"schools: {len(SCHOOLS)} created" in first.stdout
    assert f"users: {len(USERS)} created" in first.stdout
    assert f"memberships: {len(MEMBERSHIPS)} created" in first.stdout
    assert second.returncode == 0, second.stderr
    assert all(line.endswith(": 0 created") for line in second.stdout.splitlines())

    engine = create_engine(scratch_db.admin_url)
    try:
        with engine.connect() as connection:
            emails = [r[0] for r in connection.execute(text("SELECT email FROM users"))]
            slugs = [
                r[0]
                for r in connection.execute(
                    text("SELECT slug FROM organizations UNION ALL SELECT slug FROM schools")
                )
            ]
            hashes: Any = connection.execute(
                text("SELECT count(*) FROM users WHERE password_hash IS NOT NULL")
            ).scalar_one()
            counts: Any = {
                table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
                for table in ("organizations", "schools", "users", "memberships")
            }
    finally:
        engine.dispose()
    assert counts == {
        "organizations": len(ORGANIZATIONS),
        "schools": len(SCHOOLS),
        "users": len(USERS),
        "memberships": len(MEMBERSHIPS),
    }
    assert emails and all(e.endswith(f"@{FAKE_EMAIL_DOMAIN}") for e in emails)
    assert slugs and all(s.startswith("demo-") for s in slugs)
    assert hashes == 0  # no credential of any kind


def test_seed_function_is_idempotent_on_a_connection(migrated_scratch_db: ScratchDb) -> None:
    engine = create_engine(migrated_scratch_db.admin_url)
    try:
        with engine.begin() as connection:
            seed(connection)
            again = seed(connection)
    finally:
        engine.dispose()

    assert set(again.created.values()) == {0}


def test_seeded_tenants_are_isolated_for_the_application_role(
    migrated_scratch_db: ScratchDb,
) -> None:
    admin = create_engine(migrated_scratch_db.admin_url)
    app = create_engine(migrated_scratch_db.app_url)
    try:
        with admin.begin() as connection:
            seed(connection)
            rede: Any = connection.execute(
                text("SELECT id FROM organizations WHERE slug = 'demo-rede'")
            ).scalar_one()
        factory = sessionmaker(app)
        with tenant_session(factory, TenantContext(rede)) as session:
            organizations = {o.slug for o in session.scalars(select(Organization))}
            schools = {s.slug for s in session.scalars(select(School))}
    finally:
        admin.dispose()
        app.dispose()

    assert organizations == {"demo-rede"}
    assert schools == {"demo-aurora", "demo-horizonte"}
