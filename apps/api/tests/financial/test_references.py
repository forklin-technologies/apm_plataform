# ruff: noqa: E501  (SQL text)
"""F2, the class of M1: no foreign key lets a row reference, or reveal, data of another school.

A foreign key is checked by the database WITHOUT row level security, so a single-column foreign key
to a tenant table answers "does this id exist anywhere?" to whoever tries to write it. Two kinds of
test keep that from coming back:

  * Catalog tests: every foreign key between the financial tables is composite (carries the
    organization and the school); the only single-column ones go to organizations (pinned by the
    WITH CHECK of the policy) and to users (guarded by a trigger that refuses first).
  * Behaviour tests: for every reference, pointing at a row that EXISTS in another tenant and at an
    id that does not exist at all give the SAME error. Each case has a positive control (the same
    statement with a valid reference succeeds), so the equality is never between two broken
    statements.
"""

import re
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction
from tests.financial.conftest import Ledger
from tests.financial.support import token_hash
from tests.financial.test_isolation import TABLES, insert_statement

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

FK_QUERY = text(
    """
    SELECT c.conname, child.relname, parent.relname,
           (SELECT array_agg(a.attname ORDER BY k.ord)
            FROM unnest(c.conkey) WITH ORDINALITY k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum)
    FROM pg_constraint c
    JOIN pg_class child ON child.oid = c.conrelid
    JOIN pg_class parent ON parent.oid = c.confrelid
    WHERE c.contype = 'f' AND child.relname = ANY(:tables)
    ORDER BY child.relname, c.conname
    """
)


def _foreign_keys(admin_engine: Engine) -> list[tuple[str, str, str, list[str]]]:
    with admin_engine.connect() as connection:
        return [tuple(row) for row in connection.execute(FK_QUERY, {"tables": TABLES})]  # type: ignore[misc, unused-ignore]


def test_there_is_no_single_column_foreign_key_to_a_tenant_table(admin_engine: Engine) -> None:
    violations = []
    for name, child, parent, columns in _foreign_keys(admin_engine):
        if parent in ("organizations", "users"):
            assert len(columns) == 1, name  # the two cases handled by the tests below
            continue
        # Everything else is a tenant table: the key must carry the organization and the school.
        has_school = "school_id" in columns or parent == "schools"
        if "organization_id" not in columns or not has_school or len(columns) < 2:
            violations.append((name, child, parent, columns))
    assert violations == []


def test_the_expected_foreign_keys_exist(admin_engine: Engine) -> None:
    """Guards the guard: if the catalog query found nothing, the test above would pass for nothing."""
    foreign_keys = {name for name, *_ in _foreign_keys(admin_engine)}
    assert {
        "fk_financial_transactions_parent",
        "fk_financial_transactions_category",
        "fk_contributions_transaction_id_financial_transactions",
        "fk_expenses_transaction_id_financial_transactions",
        "fk_reimbursements_transaction_id_financial_transactions",
        "fk_refunds_transaction_id_financial_transactions",
        "fk_expense_attachments_transaction_id_expenses",
        "fk_pix_charges_transaction_id_contributions",
    } <= foreign_keys
    assert len(foreign_keys) >= 40


def test_every_foreign_key_to_organizations_is_the_organization_id_column(
    admin_engine: Engine,
) -> None:
    for name, _child, parent, columns in _foreign_keys(admin_engine):
        if parent == "organizations":
            assert columns == ["organization_id"], name


def test_every_foreign_key_to_users_is_guarded_by_the_membership_trigger(
    admin_engine: Engine,
) -> None:
    """A BEFORE INSERT (and BEFORE UPDATE when the application may update the column) row trigger
    that calls assert_active_member with that column, so it fails before the foreign key does."""
    with admin_engine.connect() as connection:
        triggers = connection.execute(
            text(
                "SELECT t.tgrelid::regclass::text, t.tgargs, t.tgtype, t.tgenabled "
                "FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE NOT t.tgisinternal AND p.proname = 'assert_active_member'"
            )
        ).all()
        guarded: dict[tuple[str, str], tuple[bool, bool]] = {}
        for relation, args, trigger_type, enabled in triggers:
            assert enabled == "O"
            assert trigger_type & 1 and trigger_type & 2  # ROW, BEFORE
            for column in bytes(args).decode().split("\0"):
                guarded[(relation, column)] = (bool(trigger_type & 4), bool(trigger_type & 16))
        user_columns = [
            (child, columns[0])
            for _name, child, parent, columns in _foreign_keys(admin_engine)
            if parent == "users"
        ]
        assert len(user_columns) >= 8
        for child, column in user_columns:
            assert (child, column) in guarded, f"{child}.{column} is not guarded"
            on_insert, on_update = guarded[(child, column)]
            assert on_insert, f"{child}.{column} is not guarded on INSERT"
            can_update: bool = connection.execute(
                text("SELECT has_column_privilege('apm_app', :t, :c, 'UPDATE')"),
                {"t": child, "c": column},
            ).scalar_one()
            assert on_update or not can_update, f"{child}.{column} is updatable but not guarded"


UNIQUE_QUERY = text(
    """
    SELECT i.relname, ic.relname, array_agg(a.attname ORDER BY k.ord)
    FROM pg_index x
    JOIN pg_class ic ON ic.oid = x.indexrelid
    JOIN pg_class i ON i.oid = x.indrelid
    CROSS JOIN LATERAL unnest(x.indkey::int2[]) WITH ORDINALITY k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = i.oid AND a.attnum = k.attnum
    WHERE x.indisunique AND i.relname = ANY(:tables)
    GROUP BY i.relname, ic.relname
    ORDER BY 1, 2
    """
)

# The one unique index the application can feed that does NOT carry the school, and why.
GLOBAL_UNIQUE_ALLOWED = {
    "uq_payment_accounts_webhook_secret_hash": (
        "the SHA-256 of a 128-bit webhook secret: a clash reveals nothing, and resolve_webhook_target "
        "(ADR-016) must look the account up without knowing its tenant"
    ),
    "uq_contributions_receipt_token_hash": (
        "the hash of a 128-bit secret: a clash reveals nothing, and resolve_receipt (ADR-016) must "
        "look a receipt up without knowing its tenant"
    ),
}


def test_every_unique_index_the_application_can_feed_carries_the_school(
    admin_engine: Engine,
) -> None:
    """An index that is unique across tenants answers "this value exists somewhere" (a duplicate
    key) to whoever writes it. So every unique index that has a column the application may INSERT
    must include school_id; indexes made only of server-assigned columns (ids, reference codes)
    cannot be fed. The primary key of a detail row is (transaction_id, organization_id, school_id)
    for exactly this reason."""
    violations = []
    indexes = 0
    with admin_engine.connect() as connection:
        for table, index, columns in connection.execute(UNIQUE_QUERY, {"tables": TABLES}).all():
            indexes += 1
            feedable = [
                column
                for column in columns
                if connection.execute(
                    text("SELECT has_column_privilege('apm_app', :t, :c, 'INSERT')"),
                    {"t": table, "c": column},
                ).scalar_one()
            ]
            if feedable and "school_id" not in columns and index not in GLOBAL_UNIQUE_ALLOWED:
                violations.append((table, index, columns))
    assert indexes >= 25  # the query found the indexes
    assert violations == []


# --- behaviour: a foreign id and a missing id fail the same way ------------------------------------

Case = tuple[
    str, str, Callable[[Ledger, Tenants, uuid.UUID], dict[str, Any]], Callable[[Ledger], Any]
]


def _a1_insert(ledger: Ledger, tenants: Tenants, ref: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {"org": tenants.org_a, "school": tenants.school_a1, "ref": ref, **extra}


# (name, sql with :ref, parameters, the VALID reference that must make the same statement succeed,
#  the reference that exists in another tenant)
CASES: list[
    tuple[str, str, Callable[[Ledger], uuid.UUID], Callable[[Ledger, Tenants], uuid.UUID]]
] = [
    (
        "ft.parent_transaction_id",
        "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, amount_cents, "
        "status, category_id, origin_type, created_by_user_id, parent_transaction_id, parent_kind) "
        "VALUES (:org, :school, 'REFUND', 'IN', 100, 'REQUESTED', :refund_category, 'TEACHER', :staff, "
        ":ref, 'EXPENSE')",
        lambda ledger: ledger.expense_paid,
        lambda ledger, tenants: ledger.school_b1_transaction,
    ),
    (
        "ft.category_id",
        "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, amount_cents, "
        "status, category_id, origin_type) "
        "VALUES (:org, :school, 'CONTRIBUTION', 'IN', 100, 'PENDING_PAYMENT', :ref, 'GUARDIAN')",
        lambda ledger: ledger.category,
        lambda ledger, tenants: ledger.school_b1_category,
    ),
    (
        "ft.created_by_user_id",
        "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, amount_cents, "
        "status, category_id, origin_type, created_by_user_id) "
        "VALUES (:org, :school, 'CONTRIBUTION', 'IN', 100, 'PENDING_PAYMENT', :category, 'GUARDIAN', :ref)",
        lambda ledger: ledger.fresh.staff,
        lambda ledger, tenants: tenants.user_b1,
    ),
    (
        "contributions.transaction_id",
        "INSERT INTO contributions (transaction_id, organization_id, school_id, method, receipt_token_hash, "
        "receipt_expires_at) VALUES (:ref, :org, :school, 'PIX', :hash, now() + interval '1 day')",
        lambda ledger: ledger.bare_contribution,
        lambda ledger, tenants: ledger.school_b1_transaction,
    ),
    (
        "expenses.transaction_id",
        "INSERT INTO expenses (transaction_id, organization_id, school_id, description, paid_by, "
        "submitted_by_user_id) VALUES (:ref, :org, :school, 'Papel', 'APM', :staff)",
        lambda ledger: ledger.bare_expense,
        lambda ledger, tenants: ledger.school_b1_transaction,
    ),
    (
        "expenses.submitted_by_user_id",
        "INSERT INTO expenses (transaction_id, organization_id, school_id, description, paid_by, "
        "submitted_by_user_id) VALUES (:bare_expense, :org, :school, 'Papel', 'APM', :ref)",
        lambda ledger: ledger.fresh.staff,
        lambda ledger, tenants: tenants.user_b1,
    ),
    (
        "reimbursements.transaction_id",
        "INSERT INTO reimbursements (transaction_id, organization_id, school_id, beneficiary_user_id) "
        "VALUES (:ref, :org, :school, :staff)",
        lambda ledger: ledger.bare_reimbursement,
        lambda ledger, tenants: ledger.school_b1_transaction,
    ),
    (
        "reimbursements.beneficiary_user_id",
        "INSERT INTO reimbursements (transaction_id, organization_id, school_id, beneficiary_user_id) "
        "VALUES (:bare_reimbursement, :org, :school, :ref)",
        lambda ledger: ledger.fresh.staff,
        lambda ledger, tenants: tenants.user_b1,
    ),
    (
        "refunds.transaction_id",
        "INSERT INTO refunds (transaction_id, organization_id, school_id, reason) "
        "VALUES (:ref, :org, :school, 'duplicate payment')",
        lambda ledger: ledger.bare_refund,
        lambda ledger, tenants: ledger.school_b1_transaction,
    ),
    (
        "expense_attachments.transaction_id",
        "INSERT INTO expense_attachments (organization_id, school_id, transaction_id, kind, storage_key, "
        "file_name, content_type, size_bytes, sha256, uploaded_by_user_id) "
        "VALUES (:org, :school, :ref, 'INVOICE', 'k/x', 'a.pdf', 'application/pdf', 10, :hash, :staff)",
        lambda ledger: ledger.expense_submitted,
        lambda ledger, tenants: ledger.school_b1_transaction,
    ),
    (
        "expense_attachments.uploaded_by_user_id",
        "INSERT INTO expense_attachments (organization_id, school_id, transaction_id, kind, storage_key, "
        "file_name, content_type, size_bytes, sha256, uploaded_by_user_id) "
        "VALUES (:org, :school, :expense_submitted, 'INVOICE', 'k/x', 'a.pdf', 'application/pdf', 10, :hash, :ref)",
        lambda ledger: ledger.fresh.staff,
        lambda ledger, tenants: tenants.user_b1,
    ),
    (
        "pix_charges.transaction_id",
        "INSERT INTO pix_charges (organization_id, school_id, transaction_id, payment_account_id, "
        "provider, txid, amount_cents, expires_at) VALUES (:org, :school, :ref, :account, 'SANDBOX', "
        ":txid, 3100, now() + interval '30 minutes')",
        lambda ledger: ledger.pix_pending_2,
        lambda ledger, tenants: ledger.school_b1_transaction,
    ),
    (
        "pix_charges.payment_account_id",
        "INSERT INTO pix_charges (organization_id, school_id, transaction_id, payment_account_id, "
        "provider, txid, amount_cents, expires_at) VALUES (:org, :school, :pix_pending_2, :ref, "
        "'SANDBOX', :txid, 3100, now() + interval '30 minutes')",
        lambda ledger: ledger.payment_account,
        lambda ledger, tenants: ledger.school_b1_account,
    ),
    (
        "ft.origin_user_id",
        "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, amount_cents, "
        "status, category_id, origin_type, origin_user_id) "
        "VALUES (:org, :school, 'CONTRIBUTION', 'IN', 100, 'PENDING_PAYMENT', :category, 'GUARDIAN', :ref)",
        lambda ledger: ledger.fresh.staff,
        lambda ledger, tenants: tenants.user_b1,
    ),
    (
        "monthly_closings.closed_by_user_id",
        "INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id) "
        "VALUES (:org, :school, (SELECT period_start + interval '1 month' FROM monthly_closings "
        "WHERE id = :closing), :ref)",
        lambda ledger: ledger.fresh.staff,
        lambda ledger, tenants: tenants.user_b1,
    ),
    (
        "ft.school_id",
        "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, amount_cents, "
        "status, category_id, origin_type) "
        "VALUES (:org, :ref, 'CONTRIBUTION', 'IN', 100, 'PENDING_PAYMENT', :category, 'GUARDIAN')",
        lambda ledger: ledger.fresh.school,
        lambda ledger, tenants: tenants.school_b1,
    ),
    (
        "ft.organization_id",
        "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, amount_cents, "
        "status, category_id, origin_type) "
        "VALUES (:ref, :school, 'CONTRIBUTION', 'IN', 100, 'PENDING_PAYMENT', :category, 'GUARDIAN')",
        lambda ledger: ledger.fresh.org,
        lambda ledger, tenants: tenants.org_b,
    ),
]


def _parameters(ledger: Ledger, tenants: Tenants, ref: uuid.UUID) -> dict[str, Any]:
    return {
        "org": tenants.org_a,
        "school": tenants.school_a1,
        "ref": ref,
        "staff": ledger.fresh.staff,
        "hash": token_hash(),
        "txid": uuid.uuid4().hex,
        "closing": ledger.closing,
        "category": ledger.fresh.cat_in,
        "refund_category": ledger.fresh.cat_refund,
        "account": ledger.payment_account,
        "pix_pending_2": ledger.pix_pending_2,
        "bare_expense": ledger.bare_expense,
        "bare_reimbursement": ledger.bare_reimbursement,
        "expense_submitted": ledger.expense_submitted,
    }


def _outcome(engine: Engine, sql: str, params: dict[str, Any]) -> tuple[str, str] | None:
    """None when the statement succeeds, else (sqlstate, normalised message and detail)."""
    org = params["org"]
    with transaction(engine, context=TenantContext(org)) as connection:
        try:
            connection.execute(text(sql), params)
        except DBAPIError as error:
            diag = error.orig.diag  # type: ignore[union-attr]
            sqlstate = str(error.orig.sqlstate)  # type: ignore[union-attr]
            parts = [
                diag.message_primary,
                diag.constraint_name,
                diag.table_name,
                diag.message_detail,
            ]
            return sqlstate, UUID_RE.sub("<uuid>", " | ".join(str(p) for p in parts))
        return None


@pytest.mark.parametrize(("name", "sql", "valid", "foreign"), CASES, ids=[c[0] for c in CASES])
def test_a_foreign_reference_and_a_missing_one_fail_the_same_way(
    app_engine: Engine,
    tenants: Tenants,
    ledger: Ledger,
    name: str,
    sql: str,
    valid: Callable[[Ledger], uuid.UUID],
    foreign: Callable[[Ledger, Tenants], uuid.UUID],
) -> None:
    # The control: with a VALID reference the very same statement succeeds.
    assert _outcome(app_engine, sql, _parameters(ledger, tenants, valid(ledger))) is None, name

    other_tenant = _outcome(app_engine, sql, _parameters(ledger, tenants, foreign(ledger, tenants)))
    missing = _outcome(app_engine, sql, _parameters(ledger, tenants, uuid.uuid4()))

    assert other_tenant is not None, f"{name}: a row of another tenant was accepted"
    assert missing is not None
    assert other_tenant == missing, f"{name}: the error tells an existing id from a missing one"


def test_a_user_that_exists_but_has_no_membership_gets_the_same_error_as_a_missing_one(
    app_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    sql = CASES[2][1]  # ft.created_by_user_id
    free_user = _outcome(app_engine, sql, _parameters(ledger, tenants, tenants.user_free))
    missing = _outcome(app_engine, sql, _parameters(ledger, tenants, uuid.uuid4()))
    other_school = _outcome(app_engine, sql, _parameters(ledger, tenants, tenants.user_a2))

    assert free_user is not None
    assert free_user == missing == other_school  # user_a2 is an active member, of ANOTHER school


@pytest.mark.parametrize("table", TABLES)
def test_a_school_of_another_organization_and_a_missing_school_fail_the_same_way(
    app_engine: Engine, tenants: Tenants, ledger: Ledger, table: str
) -> None:
    """The regression of the unique-index oracle: with the school of another organization the write
    used to reach a unique index (duplicate key) before the foreign key, which told the school had
    data. The WITH CHECK now refuses it first, with the same error as for a school that is not there."""
    outcomes = []
    for school in (tenants.school_b1, uuid.uuid4()):
        sql, params, _mode = insert_statement(table, ledger, tenants.org_a, school)
        outcomes.append(_outcome(app_engine, sql, {**params, "org": tenants.org_a}))
    assert outcomes[0] is not None
    assert outcomes[0] == outcomes[1], table


def test_the_actor_of_an_audit_row_cannot_be_used_to_probe_users(
    app_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    """The actor comes from the setting app.user_id (not from a column); forging it with the id of
    a user of another tenant fails like forging it with an id that does not exist."""
    sql = (
        "SELECT set_config('app.user_id', :ref, true), set_config('app.actor_type', 'USER', true); "
    )
    insert = (
        "INSERT INTO audit_logs (organization_id, school_id, action, entity_type) "
        "VALUES (:org, :school, 'test.event', 'test')"
    )
    results: list[str | None] = []
    for ref in (tenants.user_b1, uuid.uuid4(), ledger.fresh.staff):
        with transaction(app_engine, context=TenantContext(tenants.org_a)) as connection:
            connection.execute(text(sql), {"ref": str(ref)})
            try:
                connection.execute(
                    text(insert), {"org": tenants.org_a, "school": tenants.school_a1}
                )
                results.append(None)
            except DBAPIError as error:
                results.append(UUID_RE.sub("<uuid>", str(error.orig)))
    assert results[0] is not None
    assert results[0] == results[1]
    assert results[2] is None  # a real active member of the school is accepted
