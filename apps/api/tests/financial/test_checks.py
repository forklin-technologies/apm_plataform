# ruff: noqa: E501  (SQL text)
"""F6: every CHECK constraint is exercised.

Each case takes a valid row and breaks exactly ONE CHECK with an UPDATE, run with the triggers
switched off (session_replication_role = replica, which only a superuser can do), so that the CHECK
is the only thing that can refuse it. The error must name that constraint. A coverage test compares
the cases with the CHECKs in the catalog: a CHECK nobody exercises, or a case for a CHECK that is
gone, fails the suite.
"""

import uuid

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from tests.financial.support import (
    Fresh,
    add_attachment,
    add_cash_contribution,
    add_collaborator_expense,
    add_expense,
    add_pix_contribution,
    add_refund,
    utc,
)
from tests.financial.test_isolation import TABLES

FT, CONTRIBUTIONS, EXPENSES = "financial_transactions", "contributions", "expenses"

# (table, subject, SET clause, the CHECK it must break). `subject` names a row built by `_subjects`.
CASES: list[tuple[str, str, str, str]] = [
    # --- financial_transactions: value, direction against kind, status per kind --------------------
    (FT, "cash_tx", "amount_cents = 0", "ck_financial_transactions_amount_range"),
    (FT, "cash_tx", "amount_cents = -5", "ck_financial_transactions_amount_range"),
    (FT, "cash_tx", "amount_cents = 1000000000001", "ck_financial_transactions_amount_range"),
    (FT, "expense_tx", "created_by_user_id = NULL", "ck_financial_transactions_author_required"),
    (FT, "cash_tx", "direction = 'SIDEWAYS'", "ck_financial_transactions_direction_valid"),
    (FT, "cash_tx", "kind = 'GIFT'", "ck_financial_transactions_kind_valid"),
    (
        FT,
        "cash_tx",
        "late_adjustment = true, settled_at = NULL, status = 'PENDING_PAYMENT'",
        "ck_financial_transactions_late_needs_settled",
    ),
    (
        FT,
        "cash_tx",
        "parent_transaction_id = id, parent_kind = 'CONTRIBUTION'",
        "ck_financial_transactions_not_own_parent",
    ),
    (FT, "cash_tx", "settled_at = NULL", "ck_financial_transactions_paid_iff_settled"),
    (FT, "pending_tx", "settled_at = now()", "ck_financial_transactions_paid_iff_settled"),
    (
        FT,
        "refund_tx",
        "settled_at = NULL",
        "ck_financial_transactions_paid_iff_settled",
    ),  # CONFIRMED is settled
    (FT, "refund_pending_tx", "settled_at = now()", "ck_financial_transactions_paid_iff_settled"),
    (FT, "cash_tx", "reference_code = 0", "ck_financial_transactions_reference_positive"),
    (FT, "cash_tx", "origin_type = 'ALIEN'", "ck_financial_transactions_origin_type_valid"),
    (FT, "expense_tx", "origin_name = ''", "ck_financial_transactions_origin_name_length"),
    (
        FT,
        "expense_tx",
        "origin_name = repeat('x', 121)",
        "ck_financial_transactions_origin_name_length",
    ),
    (FT, "cash_tx", "origin_name = 'Maria'", "ck_financial_transactions_no_contributor_name"),
    # direction against kind (and a refund is money coming BACK: always IN)
    (FT, "cash_tx", "direction = 'OUT'", "ck_financial_transactions_shape"),  # a contribution is IN
    (FT, "expense_tx", "direction = 'IN'", "ck_financial_transactions_shape"),  # an expense is OUT
    (FT, "refund_tx", "direction = 'OUT'", "ck_financial_transactions_shape"),  # a refund is IN
    (FT, "reimbursement_tx", "direction = 'IN'", "ck_financial_transactions_shape"),
    (FT, "reimbursement_tx", "parent_kind = 'CONTRIBUTION'", "ck_financial_transactions_shape"),
    (
        FT,
        "cash_tx",
        "parent_transaction_id = gen_random_uuid(), parent_kind = 'EXPENSE'",
        "ck_financial_transactions_shape",
    ),
    (
        FT,
        "refund_tx",
        "parent_kind = 'REFUND'",
        "ck_financial_transactions_shape",
    ),  # parent: expense or reimbursement
    (FT, "refund_tx", "parent_kind = 'CONTRIBUTION'", "ck_financial_transactions_shape"),
    (
        FT,
        "refund_pending_tx",
        "parent_kind = 'EXPENSE'",
        "ck_financial_transactions_shape",
    ),  # kind without parent
    (
        FT,
        "refund_tx",
        "parent_transaction_id = NULL",
        "ck_financial_transactions_shape",
    ),  # parent without kind
    # status valid for the kind (a pending row, so that PAID/settled_at stay consistent)
    (FT, "pending_tx", "status = 'SUBMITTED'", "ck_financial_transactions_status_for_kind"),
    (FT, "pending_tx", "status = 'DRAFT'", "ck_financial_transactions_status_for_kind"),
    (FT, "pending_tx", "status = 'REQUESTED'", "ck_financial_transactions_status_for_kind"),
    (FT, "submitted_tx", "status = 'PENDING_PAYMENT'", "ck_financial_transactions_status_for_kind"),
    (FT, "submitted_tx", "status = 'REVIEW_REQUIRED'", "ck_financial_transactions_status_for_kind"),
    (
        FT,
        "reimbursement_pending_tx",
        "status = 'APPROVED'",
        "ck_financial_transactions_status_for_kind",
    ),
    (FT, "refund_pending_tx", "status = 'CANCELLED'", "ck_financial_transactions_status_for_kind"),
    (FT, "refund_pending_tx", "status = 'PENDING'", "ck_financial_transactions_status_for_kind"),
    # --- details ------------------------------------------------------------------------------------
    (CONTRIBUTIONS, "cash_tx", "kind = 'EXPENSE'", "ck_contributions_kind"),
    (CONTRIBUTIONS, "cash_tx", "method = 'CARD'", "ck_contributions_method_valid"),
    (CONTRIBUTIONS, "cash_tx", "guardian_name = ''", "ck_contributions_guardian_name_length"),
    (
        CONTRIBUTIONS,
        "cash_tx",
        "student_name = repeat('x', 121)",
        "ck_contributions_student_name_length",
    ),
    (CONTRIBUTIONS, "cash_tx", "class_name = '   '", "ck_contributions_class_name_length"),
    (
        CONTRIBUTIONS,
        "cash_tx",
        "contributor_email = 'not-an-email'",
        "ck_contributions_contributor_email_format",
    ),
    (
        CONTRIBUTIONS,
        "cash_tx",
        "contributor_email = repeat('a', 250) || '@x.com'",
        "ck_contributions_contributor_email_format",
    ),
    (
        CONTRIBUTIONS,
        "cash_tx",
        "contributor_phone = repeat('1', 31)",
        "ck_contributions_contributor_phone_length",
    ),
    (CONTRIBUTIONS, "pix_tx", "receipt_token_hash = NULL", "ck_contributions_pix_has_receipt"),
    (CONTRIBUTIONS, "pix_tx", "receipt_expires_at = NULL", "ck_contributions_pix_has_receipt"),
    (
        CONTRIBUTIONS,
        "pix_tx",
        "receipt_token_hash = 'not-a-hash'",
        "ck_contributions_receipt_token_hash_format",
    ),
    (
        CONTRIBUTIONS,
        "pix_tx",
        "review_decision_reason = 'ab'",
        "ck_contributions_review_decision_reason_length",
    ),
    (EXPENSES, "expense_tx", "kind = 'REFUND'", "ck_expenses_kind"),
    (EXPENSES, "expense_tx", "description = ''", "ck_expenses_description_length"),
    (EXPENSES, "expense_tx", "vendor = repeat('x', 201)", "ck_expenses_vendor_length"),
    (EXPENSES, "expense_tx", "purchase_reason = 'ab'", "ck_expenses_purchase_reason_length"),
    (EXPENSES, "expense_tx", "payment_method = 'BARTER'", "ck_expenses_payment_method_valid"),
    (EXPENSES, "expense_tx", "paid_by = 'NOBODY'", "ck_expenses_paid_by_valid"),
    (
        EXPENSES,
        "expense_tx",
        "approved_by_user_id = submitted_by_user_id",
        "ck_expenses_decider_is_not_submitter",
    ),
    (EXPENSES, "expense_tx", "approved_at = NULL", "ck_expenses_decision_complete"),
    (EXPENSES, "expense_tx", "approved_amount_cents = 0", "ck_expenses_approved_amount_positive"),
    (
        EXPENSES,
        "expense_tx",
        "decision_reason = repeat('x', 501)",
        "ck_expenses_decision_reason_length",
    ),
    (EXPENSES, "expense_tx", "correction_reason = 'ab'", "ck_expenses_correction_reason_length"),
    ("reimbursements", "reimbursement_tx", "kind = 'EXPENSE'", "ck_reimbursements_kind"),
    (
        "reimbursements",
        "reimbursement_tx",
        "payment_reference = ''",
        "ck_reimbursements_payment_reference_length",
    ),
    ("refunds", "refund_tx", "kind = 'EXPENSE'", "ck_refunds_kind"),
    ("refunds", "refund_tx", "reason = 'ab'", "ck_refunds_reason_length"),
    (
        "refunds",
        "refund_tx",
        "payment_reference = repeat('x', 201)",
        "ck_refunds_payment_reference_length",
    ),
    ("expense_attachments", "attachment", "kind = 'SELFIE'", "ck_expense_attachments_kind_valid"),
    (
        "expense_attachments",
        "attachment",
        "content_type = 'PDF'",
        "ck_expense_attachments_content_type_format",
    ),
    (
        "expense_attachments",
        "attachment",
        "file_name = ''",
        "ck_expense_attachments_file_name_length",
    ),
    ("expense_attachments", "attachment", "sha256 = 'xyz'", "ck_expense_attachments_sha256_format"),
    ("expense_attachments", "attachment", "size_bytes = 0", "ck_expense_attachments_size_range"),
    (
        "expense_attachments",
        "attachment",
        "size_bytes = 20971521",
        "ck_expense_attachments_size_range",
    ),
    (
        "expense_attachments",
        "attachment",
        "storage_key = '/absolute/path'",
        "ck_expense_attachments_storage_key_length",
    ),
    # --- categories and settings ----------------------------------------------------------------------
    ("categories", "category", "applies_to = 'BOTH'", "ck_categories_applies_to_valid"),
    ("categories", "category", "key = 'Bad Key'", "ck_categories_key_format"),
    ("categories", "category", "name = ''", "ck_categories_name_length"),
    ("categories", "category", "report_group = 'MISC'", "ck_categories_group_matches_direction"),
    (
        "categories",
        "category",
        "report_group = 'EXPENSES_REIMBURSEMENTS'",
        "ck_categories_group_matches_direction",
    ),  # an IN category
    (
        "categories",
        "category",
        "applies_to = 'OUT'",
        "ck_categories_group_matches_direction",
    ),  # an OUT category in an IN group
    (
        "school_settings",
        "settings",
        "approval_limit_cents = 0",
        "ck_school_settings_approval_limit_positive",
    ),
    (
        "school_settings",
        "settings",
        "brand_accent_contrast = 'white'",
        "ck_school_settings_brand_accent_contrast_format",
    ),
    (
        "school_settings",
        "settings",
        "brand_accent = '#12'",
        "ck_school_settings_brand_accent_format",
    ),
    (
        "school_settings",
        "settings",
        "min_contribution_cents = 0",
        "ck_school_settings_contribution_range",
    ),
    (
        "school_settings",
        "settings",
        "min_contribution_cents = max_contribution_cents + 1, suggested_amounts_cents = ARRAY[max_contribution_cents + 1]",
        "ck_school_settings_contribution_range",
    ),
    (
        "school_settings",
        "settings",
        "max_contribution_cents = 1000000001",
        "ck_school_settings_contribution_range",
    ),
    (
        "school_settings",
        "settings",
        "suggested_amounts_cents = ARRAY[]::bigint[]",
        "ck_school_settings_suggested_amounts_valid",
    ),
    (
        "school_settings",
        "settings",
        "suggested_amounts_cents = ARRAY[1,2,3,4,5,6,7]::bigint[]",
        "ck_school_settings_suggested_amounts_valid",
    ),
    (
        "school_settings",
        "settings",
        "suggested_amounts_cents = ARRAY[500]::bigint[]",
        "ck_school_settings_suggested_amounts_valid",
    ),  # below the minimum
    (
        "school_settings",
        "settings",
        "suggested_amounts_cents = ARRAY[600000]::bigint[]",
        "ck_school_settings_suggested_amounts_valid",
    ),  # above the maximum
    (
        "school_settings",
        "settings",
        "suggested_amounts_cents = ARRAY[2000, NULL]::bigint[]",
        "ck_school_settings_suggested_amounts_valid",
    ),
    (
        "school_settings",
        "settings",
        "pix_expiration_minutes = 4",
        "ck_school_settings_pix_expiration_range",
    ),
    (
        "school_settings",
        "settings",
        "pix_expiration_minutes = 1441",
        "ck_school_settings_pix_expiration_range",
    ),
    (
        "school_settings",
        "settings",
        "required_fields = ARRAY['shoe_size']",
        "ck_school_settings_identification_fields_valid",
    ),
    (
        "school_settings",
        "settings",
        "optional_fields = ARRAY['shoe_size']",
        "ck_school_settings_identification_fields_valid",
    ),
    (
        "school_settings",
        "settings",
        "required_fields = ARRAY['guardian_name']",
        "ck_school_settings_identification_fields_valid",
    ),  # also optional by default: the lists overlap
    (
        "school_settings",
        "settings",
        "timezone = 'Mars/Olympus'",
        "ck_school_settings_timezone_valid",
    ),
    # --- payment accounts: no credential, a reference to a secrets manager -----------------------------
    ("payment_accounts", "account", "provider = 'STRIPE'", "ck_payment_accounts_provider_valid"),
    (
        "payment_accounts",
        "account",
        "external_account_id = ''",
        "ck_payment_accounts_external_account_id_length",
    ),
    ("payment_accounts", "account", "status = 'LOST'", "ck_payment_accounts_status_valid"),
    (
        "payment_accounts",
        "account",
        "secret_ref = 'a-raw-secret-value'",
        "ck_payment_accounts_secret_ref_format",
    ),
    ("payment_accounts", "account", "secret_ref = 'env:'", "ck_payment_accounts_secret_ref_format"),
    (
        "payment_accounts",
        "account",
        "secret_ref = 'env:has space'",
        "ck_payment_accounts_secret_ref_format",
    ),
    (
        "payment_accounts",
        "account",
        "webhook_secret_hash = 'xyz'",
        "ck_payment_accounts_webhook_secret_hash_format",
    ),
    # --- Pix and webhooks -----------------------------------------------------------------------------
    ("pix_charges", "charge", "amount_cents = 0", "ck_pix_charges_amount_range"),
    ("pix_charges", "charge", "received_amount_cents = 0", "ck_pix_charges_received_amount_range"),
    ("pix_charges", "charge", "emv_payload = ''", "ck_pix_charges_emv_payload_length"),
    ("pix_charges", "charge", "end_to_end_id = 'bad'", "ck_pix_charges_end_to_end_id_format"),
    ("pix_charges", "charge", "expires_at = created_at", "ck_pix_charges_expires_after_creation"),
    ("pix_charges", "charge", "status = 'PAID'", "ck_pix_charges_paid_iff_confirmed"),
    ("pix_charges", "charge", "status = 'REVIEW_REQUIRED'", "ck_pix_charges_paid_iff_confirmed"),
    (
        "pix_charges",
        "charge",
        "status = 'PAID', end_to_end_id = 'E' || repeat('1', 31), paid_at = now(), received_amount_cents = amount_cents + 1",
        "ck_pix_charges_received_matches_status",
    ),
    (
        "pix_charges",
        "charge",
        "status = 'REVIEW_REQUIRED', end_to_end_id = 'E' || repeat('1', 31), paid_at = now(), received_amount_cents = amount_cents, divergence_reason = 'same amount'",
        "ck_pix_charges_received_matches_status",
    ),
    (
        "pix_charges",
        "charge",
        "status = 'REVIEW_REQUIRED', end_to_end_id = 'E' || repeat('1', 31), paid_at = now(), received_amount_cents = amount_cents + 1",
        "ck_pix_charges_received_matches_status",
    ),  # no reason
    (
        "pix_charges",
        "charge",
        "divergence_reason = 'ab'",
        "ck_pix_charges_divergence_reason_length",
    ),
    ("pix_charges", "charge", "provider = 'STRIPE'", "ck_pix_charges_provider_valid"),
    ("pix_charges", "charge", "status = 'LOST'", "ck_pix_charges_status_valid"),
    ("pix_charges", "charge", "txid = 'short'", "ck_pix_charges_txid_format"),
    ("webhook_events", "webhook", "attempts = -1", "ck_webhook_events_attempts_non_negative"),
    (
        "webhook_events",
        "webhook",
        "end_to_end_id = 'bad'",
        "ck_webhook_events_end_to_end_id_format",
    ),
    (
        "webhook_events",
        "webhook",
        "idempotency_key = ''",
        "ck_webhook_events_idempotency_key_length",
    ),
    (
        "webhook_events",
        "webhook",
        "processing_error = repeat('x', 1001)",
        "ck_webhook_events_processing_error_length",
    ),
    ("webhook_events", "webhook", "provider = 'STRIPE'", "ck_webhook_events_provider_valid"),
    (
        "webhook_events",
        "webhook",
        "raw_payload = '[]'::jsonb",
        "ck_webhook_events_raw_payload_object",
    ),
    # --- audit and closings -----------------------------------------------------------------------------
    ("audit_logs", "audit", "action = 'BadAction'", "ck_audit_logs_action_format"),
    ("audit_logs", "audit", "actor_type = 'USER'", "ck_audit_logs_actor_matches_type"),
    ("audit_logs", "audit", "actor_type = 'ADMIN'", "ck_audit_logs_actor_type_valid"),
    ("audit_logs", "audit", "after_data = '[]'::jsonb", "ck_audit_logs_after_data_object"),
    ("audit_logs", "audit", "before_data = '\"x\"'::jsonb", "ck_audit_logs_before_data_object"),
    ("audit_logs", "audit", "entity_type = 'Bad Type'", "ck_audit_logs_entity_type_format"),
    ("audit_logs", "audit", "entity_reference = 0", "ck_audit_logs_entity_reference_positive"),
    ("audit_logs", "audit", "request_id = repeat('x', 201)", "ck_audit_logs_request_id_length"),
    (
        "monthly_closings",
        "closing",
        "total_in_cents = total_in_cents + 1, closing_balance_cents = closing_balance_cents + 1, closing_after_pending_cents = closing_after_pending_cents + 1",
        "ck_monthly_closings_totals_add_up",
    ),
    (
        "monthly_closings",
        "closing",
        "closing_balance_cents = closing_balance_cents + 1, closing_after_pending_cents = closing_after_pending_cents + 1",
        "ck_monthly_closings_balance_arithmetic",
    ),
    (
        "monthly_closings",
        "closing",
        "closing_after_pending_cents = closing_after_pending_cents + 1",
        "ck_monthly_closings_committed_arithmetic",
    ),
    (
        "monthly_closings",
        "closing",
        "period_end = period_end + 1",
        "ck_monthly_closings_calendar_month",
    ),
    (
        "monthly_closings",
        "closing",
        "period_start = period_start + 1",
        "ck_monthly_closings_calendar_month",
    ),
    (
        "monthly_closings",
        "closing",
        "entries_hash = 'abc'",
        "ck_monthly_closings_entries_hash_format",
    ),
    (
        "monthly_closings",
        "closing",
        "breakdown = '[]'::jsonb",
        "ck_monthly_closings_breakdown_object",
    ),
    ("monthly_closings", "closing", "reopened_at = now()", "ck_monthly_closings_reopen_complete"),
    (
        "monthly_closings",
        "closing",
        "reopened_at = now(), reopened_by_user_id = closed_by_user_id, reopen_reason = 'short'",
        "ck_monthly_closings_reopen_reason_length",
    ),
    ("monthly_closings", "closing", "report_ref = ''", "ck_monthly_closings_report_ref_length"),
    (
        "monthly_closings",
        "closing",
        "pending_reimbursements_cents = -1, closing_after_pending_cents = closing_balance_cents + 1",
        "ck_monthly_closings_totals_non_negative",
    ),
    (
        "monthly_closings",
        "closing",
        "entries_count = -1",
        "ck_monthly_closings_totals_non_negative",
    ),
]

PRIMARY_KEY = {
    "financial_transactions": "id",
    "contributions": "transaction_id",
    "expenses": "transaction_id",
    "reimbursements": "transaction_id",
    "refunds": "transaction_id",
    "expense_attachments": "id",
    "categories": "id",
    "school_settings": "school_id",
    "payment_accounts": "id",
    "pix_charges": "id",
    "webhook_events": "id",
    "audit_logs": "id",
    "monthly_closings": "id",
}


def _subjects(conn: Connection, f: Fresh) -> dict[str, uuid.UUID]:
    cash = add_cash_contribution(conn, f, 5000, utc(2025, 3, 5, 15), guardian="Maria")
    pix, charge = add_pix_contribution(conn, f, 3000)
    expense = add_expense(conn, f, 1500, status="PAID", settled_at=utc(2025, 3, 6, 15))
    submitted = add_expense(conn, f, 900)
    _, reimbursement = add_collaborator_expense(conn, f, 4000, reimbursed_at=utc(2025, 3, 25, 15))
    _, reimbursement_pending = add_collaborator_expense(conn, f, 700)
    refund = add_refund(
        conn, f, 500, parent=expense, parent_kind="EXPENSE", status="CONFIRMED",
        settled_at=utc(2025, 3, 7, 15),
    )  # fmt: skip
    refund_pending = add_refund(conn, f, 100)
    attachment = add_attachment(conn, f, submitted)
    webhook: uuid.UUID = conn.execute(
        text(
            "INSERT INTO webhook_events (organization_id, school_id, provider, idempotency_key, raw_payload, signature_valid) "
            "VALUES (:o, :s, 'SANDBOX', :k, '{}'::jsonb, true) RETURNING id"
        ),
        {"o": f.org, "s": f.school, "k": uuid.uuid4().hex},
    ).scalar_one()
    audit: uuid.UUID = conn.execute(
        text(
            "INSERT INTO audit_logs (organization_id, school_id, action, entity_type) VALUES (:o, :s, 'test.event', 'test') RETURNING id"
        ),
        {"o": f.org, "s": f.school},
    ).scalar_one()
    closing: uuid.UUID = conn.execute(
        text(
            "INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id) VALUES (:o, :s, '2025-03-01', :u) RETURNING id"
        ),
        {"o": f.org, "s": f.school, "u": f.treasurer},
    ).scalar_one()
    return {
        "cash_tx": cash, "pix_tx": pix, "pending_tx": pix, "charge": charge, "expense_tx": expense,
        "submitted_tx": submitted, "reimbursement_tx": reimbursement,
        "reimbursement_pending_tx": reimbursement_pending, "refund_tx": refund,
        "refund_pending_tx": refund_pending, "attachment": attachment, "category": f.cat_in,
        "settings": f.school, "account": f.account, "webhook": webhook, "audit": audit,
        "closing": closing,
    }  # fmt: skip


@pytest.mark.parametrize(
    ("table", "subject", "clause", "constraint"),
    CASES,
    ids=[f"{c[3].removeprefix('ck_')}|{i}" for i, c in enumerate(CASES)],
)
def test_each_check_refuses_the_row_that_breaks_it(
    world: tuple[Connection, Fresh], table: str, subject: str, clause: str, constraint: str
) -> None:
    conn, f = world
    subjects = _subjects(conn, f)
    key = PRIMARY_KEY[table]
    with pytest.raises(DBAPIError) as error, conn.begin_nested():
        conn.execute(
            text("SET LOCAL session_replication_role = replica")
        )  # only the CHECKs guard now
        conn.execute(
            text(f"UPDATE {table} SET {clause} WHERE {key} = :id"),  # noqa: S608
            {"id": subjects[subject]},
        )
    if constraint == "ck_school_settings_timezone_valid":
        assert "time zone" in str(
            error.value.orig
        )  # an unknown zone makes the expression itself fail
    else:
        assert error.value.orig.diag.constraint_name == constraint, str(error.value.orig)  # type: ignore[union-attr]


def test_every_check_in_the_catalog_has_a_case_and_every_case_a_check(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        catalog = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT c.conname FROM pg_constraint c JOIN pg_class cl ON cl.oid = c.conrelid "
                    "WHERE c.contype = 'c' AND cl.relname = ANY(:t)"
                ),
                {"t": TABLES},
            )
        }
    covered = {constraint for _table, _subject, _clause, constraint in CASES}
    assert len(catalog) >= 70
    assert catalog - covered == set(), "a CHECK nobody exercises"
    assert covered - catalog == set(), "a case for a CHECK that no longer exists"


def test_a_valid_row_survives_every_case_setup(world: tuple[Connection, Fresh]) -> None:
    """The control: the rows the cases start from are valid (the CHECKs accept them)."""
    conn, f = world
    subjects = _subjects(conn, f)
    assert len(subjects) >= 15
