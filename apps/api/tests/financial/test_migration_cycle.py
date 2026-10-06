# ruff: noqa: E501  (SQL text)
"""F1: the migration goes up and back down in a database that already has data, and repeats.

`BASE` is the revision this migration sits on (read from the migration itself, so the test keeps
working when the authentication migration is merged in front of it). The data of the tenancy tables
survives the whole cycle; the financial schema (and what was written into it) is created and
removed by the migration, and the settings of the schools that already existed are backfilled.
"""

import importlib.util
import uuid
from typing import Any

from sqlalchemy import create_engine, text

from tests.dbsupport import API_DIR, ScratchDb, run_alembic
from tests.financial.support import add_cash_contribution, utc


def _module() -> Any:
    path = API_DIR / "migrations" / "versions" / "0007_financial_schema.py"
    spec = importlib.util.spec_from_file_location("financial_migration_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE: str = _module().down_revision
HEAD_LABEL = "0009_expiry_job (head)"
FINANCIAL = {
    "audit_logs", "categories", "contributions", "expense_attachments", "expenses",
    "financial_transactions", "monthly_closings", "payment_accounts", "pix_charges", "refunds",
    "reimbursements", "school_settings", "webhook_events",
}  # fmt: skip


def _state(database: ScratchDb) -> dict[str, Any]:
    engine = create_engine(database.admin_url)
    try:
        with engine.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
            return {
                "financial_tables": tables & FINANCIAL,
                "functions": conn.execute(
                    text(
                        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
                    )
                ).scalar_one(),
                "policies": conn.execute(text("SELECT count(*) FROM pg_policies")).scalar_one(),
                "schools": conn.execute(text("SELECT count(*) FROM schools")).scalar_one(),
                "settings": (
                    conn.execute(text("SELECT count(*) FROM school_settings")).scalar_one()
                    if "school_settings" in tables
                    else None
                ),
                "memberships": conn.execute(text("SELECT count(*) FROM memberships")).scalar_one(),
            }
    finally:
        engine.dispose()


def _seed_tenancy(database: ScratchDb) -> None:
    engine = create_engine(database.admin_url)
    try:
        with engine.begin() as conn:
            org = uuid.uuid4()
            conn.execute(
                text("INSERT INTO organizations (id, name, slug) VALUES (:o, 'Cycle', :s)"),
                {"o": org, "s": f"cycle-{org.hex[:8]}"},
            )
            for i in range(3):
                school, user = uuid.uuid4(), uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO schools (id, organization_id, name, slug) VALUES (:s, :o, 'S', :slug)"
                    ),
                    {"s": school, "o": org, "slug": f"cycle-{i}-{org.hex[:8]}"},
                )
                conn.execute(
                    text("INSERT INTO users (id, email, full_name) VALUES (:u, :e, 'U')"),
                    {"u": user, "e": f"cycle-{i}-{org.hex[:8]}@example.test"},
                )
                conn.execute(
                    text(
                        "INSERT INTO memberships (user_id, organization_id, school_id, role, status) VALUES (:u, :o, :s, 'treasurer', 'active')"
                    ),
                    {"u": user, "o": org, "s": school},
                )
    finally:
        engine.dispose()


def _write_financial_data(database: ScratchDb) -> None:
    from tests.financial.support import make_school

    engine = create_engine(database.admin_url)
    try:
        with engine.begin() as conn:
            fresh = make_school(conn)
            add_cash_contribution(conn, fresh, 5000, utc(2025, 3, 5, 15))
    finally:
        engine.dispose()


def test_the_migration_cycles_in_a_database_with_data(scratch_db: ScratchDb) -> None:
    assert run_alembic(scratch_db, "upgrade", BASE).returncode == 0
    before = _state(scratch_db)
    # 7 functions at BASE: the 2 of the tenancy core, the 2 more context functions of the
    # authentication (app_session_id, app_user_id) and its 3 SECURITY DEFINER functions (0006)
    assert before["financial_tables"] == set() and before["functions"] == 7
    _seed_tenancy(scratch_db)  # data that exists BEFORE the financial schema does
    seeded = _state(scratch_db)
    assert seeded["schools"] == 3

    up = run_alembic(scratch_db, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    after_up = _state(scratch_db)
    assert after_up["financial_tables"] == FINANCIAL
    assert after_up["schools"] == 3 and after_up["memberships"] == 3  # nothing was lost
    assert after_up["settings"] == 3  # the schools that already existed got their settings
    assert HEAD_LABEL in (
        run_alembic(scratch_db, "current").stdout + run_alembic(scratch_db, "current").stderr
    )

    _write_financial_data(scratch_db)  # a ledger row, written under the new schema
    with_data = _state(scratch_db)
    assert with_data["settings"] == 4

    down = run_alembic(scratch_db, "downgrade", BASE)
    assert down.returncode == 0, down.stderr
    after_down = _state(scratch_db)
    assert after_down["financial_tables"] == set()  # gone, data included
    assert after_down["functions"] == before["functions"]  # the 7 of BASE only
    assert after_down["policies"] == before["policies"]
    assert after_down["schools"] == with_data["schools"]  # the schools of the tenancy tables stay
    assert after_down["memberships"] == with_data["memberships"]

    again = run_alembic(scratch_db, "upgrade", "head")
    assert again.returncode == 0, again.stderr
    reapplied = _state(scratch_db)
    assert reapplied["financial_tables"] == FINANCIAL
    assert reapplied["settings"] == reapplied["schools"] == 4  # backfilled again

    noop = run_alembic(scratch_db, "upgrade", "head")  # idempotent: already at head
    assert noop.returncode == 0, noop.stderr
    assert _state(scratch_db) == reapplied


def test_the_downgrade_leaves_no_privilege_of_the_application_role_behind(
    scratch_db: ScratchDb,
) -> None:
    assert run_alembic(scratch_db, "upgrade", "head").returncode == 0
    assert run_alembic(scratch_db, "downgrade", BASE).returncode == 0
    engine = create_engine(scratch_db.admin_url)
    try:
        with engine.connect() as conn:
            leftovers = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.table_privileges "
                    "WHERE grantee = 'apm_app' AND table_schema = 'public' AND table_name = ANY(:t)"
                ),
                {"t": sorted(FINANCIAL)},
            ).all()
            columns = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.column_privileges "
                    "WHERE grantee = 'apm_app' AND table_schema = 'public' AND table_name = ANY(:t)"
                ),
                {"t": sorted(FINANCIAL)},
            ).all()
    finally:
        engine.dispose()
    assert leftovers == [] and columns == []
