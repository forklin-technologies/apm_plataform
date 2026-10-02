# ruff: noqa: E501  (SQL text)
"""F8: the application role has exactly the grants documented in docs/financial-model.md.

The expected sets below are written out by hand (they are NOT read from the migration), so a grant
added by mistake, or by tampering with the database, fails here. A new table is born denied: it is
not in any of these sets until somebody adds it on purpose.
"""

from typing import Any

import pytest
from sqlalchemy import Engine, text

from tests.financial.test_isolation import TABLES

SERVER_ASSIGNED = {"id", "created_at", "updated_at"}

# Per table, the columns the application role may INSERT. Never the id, the reference code, the
# booking flag, the approval time, the actor of an audit row, nor anything the database computes.
INSERTABLE: dict[str, set[str]] = {
    "financial_transactions": {
        "organization_id",
        "school_id",
        "kind",
        "direction",
        "amount_cents",
        "status",
        "category_id",
        "occurred_at",
        "settled_at",
        "parent_transaction_id",
        "parent_kind",
        "created_by_user_id",
    },
    "contributions": {
        "transaction_id",
        "organization_id",
        "school_id",
        "method",
        "guardian_name",
        "student_name",
        "class_name",
        "receipt_token_hash",
        "receipt_expires_at",
    },
    "expenses": {
        "transaction_id",
        "organization_id",
        "school_id",
        "description",
        "vendor",
        "paid_by",
        "submitted_by_user_id",
    },
    "reimbursements": {"transaction_id", "organization_id", "school_id", "beneficiary_user_id"},
    "refunds": {"transaction_id", "organization_id", "school_id", "reason"},
    "expense_attachments": {
        "organization_id",
        "school_id",
        "transaction_id",
        "storage_key",
        "file_name",
        "content_type",
        "size_bytes",
        "sha256",
        "uploaded_by_user_id",
    },
    "categories": {"organization_id", "school_id", "key", "name", "applies_to", "is_active"},
    "school_settings": {
        "school_id",
        "organization_id",
        "timezone",
        "min_contribution_cents",
        "max_contribution_cents",
        "pix_expiration_minutes",
        "identification_mode",
        "required_fields",
        "brand_accent",
        "brand_accent_contrast",
        "approval_limit_cents",
    },
    "pix_charges": {
        "transaction_id",
        "organization_id",
        "school_id",
        "provider",
        "txid",
        "status",
        "amount_cents",
        "expires_at",
        "emv_payload",
    },
    "webhook_events": {
        "organization_id",
        "school_id",
        "provider",
        "idempotency_key",
        "end_to_end_id",
        "raw_payload",
        "signature_valid",
    },
    "audit_logs": {
        "organization_id",
        "school_id",
        "action",
        "entity_type",
        "entity_id",
        "before_data",
        "after_data",
    },
    "monthly_closings": {"organization_id", "school_id", "period_start", "closed_by_user_id"},
}

# Never id, organization_id, school_id, kind, amount_cents, direction or a parent (the M1 lesson).
UPDATABLE: dict[str, set[str]] = {
    "financial_transactions": {"status", "settled_at", "updated_at"},
    "contributions": {"guardian_name", "student_name", "class_name", "updated_at"},
    "expenses": {"description", "vendor", "approved_by_user_id", "decision_reason", "updated_at"},
    "reimbursements": {"payment_reference", "updated_at"},
    "refunds": {"payment_reference", "updated_at"},
    "expense_attachments": set(),
    "categories": {"name", "is_active", "updated_at"},
    "school_settings": {
        "timezone",
        "min_contribution_cents",
        "max_contribution_cents",
        "pix_expiration_minutes",
        "identification_mode",
        "required_fields",
        "brand_accent",
        "brand_accent_contrast",
        "approval_limit_cents",
        "updated_at",
    },
    "pix_charges": {"status", "end_to_end_id", "paid_at", "emv_payload", "updated_at"},
    "webhook_events": {"processed_at", "processing_error", "attempts"},
    "audit_logs": set(),
    "monthly_closings": {"reopened_by_user_id", "reopen_reason", "report_ref"},
}

# The only functions the application role may call besides the two context functions.
CALLABLE_FUNCTIONS = {
    "statement_entries",
    "statement_summary",
    "statement_pending",
    "closing_entries_hash",
    "verify_closing",
}


def _columns(admin_engine: Engine, table: str) -> list[str]:
    with admin_engine.connect() as connection:
        return [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"
                ),
                {"t": table},
            )
        ]


def _effective(admin_engine: Engine, table: str, privilege: str) -> set[str]:
    with admin_engine.connect() as connection:
        return {
            column
            for column in _columns(admin_engine, table)
            if connection.execute(
                text("SELECT has_column_privilege('apm_app', :t, :c, :p)"),
                {"t": table, "c": column, "p": privilege},
            ).scalar_one()
        }


def test_the_expectations_cover_exactly_the_financial_tables() -> None:
    assert set(INSERTABLE) == set(UPDATABLE) == set(TABLES)


@pytest.mark.parametrize("table", TABLES)
def test_select_is_granted_on_every_column(admin_engine: Engine, table: str) -> None:
    assert _effective(admin_engine, table, "SELECT") == set(_columns(admin_engine, table))


@pytest.mark.parametrize("table", TABLES)
def test_insert_is_granted_on_exactly_these_columns(admin_engine: Engine, table: str) -> None:
    assert _effective(admin_engine, table, "INSERT") == INSERTABLE[table]


@pytest.mark.parametrize("table", TABLES)
def test_update_is_granted_on_exactly_these_columns(admin_engine: Engine, table: str) -> None:
    assert _effective(admin_engine, table, "UPDATE") == UPDATABLE[table]


@pytest.mark.parametrize("table", TABLES)
def test_no_other_privilege_on_a_financial_table(admin_engine: Engine, table: str) -> None:
    """No DELETE, TRUNCATE, REFERENCES or TRIGGER, and the only table-level grant is SELECT."""
    with admin_engine.connect() as connection:
        others = {
            privilege
            for privilege in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
            if connection.execute(
                text("SELECT has_table_privilege('apm_app', :t, :p)"), {"t": table, "p": privilege}
            ).scalar_one()
        }
        table_level = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT privilege_type FROM information_schema.table_privileges "
                    "WHERE grantee = 'apm_app' AND table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            )
        }
    assert others == set()
    assert table_level == {"SELECT"}


def test_no_column_with_a_server_assigned_value_is_insertable(admin_engine: Engine) -> None:
    for table, columns in INSERTABLE.items():
        assert not (columns & SERVER_ASSIGNED), table
    assert "reference_code" not in INSERTABLE["financial_transactions"]
    assert "late_adjustment" not in INSERTABLE["financial_transactions"]


def test_only_the_statement_functions_are_callable_by_the_application_role(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as connection:
        callable_by_app: set[str] = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' "
                    "AND has_function_privilege('apm_app', p.oid, 'EXECUTE')"
                )
            )
        }
        public_acl: Any = connection.execute(
            text(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, "
                "aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
                "WHERE n.nspname = 'public' AND a.grantee = 0"
            )
        ).all()
    assert callable_by_app == CALLABLE_FUNCTIONS | {"app_org", "app_school"}
    assert public_acl == []  # nothing is executable by PUBLIC


def test_the_application_role_cannot_switch_the_triggers_off(app_engine: Engine) -> None:
    """The guards are triggers: session_replication_role = replica (which skips them) needs a
    superuser, and ALTER TABLE ... DISABLE TRIGGER needs ownership."""
    from sqlalchemy.exc import DBAPIError

    from tests.dbsupport import transaction

    for statement in (
        "SET session_replication_role = replica",
        "ALTER TABLE financial_transactions DISABLE TRIGGER ALL",
        "ALTER TABLE financial_transactions DISABLE TRIGGER ft_05_immutable",
        "DROP TRIGGER ft_05_immutable ON financial_transactions",
        "ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_90_no_update",
        "DROP FUNCTION public.assert_immutable_columns()",
        "CREATE OR REPLACE FUNCTION public.forbid_delete() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN OLD; END'",
    ):
        with transaction(app_engine) as connection, pytest.raises(DBAPIError):
            connection.exec_driver_sql(statement)
