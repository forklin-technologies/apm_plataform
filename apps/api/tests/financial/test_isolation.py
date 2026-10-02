# ruff: noqa: E501  (SQL text)
"""F2: the isolation matrix for the financial tables.

Same shape as the matrix of the tenancy tables (tests/test_isolation_matrix.py): for every table,
every operation and every context, the expected outcome is data, checked against the real database
twice, as the application role (apm_app) and as the table owner (apm_owner, SET ROLE, which proves
FORCE ROW LEVEL SECURITY and the triggers). The subject rows belong to organization A, school A1.

Contexts: own_org (whole organization A), own_school (A1), same_org_other_school (A2),
other_org (B), no_context (fails closed).
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction
from tests.financial.conftest import Ledger
from tests.financial.support import token_hash

CONTEXTS = ["own_org", "own_school", "same_org_other_school", "other_org", "no_context"]
TABLES = [
    "financial_transactions",
    "contributions",
    "expenses",
    "reimbursements",
    "refunds",
    "expense_attachments",
    "categories",
    "school_settings",
    "pix_charges",
    "webhook_events",
    "audit_logs",
    "monthly_closings",
]
OPERATIONS = ["select", "insert", "update", "delete"]
SCHOOL_SCOPED = {"own_org", "own_school"}
NOBODY: set[str] = set()

# Tables nobody can update: the application role has no UPDATE privilege and the owner has no
# UPDATE policy (and a trigger refuses it as well).
NEVER_UPDATED = {"expense_attachments", "audit_logs"}

EXPECTED: dict[tuple[str, str], set[str]] = {}
for _table in TABLES:
    EXPECTED[(_table, "select")] = SCHOOL_SCOPED
    EXPECTED[(_table, "insert")] = SCHOOL_SCOPED
    EXPECTED[(_table, "update")] = NOBODY if _table in NEVER_UPDATED else SCHOOL_SCOPED
    EXPECTED[(_table, "delete")] = NOBODY  # no DELETE grant, no DELETE policy, a trigger refuses it

PRIMARY_KEY = {
    "financial_transactions": "id",
    "contributions": "transaction_id",
    "expenses": "transaction_id",
    "reimbursements": "transaction_id",
    "refunds": "transaction_id",
    "expense_attachments": "id",
    "categories": "id",
    "school_settings": "school_id",
    "pix_charges": "id",
    "webhook_events": "id",
    "audit_logs": "id",
    "monthly_closings": "id",
}

Statement = tuple[str, dict[str, Any], str]  # sql, parameters, "count" | "rowcount"


def _subject(ledger: Ledger, table: str) -> uuid.UUID:
    return {
        "financial_transactions": ledger.cash_contribution,
        "contributions": ledger.cash_contribution,
        "expenses": ledger.expense_paid,
        "reimbursements": ledger.reimbursement_pending,
        "refunds": ledger.refund_pending,
        "expense_attachments": ledger.attachment,
        "categories": ledger.category,
        "school_settings": ledger.fresh.school,
        "pix_charges": ledger.pix_charge,
        "webhook_events": ledger.webhook_event,
        "audit_logs": ledger.audit_log,
        "monthly_closings": ledger.closing,
    }[table]


def _fresh() -> str:
    return uuid.uuid4().hex[:12]


def insert_statement(table: str, ledger: Ledger, org: uuid.UUID, school: uuid.UUID) -> Statement:
    """A valid INSERT into `table` for the given tenant. Everything it points at is in A1, so for
    another tenant the only things that can let it through are broken policies or foreign keys."""
    a1 = ledger.fresh
    base = {"org": org, "school": school}
    statements: dict[str, Statement] = {
        "financial_transactions": (
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
            "amount_cents, status) VALUES (:org, :school, 'CONTRIBUTION', 'IN', 1000, 'PENDING_PAYMENT')",
            base,
            "rowcount",
        ),
        "contributions": (
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
            "receipt_token_hash, receipt_expires_at) "
            "VALUES (:tx, :org, :school, 'PIX', :hash, now() + interval '1 day')",
            {**base, "tx": ledger.bare_contribution, "hash": token_hash()},
            "rowcount",
        ),
        "expenses": (
            "INSERT INTO expenses (transaction_id, organization_id, school_id, description, paid_by, "
            "submitted_by_user_id) VALUES (:tx, :org, :school, 'Papel', 'APM', :staff)",
            {**base, "tx": ledger.bare_expense, "staff": a1.staff},
            "rowcount",
        ),
        "reimbursements": (
            "INSERT INTO reimbursements (transaction_id, organization_id, school_id, "
            "beneficiary_user_id) VALUES (:tx, :org, :school, :staff)",
            {**base, "tx": ledger.bare_reimbursement, "staff": a1.staff},
            "rowcount",
        ),
        "refunds": (
            "INSERT INTO refunds (transaction_id, organization_id, school_id, reason) "
            "VALUES (:tx, :org, :school, 'duplicate payment')",
            {**base, "tx": ledger.bare_refund},
            "rowcount",
        ),
        "expense_attachments": (
            "INSERT INTO expense_attachments (organization_id, school_id, transaction_id, "
            "storage_key, file_name, content_type, size_bytes, sha256, uploaded_by_user_id) "
            "VALUES (:org, :school, :tx, :key, 'nota.pdf', 'application/pdf', 100, :hash, :staff)",
            {
                **base,
                "tx": ledger.expense_submitted,
                "key": f"k/{_fresh()}",
                "hash": token_hash(),
                "staff": a1.staff,
            },
            "rowcount",
        ),
        "categories": (
            "INSERT INTO categories (organization_id, school_id, key, name, applies_to) "
            "VALUES (:org, :school, :key, 'Nova', 'IN')",
            {**base, "key": f"k_{_fresh()}"},
            "rowcount",
        ),
        "school_settings": (
            "INSERT INTO school_settings (school_id, organization_id) VALUES (:school, :org)",
            base,
            "rowcount",
        ),
        "pix_charges": (
            "INSERT INTO pix_charges (organization_id, school_id, transaction_id, provider, txid, "
            "amount_cents, expires_at) "
            "VALUES (:org, :school, :tx, 'SANDBOX', :txid, 3100, now() + interval '30 minutes')",
            {**base, "tx": ledger.pix_pending_2, "txid": uuid.uuid4().hex},
            "rowcount",
        ),
        "webhook_events": (
            "INSERT INTO webhook_events (organization_id, school_id, provider, idempotency_key, "
            "raw_payload, signature_valid) VALUES (:org, :school, 'SANDBOX', :key, '{}'::jsonb, true)",
            {**base, "key": _fresh()},
            "rowcount",
        ),
        "audit_logs": (
            "INSERT INTO audit_logs (organization_id, school_id, action, entity_type) "
            "VALUES (:org, :school, 'test.event', 'test')",
            base,
            "rowcount",
        ),
        "monthly_closings": (
            "INSERT INTO monthly_closings (organization_id, school_id, period_start, "
            "closed_by_user_id) VALUES (:org, :school, "
            "(SELECT period_start + interval '1 month' FROM monthly_closings WHERE id = :closing), "
            ":treasurer)",
            {
                **base,
                "closing": ledger.closing,
                "treasurer": a1.staff,
            },  # a member of A1, visible in its context
            "rowcount",
        ),
    }
    return statements[table]


def _statement(table: str, operation: str, ledger: Ledger, tenants: Tenants) -> Statement:
    key = PRIMARY_KEY[table]
    subject = _subject(ledger, table)
    if operation == "select":
        return f"SELECT count(*) FROM {table} WHERE {key} = :id", {"id": subject}, "count"  # noqa: S608
    if operation == "delete":
        return f"DELETE FROM {table} WHERE {key} = :id", {"id": subject}, "rowcount"  # noqa: S608
    if operation == "insert":
        school = ledger.school_a3 if table == "school_settings" else tenants.school_a1
        return insert_statement(table, ledger, tenants.org_a, school)
    updates = {
        "financial_transactions": "SET status = 'CANCELLED'",
        "contributions": "SET guardian_name = NULL",
        "expenses": "SET description = 'Edited'",
        "reimbursements": "SET payment_reference = 'r-1'",
        "refunds": "SET payment_reference = 'r-1'",
        "expense_attachments": "SET file_name = 'x.pdf'",
        "categories": "SET name = 'Renamed'",
        "school_settings": "SET pix_expiration_minutes = 45",
        "pix_charges": "SET status = 'CANCELLED'",
        "webhook_events": "SET attempts = 1",
        "audit_logs": "SET action = 'x.y'",
        "monthly_closings": "SET report_ref = 'r.pdf'",
    }
    # The subject of an update must be able to change: a pending ledger row, a submitted expense.
    target = {
        "financial_transactions": ledger.pix_pending,
        "expenses": ledger.expense_submitted,
    }.get(table, subject)
    return f"UPDATE {table} {updates[table]} WHERE {key} = :id", {"id": target}, "rowcount"  # noqa: S608


def _context(
    name: str, ledger: Ledger, tenants: Tenants, table: str, operation: str
) -> TenantContext | None:
    own_school = (
        ledger.school_a3
        if (table, operation) == ("school_settings", "insert")
        else tenants.school_a1
    )
    return {
        "own_org": TenantContext(tenants.org_a),
        "own_school": TenantContext(tenants.org_a, own_school),
        "same_org_other_school": TenantContext(tenants.org_a, tenants.school_a2),
        "other_org": TenantContext(tenants.org_b),
        "no_context": None,
    }[name]


def _takes_effect(
    engine: Engine,
    *,
    owner: bool,
    table: str,
    operation: str,
    context_name: str,
    ledger: Ledger,
    tenants: Tenants,
) -> bool:
    sql, params, mode = _statement(table, operation, ledger, tenants)
    context = _context(context_name, ledger, tenants, table, operation)
    with transaction(engine, owner=owner, context=context) as connection:
        try:
            result = connection.execute(text(sql), params)
            return bool(result.scalar_one() == 1) if mode == "count" else result.rowcount == 1
        except DBAPIError:
            return False


CASES = [
    pytest.param(table, operation, context, id=f"{table}-{operation}-{context}")
    for table in TABLES
    for operation in OPERATIONS
    for context in CONTEXTS
]


@pytest.mark.parametrize(("table", "operation", "context_name"), CASES)
def test_application_role_matrix(
    app_engine: Engine,
    tenants: Tenants,
    ledger: Ledger,
    table: str,
    operation: str,
    context_name: str,
) -> None:
    expected = context_name in EXPECTED[(table, operation)]

    actual = _takes_effect(
        app_engine,
        owner=False,
        table=table,
        operation=operation,
        context_name=context_name,
        ledger=ledger,
        tenants=tenants,
    )

    assert actual is expected


@pytest.mark.parametrize(("table", "operation", "context_name"), CASES)
def test_owner_is_also_bound_by_the_policies_force_rls(
    admin_engine: Engine,
    tenants: Tenants,
    ledger: Ledger,
    table: str,
    operation: str,
    context_name: str,
) -> None:
    expected = context_name in EXPECTED[(table, operation)]

    actual = _takes_effect(
        admin_engine,
        owner=True,
        table=table,
        operation=operation,
        context_name=context_name,
        ledger=ledger,
        tenants=tenants,
    )

    assert actual is expected


# --- tenant-hop --------------------------------------------------------------------------------

# What may refuse an attempt, per layer (the text must be in the database error):
ACCEPTED_REASONS = (
    "permission denied",  # the column or command is not granted to apm_app
    "can never change",  # the immutability trigger (the owner has every privilege)
    "is final",  # the freeze trigger
    "can never be updated",  # the update guard of the insert-only tables
    "row-level security",  # the WITH CHECK of the policy
    "foreign key",  # the composite foreign key
    "invalid user reference",  # the membership guard
    "no visible settings",  # the booking and the closing, for a school the context cannot see
)

# (table, column, new value name): an UPDATE that tries to move a row to another school or tenant.
MOVES = [
    (table, column, target)
    for table in TABLES
    for column, target in (("organization_id", "org_b"), ("school_id", "school_a2"))
]


def _refused(error: DBAPIError) -> bool:
    message = str(error.orig).lower().replace("row level", "row-level")
    return any(reason in message for reason in ACCEPTED_REASONS)


@pytest.mark.parametrize("owner", [False, True], ids=["apm_app", "apm_owner"])
@pytest.mark.parametrize(
    ("table", "column", "target"), MOVES, ids=[f"{t}.{c}->{v}" for t, c, v in MOVES]
)
def test_a_row_cannot_be_moved_to_another_school_or_tenant(
    app_engine: Engine,
    admin_engine: Engine,
    tenants: Tenants,
    ledger: Ledger,
    owner: bool,
    table: str,
    column: str,
    target: str,
) -> None:
    engine = admin_engine if owner else app_engine
    destination = {"org_b": tenants.org_b, "school_a2": tenants.school_a2}[target]
    key = PRIMARY_KEY[table]
    with transaction(engine, owner=owner, context=TenantContext(tenants.org_a)) as connection:
        try:
            result = connection.execute(
                text(f"UPDATE {table} SET {column} = :v WHERE {key} = :id"),  # noqa: S608
                {"v": destination, "id": _subject(ledger, table)},
            )
        except DBAPIError as error:
            assert _refused(error), str(error.orig)
        else:
            assert result.rowcount == 0  # hidden by the policies, never moved
    with admin_engine.connect() as connection:
        row = connection.execute(
            text(f"SELECT organization_id, school_id FROM {table} WHERE {key} = :id"),  # noqa: S608
            {"id": _subject(ledger, table)},
        ).one()
    assert (row[0], row[1]) == (tenants.org_a, tenants.school_a1)


TARGETS = {
    "org_b/school_b1": ("org_b", "school_b1"),
    "org_a/school_b1": ("org_a", "school_b1"),  # the composite FK (school, organization)
    "org_b/school_a1": ("org_b", "school_a1"),
}


@pytest.mark.parametrize("owner", [False, True], ids=["apm_app", "apm_owner"])
@pytest.mark.parametrize("target", list(TARGETS))
@pytest.mark.parametrize("table", TABLES)
def test_a_new_row_cannot_be_written_into_another_tenant(
    app_engine: Engine,
    admin_engine: Engine,
    tenants: Tenants,
    ledger: Ledger,
    owner: bool,
    table: str,
    target: str,
) -> None:
    org_name, school_name = TARGETS[target]
    ids = {
        "org_a": tenants.org_a,
        "org_b": tenants.org_b,
        "school_a1": tenants.school_a1,
        "school_b1": tenants.school_b1,
    }
    sql, params, _mode = insert_statement(table, ledger, ids[org_name], ids[school_name])
    engine = admin_engine if owner else app_engine
    with (
        transaction(engine, owner=owner, context=TenantContext(tenants.org_a)) as connection,
        pytest.raises(DBAPIError) as error,
    ):
        connection.execute(text(sql), params)
    assert _refused(error.value), str(error.value.orig)
