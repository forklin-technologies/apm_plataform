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
    add_refund,
    add_reimbursement,
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


def audit_rows(conn: Connection, entity_type: str, entity_id: uuid.UUID) -> list[Any]:
    return list(
        conn.execute(
            text(
                "SELECT action, actor_user_id, actor_type, request_id, host(ip), before_data, "
                "after_data, organization_id, school_id FROM audit_logs "
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
    "description": "CANARY-DESCRIPTION",
    "vendor": "CANARY-VENDOR",
    "decision_reason": "CANARY-DECISION",
    "refund_reason": "CANARY-REFUND-REASON",
    "payment_reference": "CANARY-PAYMENT-REF",
    "file_name": "CANARY-FILE.pdf",
    "storage_key": "k/CANARY-KEY",
    "emv": "CANARY-EMV-PAYLOAD",
    "reopen_reason": "CANARY-REOPEN-REASON",
    "report_ref": "CANARY-REPORT.pdf",
}
PERSONAL_KEYS = {
    "guardian_name", "student_name", "class_name", "description", "vendor", "decision_reason",
    "reason", "payment_reference", "file_name", "storage_key", "emv_payload", "receipt_token_hash",
    "reopen_reason", "report_ref",
}  # fmt: skip


def test_every_audited_table_writes_its_record_and_none_carries_personal_data(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    act_as(conn, f.treasurer)
    hash_ = token_hash()

    # contributions (insert, anonymize), financial_transactions (insert, settle)
    cash = add_cash_contribution(
        conn, f, 10000, utc(2025, 3, 5, 15), guardian=CANARIES["guardian"],
        student=CANARIES["student"], class_name=CANARIES["class_name"],
    )  # fmt: skip
    conn.execute(
        text("UPDATE contributions SET guardian_name = NULL WHERE transaction_id = :t"), {"t": cash}
    )
    # pix_charges (insert, update) with a receipt token hash
    pix_tx, charge = add_pix_contribution(conn, f, 3000)
    conn.execute(
        text("UPDATE contributions SET guardian_name = guardian_name WHERE transaction_id = :t"),
        {"t": pix_tx},
    )
    conn.execute(
        text("UPDATE pix_charges SET emv_payload = :e WHERE id = :c"),
        {"e": CANARIES["emv"], "c": charge},
    )
    conn.execute(
        text(
            "UPDATE contributions SET receipt_expires_at = receipt_expires_at WHERE transaction_id = :t"
        ),
        {"t": pix_tx},
    )
    # expenses (insert, decide), expense_attachments (insert)
    expense = add_expense(conn, f, 1000)
    conn.execute(
        text("UPDATE expenses SET vendor = :v, description = :d WHERE transaction_id = :t"),
        {"v": CANARIES["vendor"], "d": CANARIES["description"], "t": expense},
    )
    conn.execute(
        text(
            "UPDATE expenses SET approved_by_user_id = :u, decision_reason = :r WHERE transaction_id = :t"
        ),
        {"u": f.treasurer, "r": CANARIES["decision_reason"], "t": expense},
    )
    attachment: Any = conn.execute(
        text(
            "INSERT INTO expense_attachments (organization_id, school_id, transaction_id, storage_key, "
            "file_name, content_type, size_bytes, sha256, uploaded_by_user_id) "
            "VALUES (:o, :s, :t, :k, :n, 'application/pdf', 100, :h, :u) RETURNING id"
        ),
        {"o": f.org, "s": f.school, "t": expense, "k": CANARIES["storage_key"],
         "n": CANARIES["file_name"], "h": hash_, "u": f.staff},
    ).scalar_one()  # fmt: skip
    # reimbursements, refunds (insert, payment reference)
    collab = add_expense(conn, f, 700, paid_by="COLLABORATOR", status="APPROVED")
    reimbursement = add_reimbursement(conn, f, collab, 700)
    conn.execute(
        text("UPDATE reimbursements SET payment_reference = :r WHERE transaction_id = :t"),
        {"r": CANARIES["payment_reference"], "t": reimbursement},
    )
    refund = add_refund(conn, f, cash, "CONTRIBUTION", 500)
    conn.execute(
        text("UPDATE refunds SET payment_reference = :r WHERE transaction_id = :t"),
        {"r": CANARIES["payment_reference"], "t": refund},
    )
    conn.execute(
        text("UPDATE refunds SET payment_reference = payment_reference WHERE transaction_id = :t"),
        {"t": refund},
    )
    # categories, school_settings
    category: Any = conn.execute(
        text(
            "INSERT INTO categories (organization_id, school_id, key, name, applies_to) "
            "VALUES (:o, :s, 'canary_key', 'Eventos', 'IN') RETURNING id"
        ),
        {"o": f.org, "s": f.school},
    ).scalar_one()
    conn.execute(text("UPDATE categories SET name = 'Festas' WHERE id = :c"), {"c": category})
    conn.execute(
        text("UPDATE school_settings SET pix_expiration_minutes = 45 WHERE school_id = :s"),
        {"s": f.school},
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

    expected: dict[str, tuple[uuid.UUID, list[str]]] = {
        "financial_transactions": (cash, ["insert"]),
        "contributions": (cash, ["insert", "update"]),
        "pix_charges": (charge, ["insert", "update"]),
        "expenses": (expense, ["insert", "update", "update"]),
        "expense_attachments": (attachment, ["insert"]),
        "reimbursements": (reimbursement, ["insert", "update"]),
        "refunds": (refund, ["insert", "update"]),
        "categories": (category, ["insert", "update"]),
        "school_settings": (f.school, ["insert", "update"]),
        "monthly_closings": (closing, ["insert", "update"]),
    }
    for table, (entity, actions) in expected.items():
        rows = audit_rows(conn, table, entity)
        assert [row.action.split(".")[1] for row in rows] == actions, table
        assert all(row.action.startswith(f"{table}.") for row in rows), table
        assert all(row.actor_user_id == f.treasurer for row in rows[1:]), (
            table
        )  # acted as the treasurer

    # Nothing personal in any record of this school: neither the values nor the column names.
    everything = conn.execute(
        text("SELECT before_data, after_data FROM audit_logs WHERE school_id = :s"), {"s": f.school}
    ).all()
    blob = json.dumps([[row[0], row[1]] for row in everything])
    for canary in CANARIES.values():
        assert canary not in blob, canary
    receipt_hash: str = conn.execute(
        text("SELECT receipt_token_hash FROM contributions WHERE transaction_id = :t"),
        {"t": pix_tx},
    ).scalar_one()
    assert receipt_hash not in blob  # the secret of the receipt link is never copied
    keys = {key for row in everything for image in row for key in (image or {})}
    assert not (keys & PERSONAL_KEYS)
    # What is kept instead is whether they are set.
    assert {"guardian_name_present", "description_present", "payment_reference_present",
            "emv_payload_present", "report_ref_present", "file_name_present"} <= keys  # fmt: skip
    anonymized = [r for r in audit_rows(conn, "contributions", cash) if r.action.endswith("update")]
    assert anonymized[0].before_data == {"guardian_name_present": True}
    assert anonymized[0].after_data == {"guardian_name_present": False}
