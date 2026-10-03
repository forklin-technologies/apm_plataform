# ruff: noqa: E501  (SQL text)
"""F3: after a row is settled (or final) no protected column changes, by NO path.

The paths, each tried on its own:
  * apm_app: the column privileges stop what is not granted ("permission denied"), and the
    triggers stop what is ("is final");
  * the table owner with FORCE ROW LEVEL SECURITY (SET ROLE apm_owner, in a tenant context): it has
    every privilege, so only the triggers stop it;
  * the admin, a superuser: it bypasses privileges and row level security, so ONLY the triggers
    stop it (they fire for superusers too; only session_replication_role = replica, which the
    application role cannot set, skips them).
Nothing is ever deleted or truncated, and audit_logs only grows.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction
from tests.financial.conftest import Ledger
from tests.financial.test_isolation import PRIMARY_KEY, TABLES, _subject

# Columns that never change, settled or not (the trigger assert_immutable_columns).
IMMUTABLE: dict[str, list[str]] = {
    "financial_transactions": [
        "id",
        "organization_id",
        "school_id",
        "kind",
        "direction",
        "parent_transaction_id",
        "parent_kind",
        "reference_code",
        "created_at",
        "created_by_user_id",
        "origin_type",
        "origin_name",
        "origin_user_id",
    ],
    "contributions": [
        "transaction_id",
        "organization_id",
        "school_id",
        "kind",
        "method",
        "receipt_token_hash",
        "receipt_expires_at",
        "created_at",
    ],
    "expenses": [
        "transaction_id",
        "organization_id",
        "school_id",
        "kind",
        "paid_by",
        "submitted_by_user_id",
        "created_at",
    ],
    "reimbursements": [
        "transaction_id",
        "organization_id",
        "school_id",
        "kind",
        "beneficiary_user_id",
        "created_at",
    ],
    "refunds": ["transaction_id", "organization_id", "school_id", "kind", "reason", "created_at"],
    "categories": [
        "id",
        "organization_id",
        "school_id",
        "key",
        "applies_to",
        "report_group",
        "created_at",
    ],
    "school_settings": ["school_id", "organization_id", "created_at"],
    "payment_accounts": ["id", "organization_id", "school_id", "provider", "created_at"],
    "pix_charges": [
        "id",
        "organization_id",
        "school_id",
        "transaction_id",
        "provider",
        "txid",
        "amount_cents",
        "expires_at",
        "created_at",
    ],
    "webhook_events": [
        "id",
        "organization_id",
        "school_id",
        "provider",
        "idempotency_key",
        "end_to_end_id",
        "raw_payload",
        "signature_valid",
        "received_at",
    ],
    "monthly_closings": [
        "id",
        "organization_id",
        "school_id",
        "period_start",
        "period_end",
        "timezone",
        "opening_balance_cents",
        "contributions_in_cents",
        "other_in_cents",
        "refunds_in_cents",
        "total_in_cents",
        "expenses_out_cents",
        "reimbursements_out_cents",
        "total_out_cents",
        "closing_balance_cents",
        "pending_reimbursements_cents",
        "closing_after_pending_cents",
        "entries_count",
        "entries_hash",
        "breakdown",
        "closed_by_user_id",
        "closed_at",
    ],
}


def _new_value(data_type: str, column: str) -> str:
    """An expression that is always different from the current value, NULL included. It does not
    have to be valid for the CHECK constraints: the triggers fire before them."""
    if data_type == "uuid":
        return "gen_random_uuid()"
    if data_type == "boolean":
        return f"NOT {column}"
    if data_type in ("bigint", "integer"):
        return f"coalesce({column}, 0) + 1"
    if data_type.startswith("timestamp"):
        return f"coalesce({column}, now()) + interval '1 day'"
    if data_type == "date":
        return f"coalesce({column}, current_date) + 1"
    if data_type == "jsonb":
        return f"coalesce({column}, '{{}}'::jsonb) || '{{\"x\": 1}}'::jsonb"
    if data_type == "ARRAY":
        return f"coalesce({column}, ARRAY[]::text[]) || ARRAY['x']"
    return f"coalesce({column}, '') || 'x'"


def _data_types(admin_engine: Engine, table: str) -> dict[str, str]:
    with admin_engine.connect() as connection:
        return {
            row[0]: row[1]
            for row in connection.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            )
        }


def _attempt(
    engine: Engine,
    *,
    mode: str,
    tenants: Tenants,
    table: str,
    column: str,
    row_id: uuid.UUID,
    expression: str,
) -> str:
    """Run the UPDATE as `mode` (app, owner or admin) and return the error text ('' if it worked)."""
    key = PRIMARY_KEY[table]
    context = None if mode == "admin" else TenantContext(tenants.org_a)
    with transaction(engine, owner=(mode == "owner"), context=context) as connection:
        try:
            result = connection.execute(
                text(f"UPDATE {table} SET {column} = {expression} WHERE {key} = :id"),  # noqa: S608
                {"id": row_id},
            )
        except DBAPIError as error:
            return str(error.orig)
        return "" if result.rowcount else "no row"


PROTECTED = [(table, column) for table, columns in IMMUTABLE.items() for column in columns]


@pytest.mark.parametrize("mode", ["app", "owner", "admin"])
@pytest.mark.parametrize(("table", "column"), PROTECTED, ids=[f"{t}.{c}" for t, c in PROTECTED])
def test_a_protected_column_never_changes(
    app_engine: Engine,
    admin_engine: Engine,
    tenants: Tenants,
    ledger: Ledger,
    mode: str,
    table: str,
    column: str,
) -> None:
    engine = app_engine if mode == "app" else admin_engine
    expression = _new_value(_data_types(admin_engine, table)[column], column)

    error = _attempt(
        engine,
        mode=mode,
        tenants=tenants,
        table=table,
        column=column,
        row_id=_subject(ledger, table),
        expression=expression,
    )

    if mode == "app":
        assert "permission denied" in error, error
    elif table in ("expense_attachments", "audit_logs"):
        assert error  # never listed here: see the insert-only test
    else:
        # For the owner a row is visible only in its tenant context, and the trigger refuses first.
        assert "can never change" in error, error


FT_COLUMNS = [
    "id",
    "organization_id",
    "school_id",
    "kind",
    "direction",
    "amount_cents",
    "status",
    "category_id",
    "occurred_at",
    "settled_at",
    "late_adjustment",
    "parent_transaction_id",
    "parent_kind",
    "created_by_user_id",
    "reference_code",
    "created_at",
    "updated_at",
]


def _final_rows(ledger: Ledger) -> dict[str, uuid.UUID]:
    return {
        "settled cash contribution": ledger.cash_contribution,
        "settled APM expense": ledger.expense_paid,
        "cancelled contribution": ledger.cancelled_contribution,
        "rejected expense": ledger.rejected_expense,
        "rejected refund": ledger.rejected_refund,
    }


FINAL_ROW_NAMES = [
    "settled cash contribution",
    "settled APM expense",
    "cancelled contribution",
    "rejected expense",
    "rejected refund",
]


@pytest.mark.parametrize("mode", ["owner", "admin"])
@pytest.mark.parametrize("column", FT_COLUMNS)
@pytest.mark.parametrize("name", FINAL_ROW_NAMES)
def test_a_final_ledger_row_changes_in_no_column_at_all(
    admin_engine: Engine,
    tenants: Tenants,
    ledger: Ledger,
    name: str,
    column: str,
    mode: str,
) -> None:
    """Settled or terminal: not even category, date, status or updated_at (a correction is a REFUND)."""
    expression = _new_value(_data_types(admin_engine, "financial_transactions")[column], column)

    error = _attempt(
        admin_engine,
        mode=mode,
        tenants=tenants,
        table="financial_transactions",
        column=column,
        row_id=_final_rows(ledger)[name],
        expression=expression,
    )

    assert "can never change" in error or "is final" in error, error


@pytest.mark.parametrize(
    "column", ["status", "settled_at", "updated_at", "amount_cents", "category_id", "occurred_at"]
)
def test_the_application_role_cannot_change_a_final_row_either(
    app_engine: Engine, admin_engine: Engine, tenants: Tenants, ledger: Ledger, column: str
) -> None:
    """These are the columns the application may update: the trigger is what stops it."""
    expression = _new_value(_data_types(admin_engine, "financial_transactions")[column], column)
    for row in (ledger.cash_contribution, ledger.expense_paid, ledger.rejected_expense):
        error = _attempt(
            app_engine,
            mode="app",
            tenants=tenants,
            table="financial_transactions",
            column=column,
            row_id=row,
            expression=expression,
        )
        assert "is final" in error or "can never change" in error, error


def test_confirming_twice_is_safe_and_does_not_touch_the_row(
    app_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    """The compare-and-swap of ADR-015: the second confirmation matches no row."""
    with transaction(app_engine, context=TenantContext(tenants.org_a)) as connection:
        result = connection.execute(
            text(
                "UPDATE financial_transactions SET status = 'PAID', settled_at = now() "
                "WHERE id = :id AND status = 'PENDING_PAYMENT'"
            ),
            {"id": ledger.cash_contribution},  # already PAID
        )
        assert result.rowcount == 0


# --- set-once and erase-only columns ---------------------------------------------------------------

# (table, subject row, column, value to write): a column that holds a value cannot be rewritten.
SET_ONCE = [
    ("expenses", "expense_paid", "approved_by_user_id", "gen_random_uuid()"),
    ("expenses", "expense_paid", "approved_amount_cents", "approved_amount_cents + 1"),
    ("expenses", "rejected_expense", "decision_reason", "'rewritten'"),
]


@pytest.mark.parametrize("mode", ["owner", "admin"])
@pytest.mark.parametrize(("table", "subject", "column", "value"), SET_ONCE)
def test_a_set_once_column_cannot_be_rewritten(
    admin_engine: Engine,
    tenants: Tenants,
    ledger: Ledger,
    mode: str,
    table: str,
    subject: str,
    column: str,
    value: str,
) -> None:
    row_id: uuid.UUID = getattr(ledger, subject)
    error = _attempt(
        admin_engine,
        mode=mode,
        tenants=tenants,
        table=table,
        column=column,
        row_id=row_id,
        expression=value,
    )
    assert "already set" in error, error


# --- nothing is deleted, nothing is truncated, the audit log only grows ----------------------------


@pytest.mark.parametrize("table", TABLES)
def test_no_row_is_ever_deleted_by_any_path(
    app_engine: Engine, admin_engine: Engine, tenants: Tenants, ledger: Ledger, table: str
) -> None:
    key = PRIMARY_KEY[table]
    subject = _subject(ledger, table)
    delete = text(f"DELETE FROM {table} WHERE {key} = :id")  # noqa: S608

    with (
        transaction(app_engine, context=TenantContext(tenants.org_a)) as connection,
        pytest.raises(DBAPIError, match="permission denied"),
    ):
        connection.execute(delete, {"id": subject})
    with transaction(admin_engine, owner=True, context=TenantContext(tenants.org_a)) as connection:
        assert connection.execute(delete, {"id": subject}).rowcount == 0  # no DELETE policy
    with (
        transaction(admin_engine) as connection,
        pytest.raises(DBAPIError, match="can never be deleted"),
    ):
        connection.execute(delete, {"id": subject})  # the trigger, for a superuser


@pytest.mark.parametrize("cascade", ["", " CASCADE"], ids=["plain", "cascade"])
@pytest.mark.parametrize("table", TABLES)
def test_no_table_is_ever_truncated_by_any_path(
    app_engine: Engine, admin_engine: Engine, tenants: Tenants, table: str, cascade: str
) -> None:
    statement = f"TRUNCATE {table}{cascade}"
    with (
        transaction(app_engine) as connection,
        pytest.raises(DBAPIError, match="permission denied"),
    ):
        connection.exec_driver_sql(statement)
    for owner in (True, False):  # the owner has the privilege; the admin is a superuser
        with (
            transaction(admin_engine, owner=owner) as connection,
            # A table that other tables point at is refused by Postgres itself before the
            # trigger runs; every other one is refused by the trigger.
            pytest.raises(
                DBAPIError, match="can never be truncated|cannot truncate a table referenced"
            ),
        ):
            connection.exec_driver_sql(statement)


def test_audit_logs_only_accept_inserts(
    app_engine: Engine, admin_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    context = TenantContext(tenants.org_a, tenants.school_a1)
    insert = text(
        "INSERT INTO audit_logs (organization_id, school_id, action, entity_type) "
        "VALUES (:org, :school, 'test.event', 'test')"
    )
    params: dict[str, Any] = {"org": tenants.org_a, "school": tenants.school_a1}
    with transaction(app_engine, context=context) as connection:
        assert connection.execute(insert, params).rowcount == 1  # INSERT works
    update = text("UPDATE audit_logs SET action = 'x.y' WHERE id = :id")
    with (
        transaction(app_engine, context=context) as connection,
        pytest.raises(DBAPIError, match="permission denied"),
    ):
        connection.execute(update, {"id": ledger.audit_log})
    with transaction(admin_engine, owner=True, context=context) as connection:
        assert connection.execute(update, {"id": ledger.audit_log}).rowcount == 0
    with (
        transaction(admin_engine) as connection,
        pytest.raises(DBAPIError, match="can never be updated"),
    ):
        connection.execute(update, {"id": ledger.audit_log})
