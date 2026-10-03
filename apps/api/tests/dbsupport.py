"""Helpers for the tests that need a real Postgres: tenant fixtures, scratch databases, Alembic."""

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.db.tenant import TenantContext, apply_tenant_context

API_DIR = Path(__file__).resolve().parents[1]


def with_database(url: str, database: str) -> str:
    return make_url(url).set(database=database).render_as_string(hide_password=False)


@dataclass(frozen=True)
class Tenants:
    """Two organizations and a handful of rows in each, created fresh for a test session."""

    org_a: uuid.UUID
    org_b: uuid.UUID
    school_a1: uuid.UUID
    school_a2: uuid.UUID
    school_b1: uuid.UUID
    user_a1: uuid.UUID  # staff of school A1
    user_a2: uuid.UUID  # viewer of school A2
    user_admin_a: uuid.UUID  # organization_admin of A (whole organization)
    user_b1: uuid.UUID  # staff of school B1
    user_free: uuid.UUID  # no membership anywhere
    membership_a1: uuid.UUID
    membership_a2: uuid.UUID
    membership_admin_a: uuid.UUID
    membership_b1: uuid.UUID
    slugs: tuple[str, ...]
    emails: tuple[str, ...]


def create_tenants(connection: Connection) -> Tenants:
    """Insert the fixture rows as the admin (a superuser in development, so RLS is bypassed)."""
    suffix = uuid.uuid4().hex[:10]
    ids = {
        name: uuid.uuid4() for name in Tenants.__annotations__ if name not in ("slugs", "emails")
    }
    slugs = tuple(f"t3-{name}-{suffix}" for name in ("org-a", "org-b", "s-a1", "s-a2", "s-b1"))
    emails = tuple(
        f"t3-{name}-{suffix}@example.test" for name in ("a1", "a2", "admin-a", "b1", "free")
    )
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, slug) "
            "VALUES (:a, 'Org A', :sa), (:b, 'Org B', :sb)"
        ),
        {"a": ids["org_a"], "b": ids["org_b"], "sa": slugs[0], "sb": slugs[1]},
    )
    connection.execute(
        text(
            "INSERT INTO schools (id, organization_id, name, slug) VALUES "
            "(:a1, :a, 'School A1', :s1), (:a2, :a, 'School A2', :s2), (:b1, :b, 'School B1', :s3)"
        ),
        {
            "a": ids["org_a"],
            "b": ids["org_b"],
            "a1": ids["school_a1"],
            "a2": ids["school_a2"],
            "b1": ids["school_b1"],
            "s1": slugs[2],
            "s2": slugs[3],
            "s3": slugs[4],
        },
    )
    user_keys = ("user_a1", "user_a2", "user_admin_a", "user_b1", "user_free")
    for key, email in zip(user_keys, emails, strict=True):
        connection.execute(
            text("INSERT INTO users (id, email, full_name) VALUES (:id, :email, 'Test User')"),
            {"id": ids[key], "email": email},
        )
    for key, user, org, school, role in (
        ("membership_a1", "user_a1", "org_a", "school_a1", "staff"),
        ("membership_a2", "user_a2", "org_a", "school_a2", "viewer"),
        ("membership_admin_a", "user_admin_a", "org_a", None, "organization_admin"),
        ("membership_b1", "user_b1", "org_b", "school_b1", "staff"),
    ):
        connection.execute(
            text(
                "INSERT INTO memberships (id, user_id, organization_id, school_id, role, status) "
                "VALUES (:id, :user, :org, :school, :role, 'active')"
            ),
            {
                "id": ids[key],
                "user": ids[user],
                "org": ids[org],
                "school": ids[school] if school else None,
                "role": role,
            },
        )
    return Tenants(slugs=slugs, emails=emails, **ids)


def delete_tenants(connection: Connection, tenants: Tenants) -> None:
    """Remove exactly the rows create_tenants made (and anything a test left under them)."""
    org_ids = [tenants.org_a, tenants.org_b]
    connection.execute(
        text("DELETE FROM memberships WHERE organization_id = ANY(:o)"), {"o": org_ids}
    )
    connection.execute(
        text("DELETE FROM users WHERE email = ANY(:e) OR id = ANY(:u)"),
        {"e": list(tenants.emails), "u": [tenants.user_a1, tenants.user_free]},
    )
    connection.execute(text("DELETE FROM schools WHERE organization_id = ANY(:o)"), {"o": org_ids})
    connection.execute(text("DELETE FROM organizations WHERE id = ANY(:o)"), {"o": org_ids})


@contextmanager
def transaction(
    engine: Engine, *, owner: bool = False, context: TenantContext | None = None
) -> Iterator[Connection]:
    """One transaction that is always rolled back, optionally acting as apm_owner and in a tenant.

    `owner=True` runs on the admin engine and does SET LOCAL ROLE apm_owner, which drops the
    admin's superuser bypass: FORCE ROW LEVEL SECURITY is then what protects the owner's tables.
    """
    connection = engine.connect()
    outer = connection.begin()
    try:
        if owner:
            connection.exec_driver_sql("SET LOCAL ROLE apm_owner")
        if context is not None:
            apply_tenant_context(connection, context)
        yield connection
    finally:
        outer.rollback()
        connection.close()


@dataclass(frozen=True)
class ScratchDb:
    name: str
    admin_url: str
    app_url: str


@contextmanager
def scratch_database(admin_url: str, app_url: str) -> Iterator[ScratchDb]:
    """A throwaway, empty database in the same cluster (roles are cluster-wide, tables are not)."""
    name = f"apm_test_{uuid.uuid4().hex[:12]}"
    maintenance = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with maintenance.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        yield ScratchDb(name, with_database(admin_url, name), with_database(app_url, name))
    finally:
        with maintenance.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        maintenance.dispose()


def run_alembic(
    database: ScratchDb, *args: str, app_url: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run `alembic <args>` in a real subprocess against a scratch database."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(API_DIR),
        "ENV": "test",
        "DATABASE_URL": app_url or database.app_url,
        "DATABASE_ADMIN_URL": database.admin_url,
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
