# ruff: noqa: E501  (SQL text)
"""Condition 5: every change to a financial table is recorded by the database, in the same
transaction, with the actor taken from the transaction settings (never from a column), and with no
personal data in `before_data` and `after_data`.

Contract with the authentication layer (docs/financial-model.md): app.user_id, app.actor_type
(USER, SYSTEM or PUBLIC), app.request_id and, optionally, app.client_ip, set with
set_config(..., true) so they last one transaction. Without them the actor is NULL and PUBLIC.
"""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction
from tests.financial.conftest import Ledger
from tests.financial.support import (
    Fresh,
    add_cash_contribution,
    add_expense,
    add_pix_contribution,
    add_pix_review,
    add_refund,
    add_reimbursement,
    set_status,
    token_hash,
    utc,
)

REQUEST_ID = "req-7f3a"
CLIENT_IP = "203.0.113.7"


def act_as(conn: Connection, user: uuid.UUID | None, actor_type: str | None = "USER") -> None:
    """Set the actor of this transaction, the way the authentication layer will."""
    conn.execute(
        text(
            "SELECT set_config('app.user_id', :u, true), set_config('app.actor_type', :t, true), "
            "set_config('app.request_id', :r, true), set_config('app.client_ip', :ip, true)"
        ),
        {
            "u": "" if user is None else str(user),
            "t": actor_type or "",
            "r": REQUEST_ID,
            "ip": CLIENT_IP,
        },
    )


def _scalar(conn: Connection, sql: str, **params: object) -> Any:
    return conn.execute(text(sql), params).scalar_one()


def audit_rows(conn: Connection, entity_type: str, entity_id: uuid.UUID) -> list[Any]:
    return list(
        conn.execute(
            text(
                "SELECT action, actor_user_id, actor_type, request_id, host(ip), before_data, "
                "after_data, organization_id, school_id, entity_reference FROM audit_logs "
                "WHERE entity_type = :t AND entity_id = :e ORDER BY occurred_at, id"
            ),
            {"t": entity_type, "e": entity_id},
        )
    )


def test_an_insert_and_an_update_leave_a_record_with_the_actor_of_the_settings(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    act_as(conn, f.treasurer)
    tx, _ = add_pix_contribution(conn, f, 3000)
    conn.execute(
        text("UPDATE financial_transactions SET status = 'CANCELLED' WHERE id = :t"), {"t": tx}
    )

    inserted, updated = audit_rows(conn, "financial_transactions", tx)

    assert inserted.action == "financial_transactions.insert"
    assert inserted.before_data is None
    assert inserted.after_data["status"] == "PENDING_PAYMENT"
    assert inserted.after_data["amount_cents"] == 3000
    assert updated.action == "financial_transactions.update"
    assert updated.before_data == {"status": "PENDING_PAYMENT"}  # only what changed
    assert updated.after_data == {"status": "CANCELLED"}
    for row in (inserted, updated):
        assert row.actor_user_id == f.treasurer
        assert row.actor_type == "USER"
        assert row.request_id == REQUEST_ID
        assert row[4] == CLIENT_IP
        assert row.organization_id == f.org and row.school_id == f.school


def test_without_settings_the_actor_is_null_and_public(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx, _ = add_pix_contribution(conn, f, 3000)

    (row,) = audit_rows(conn, "financial_transactions", tx)

    assert row.actor_user_id is None
    assert row.actor_type == "PUBLIC"
    assert row.request_id is None and row[4] is None


def test_a_job_is_a_system_actor_without_a_user(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    act_as(conn, None, "SYSTEM")
    tx, _ = add_pix_contribution(conn, f, 3000)

    (row,) = audit_rows(conn, "financial_transactions", tx)

    assert (row.actor_type, row.actor_user_id, row.request_id) == ("SYSTEM", None, REQUEST_ID)


@pytest.mark.parametrize(
    ("user", "actor_type"),
    [("none", "USER"), ("member", "PUBLIC"), ("member", "SYSTEM"), ("member", "ADMIN")],
)
def test_an_actor_that_does_not_match_its_type_is_refused(
    world: tuple[Connection, Fresh], user: str, actor_type: str
) -> None:
    conn, f = world
    act_as(conn, f.treasurer if user == "member" else None, actor_type)
    with pytest.raises(DBAPIError, match="ck_audit_logs_actor"):
        add_pix_contribution(conn, f, 3000)


def test_the_actor_must_be_an_active_member_of_the_school(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    act_as(conn, f.outsider)
    with pytest.raises(DBAPIError, match="invalid user reference in actor_user_id"):
        add_pix_contribution(conn, f, 3000)


def test_a_service_can_add_a_high_level_event_and_cannot_choose_its_actor(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    act_as(conn, f.treasurer)
    expense = add_expense(conn, f, 1000)
    conn.execute(
        text(
            "INSERT INTO audit_logs (organization_id, school_id, action, entity_type, entity_id, "
            "after_data) VALUES (:o, :s, 'expense.approved', 'financial_transactions', :e, "
            '\'{"note": "approved"}\'::jsonb)'
        ),
        {"o": f.org, "s": f.school, "e": expense},
    )
    events = [
        r
        for r in audit_rows(conn, "financial_transactions", expense)
        if r.action == "expense.approved"
    ]
    assert [(r.actor_user_id, r.actor_type) for r in events] == [(f.treasurer, "USER")]


def test_the_application_role_cannot_write_the_actor_columns(
    app_engine: Engine, tenants: Tenants
) -> None:
    for column in ("actor_user_id", "actor_type", "request_id", "ip", "occurred_at", "id"):
        with (
            transaction(app_engine, context=TenantContext(tenants.org_a)) as connection,
            pytest.raises(DBAPIError, match="permission denied"),
        ):
            connection.execute(
                text(
                    f"INSERT INTO audit_logs (organization_id, school_id, action, entity_type, {column}) "  # noqa: S608
                    f"SELECT :o, :s, 'test.event', 'test', {column} FROM audit_logs LIMIT 1"
                ),
                {"o": tenants.org_a, "s": tenants.school_a1},
            )


def test_a_change_that_touches_no_audited_column_leaves_no_record(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    tx, _ = add_pix_contribution(conn, f, 3000)
    before = len(audit_rows(conn, "financial_transactions", tx))
    conn.execute(
        text("UPDATE financial_transactions SET updated_at = now() WHERE id = :t"), {"t": tx}
    )
    assert len(audit_rows(conn, "financial_transactions", tx)) == before


def test_a_rolled_back_change_leaves_no_record(admin_engine: Engine, ledger: Ledger) -> None:
    """The record is written in the SAME transaction as the change."""
    count = text("SELECT count(*) FROM audit_logs WHERE entity_id = :e")
    with admin_engine.connect() as connection:
        before: Any = connection.execute(count, {"e": ledger.pix_pending}).scalar_one()
    with admin_engine.connect() as connection:
        transaction_ = connection.begin()
        connection.execute(
            text("UPDATE financial_transactions SET status = 'CANCELLED' WHERE id = :t"),
            {"t": ledger.pix_pending},
        )
        inside: Any = connection.execute(count, {"e": ledger.pix_pending}).scalar_one()
        transaction_.rollback()
    with admin_engine.connect() as connection:
        after: Any = connection.execute(count, {"e": ledger.pix_pending}).scalar_one()
    assert inside == before + 1 and after == before


# --- one record per audited table, and no personal data in any of them ----------------------------

CANARIES = {
    "guardian": "CANARY-GUARDIAN",
    "student": "CANARY-STUDENT",
    "class_name": "CANARY-CLASS",
    "email": "canary-contributor@example.test",
    "phone": "CANARY-+55-11-90000-0000",
    "description": "CANARY-DESCRIPTION",
    "vendor": "CANARY-VENDOR",
    "purchase_reason": "CANARY-PURCHASE-REASON",
    "decision_reason": "CANARY-DECISION",
    "correction_reason": "CANARY-CORRECTION",
    "review_reason": "CANARY-REVIEW-REASON",
    "divergence_reason": "CANARY-DIVERGENCE",
    "refund_reason": "CANARY-REFUND-REASON",
    "origin_name": "CANARY-ORIGIN-NAME",
    "payment_reference": "CANARY-PAYMENT-REF",
    "file_name": "CANARY-FILE.pdf",
    "storage_key": "k/CANARY-KEY",
    "emv": "CANARY-EMV-PAYLOAD",
    "external_account": "CANARY-ACCOUNT-ID",
    "secret_ref": "env:CANARY_SECRET_REF",
    "reopen_reason": "CANARY-REOPEN-REASON",
    "report_ref": "CANARY-REPORT.pdf",
}
PERSONAL_KEYS = {
    "guardian_name", "student_name", "class_name", "contributor_email", "contributor_phone",
    "description", "vendor", "purchase_reason", "decision_reason", "correction_reason",
    "review_decision_reason", "divergence_reason", "reason", "origin_name", "payment_reference",
    "file_name", "storage_key", "emv_payload", "receipt_token_hash", "external_account_id",
    "secret_ref", "webhook_secret_hash", "reopen_reason", "report_ref",
}  # fmt: skip


def test_every_audited_table_writes_its_record_and_none_carries_personal_data(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    act_as(conn, f.treasurer)
    webhook_hash = token_hash()

    # contributions (insert, anonymize), financial_transactions (insert)
    cash = add_cash_contribution(
        conn, f, 10000, utc(2025, 3, 5, 15), guardian=CANARIES["guardian"],
        student=CANARIES["student"], class_name=CANARIES["class_name"],
        email=CANARIES["email"], phone=CANARIES["phone"],
    )  # fmt: skip
    conn.execute(
        text(
            "UPDATE contributions SET guardian_name = NULL, contributor_email = NULL "
            "WHERE transaction_id = :t"
        ),
        {"t": cash},
    )
    # A Pix in review accepted by the management: the review reason, then the amount received.
    _, charge = add_pix_contribution(conn, f, 3000)
    conn.execute(
        text("UPDATE pix_charges SET emv_payload = :e WHERE id = :c"),
        {"e": CANARIES["emv"], "c": charge},
    )
    review_tx, review_charge = add_pix_review(conn, f, 3300, 3350)
    conn.execute(
        text("UPDATE contributions SET review_decision_reason = :r WHERE transaction_id = :t"),
        {"r": CANARIES["review_reason"], "t": review_tx},
    )
    conn.execute(
        text(
            "UPDATE financial_transactions SET status = 'PAID', amount_cents = 3350, "
            "settled_at = :at WHERE id = :t"
        ),
        {"at": utc(2025, 3, 11, 15), "t": review_tx},
    )
    # expenses (insert, edit, decide), expense_attachments (insert)
    expense = add_expense(conn, f, 1000, status="DRAFT")
    conn.execute(
        text(
            "UPDATE expenses SET vendor = :v, description = :d, purchase_reason = :p "
            "WHERE transaction_id = :t"
        ),
        {
            "v": CANARIES["vendor"],
            "d": CANARIES["description"],
            "p": CANARIES["purchase_reason"],
            "t": expense,
        },
    )
    attachment: Any = conn.execute(
        text(
            "INSERT INTO expense_attachments (organization_id, school_id, transaction_id, kind, "
            "storage_key, file_name, content_type, size_bytes, sha256, uploaded_by_user_id) "
            "VALUES (:o, :s, :t, 'INVOICE', :k, :n, 'application/pdf', 100, :h, :u) RETURNING id"
        ),
        {"o": f.org, "s": f.school, "t": expense, "k": CANARIES["storage_key"],
         "n": CANARIES["file_name"], "h": token_hash(), "u": f.staff},
    ).scalar_one()  # fmt: skip
    set_status(conn, expense, "SUBMITTED")
    conn.execute(
        text("UPDATE expenses SET correction_reason = :r WHERE transaction_id = :t"),
        {"r": CANARIES["correction_reason"], "t": expense},
    )
    set_status(conn, expense, "CORRECTION_REQUESTED")
    set_status(conn, expense, "SUBMITTED")
    conn.execute(
        text(
            "UPDATE expenses SET approved_by_user_id = :u, approved_amount_cents = 1000, "
            "decision_reason = :r WHERE transaction_id = :t"
        ),
        {"u": f.treasurer, "r": CANARIES["decision_reason"], "t": expense},
    )
    # reimbursements, refunds (insert, payment reference, confirmation)
    collab = add_expense(conn, f, 700, paid_by="COLLABORATOR", status="APPROVED")
    reimbursement = add_reimbursement(conn, f, collab)
    conn.execute(
        text(
            "UPDATE reimbursements SET payment_reference = :r, paid_by_user_id = :u "
            "WHERE transaction_id = :t"
        ),
        {"r": CANARIES["payment_reference"], "u": f.treasurer, "t": reimbursement},
    )
    refund = add_refund(
        conn, f, 500, reason=CANARIES["refund_reason"], origin_name=CANARIES["origin_name"],
        origin_type="OTHER",
    )  # fmt: skip
    conn.execute(
        text("UPDATE refunds SET payment_reference = :r WHERE transaction_id = :t"),
        {"r": CANARIES["payment_reference"], "t": refund},
    )
    # categories, school_settings, payment_accounts (a hash and a reference that are never copied)
    category: Any = conn.execute(
        text(
            "INSERT INTO categories (organization_id, school_id, key, name, applies_to, report_group) "
            "VALUES (:o, :s, 'canary_key', 'Eventos', 'IN', 'OTHER_INCOME') RETURNING id"
        ),
        {"o": f.org, "s": f.school},
    ).scalar_one()
    conn.execute(text("UPDATE categories SET name = 'Festas' WHERE id = :c"), {"c": category})
    conn.execute(
        text("UPDATE school_settings SET pix_expiration_minutes = 45 WHERE school_id = :s"),
        {"s": f.school},
    )
    account: Any = conn.execute(
        text(
            "INSERT INTO payment_accounts (organization_id, school_id, provider, external_account_id, "
            "status, secret_ref, webhook_secret_hash) "
            "VALUES (:o, :s, 'BB', :e, 'PENDING', :r, :h) RETURNING id"
        ),
        {"o": f.org, "s": f.school, "e": CANARIES["external_account"],
         "r": CANARIES["secret_ref"], "h": webhook_hash},
    ).scalar_one()  # fmt: skip
    conn.execute(
        text("UPDATE payment_accounts SET status = 'INACTIVE' WHERE id = :a"), {"a": account}
    )
    # monthly_closings (insert, report, nothing else is allowed on an active closing)
    closing: Any = conn.execute(
        text(
            "INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id) "
            "VALUES (:o, :s, '2025-03-01', :u) RETURNING id"
        ),
        {"o": f.org, "s": f.school, "u": f.treasurer},
    ).scalar_one()
    conn.execute(
        text("UPDATE monthly_closings SET report_ref = :r WHERE id = :c"),
        {"r": CANARIES["report_ref"], "c": closing},
    )

    # (entity, the first action and how many updates it saw at least)
    expected: dict[str, tuple[uuid.UUID, int]] = {
        "financial_transactions": (cash, 0),
        "contributions": (cash, 1),
        "pix_charges": (charge, 1),
        "expenses": (expense, 2),
        "expense_attachments": (attachment, 0),
        "reimbursements": (reimbursement, 1),
        "refunds": (refund, 1),
        "categories": (category, 1),
        "school_settings": (f.school, 1),
        "payment_accounts": (account, 1),
        "monthly_closings": (closing, 1),
    }
    for table, (entity, updates) in expected.items():
        rows = audit_rows(conn, table, entity)
        actions = [row.action.split(".")[1] for row in rows]
        assert actions[0] == "insert" and actions.count("update") >= updates, (table, actions)
        assert all(row.action.startswith(f"{table}.") for row in rows), table
        assert all(row.actor_user_id == f.treasurer for row in rows[1:]), table

    # The readable reference: the reference_code of the movement, also for its detail rows.
    code = _scalar(conn, "SELECT reference_code FROM financial_transactions WHERE id = :t", t=cash)
    for table in ("financial_transactions", "contributions"):
        assert {row.entity_reference for row in audit_rows(conn, table, cash)} == {code}, table
    assert audit_rows(conn, "categories", category)[0].entity_reference is None

    # Nothing personal in any record of this school: neither the values nor the column names.
    everything = conn.execute(
        text("SELECT before_data, after_data FROM audit_logs WHERE school_id = :s"), {"s": f.school}
    ).all()
    blob = json.dumps([[row[0], row[1]] for row in everything])
    for canary in CANARIES.values():
        assert canary not in blob, canary
    receipt_hash: str = (
        conn.execute(
            text("SELECT receipt_token_hash FROM contributions WHERE transaction_id = :t"),
            {"t": cash},
        ).scalar_one()
        or ""
    )
    for secret in (receipt_hash, webhook_hash):
        assert secret and secret not in blob  # the hash of a secret is never copied
    keys = {key for row in everything for image in row for key in (image or {})}
    assert not (keys & PERSONAL_KEYS)
    # What is kept instead is whether they are set.
    assert {"guardian_name_present", "contributor_email_present", "description_present",
            "purchase_reason_present", "payment_reference_present", "emv_payload_present",
            "report_ref_present", "file_name_present", "review_decision_reason_present",
            "external_account_id_present", "secret_ref_present", "webhook_secret_hash_present",
            "origin_name_present"} <= keys  # fmt: skip
    anonymized = [r for r in audit_rows(conn, "contributions", cash) if r.action.endswith("update")]
    assert anonymized[0].before_data == {
        "guardian_name_present": True,
        "contributor_email_present": True,
    }
    assert anonymized[0].after_data == {
        "guardian_name_present": False,
        "contributor_email_present": False,
    }


def test_accepting_a_review_records_the_amount_before_and_after(
    world: tuple[Connection, Fresh],
) -> None:
    """Condition (3) of the round 2: the audit log shows the value that was adjusted."""
    conn, f = world
    act_as(conn, f.treasurer)
    tx, _ = add_pix_review(conn, f, 3300, 3350)
    conn.execute(
        text(
            "UPDATE contributions SET review_decision_reason = 'accepted by the management' WHERE transaction_id = :t"
        ),
        {"t": tx},
    )
    conn.execute(
        text(
            "UPDATE financial_transactions SET status = 'PAID', amount_cents = 3350, "
            "settled_at = :at WHERE id = :t"
        ),
        {"at": utc(2025, 3, 11, 15), "t": tx},
    )

    accepted = [
        r for r in audit_rows(conn, "financial_transactions", tx) if r.action.endswith("update")
    ][-1]

    assert accepted.before_data["amount_cents"] == 3300
    assert accepted.after_data["amount_cents"] == 3350
    assert accepted.before_data["status"] == "REVIEW_REQUIRED"
    assert accepted.after_data["status"] == "PAID"
