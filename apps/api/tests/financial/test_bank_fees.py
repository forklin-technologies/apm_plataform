# ruff: noqa: E501  (SQL text)
"""F15: bank fees (ADR-015, revision 3).

The statement of the bank is full of fees ("Tarifa Pix Enviado", "Tarifa Pacote de Servicos") that
nobody approves: the bank already took the money. They are expenses of the APM in a category that
needs no approval (categories.requires_approval = false, only for report group BANK_FEES). Rules
checked here, in the database:

  * the fee is born APPROVED, from the bank, with no attachment, no reason and no payment method, and
    the database fills in the approved amount; whoever records it settles it (APPROVED to PAID);
  * it moves the cash like any expense paid by the APM and shows in its own report group;
  * it is audited like every other change;
  * nothing else can use the exemption: an APPROVED start anywhere else is refused, a fee is never
    sent for review, never paid by a collaborator and never has an approver.
"""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction
from tests.financial.support import (
    Fresh,
    add_attachment,
    add_bank_fee,
    add_cash_contribution,
    add_expense,
    add_refund,
    add_transaction,
    bank_fees_category,
    check_consistency,
    entries,
    set_status,
    summary_row,
    utc,
)

WHEN = utc(2025, 3, 10, 15)
DAY = (WHEN.date(), WHEN.date())


def status_of(conn: Connection, tx: uuid.UUID) -> str:
    return str(
        conn.execute(
            text("SELECT status FROM financial_transactions WHERE id = :t"), {"t": tx}
        ).scalar_one()
    )


def record_fee(
    conn: Connection,
    f: Fresh,
    amount: int = 590,
    *,
    status: str = "APPROVED",
    origin_type: str = "BANK",
) -> uuid.UUID:
    """The ledger row and the expense row of a fee, with the parameters the tests vary."""
    tx = add_transaction(
        conn, f, kind="EXPENSE", direction="OUT", amount=amount, status=status,
        category=bank_fees_category(conn, f), created_by=f.treasurer, origin_type=origin_type,
    )  # fmt: skip
    conn.execute(
        text(
            "INSERT INTO expenses (transaction_id, organization_id, school_id, description, paid_by, "
            "submitted_by_user_id) VALUES (:t, :o, :s, 'Tarifa Pix Enviado', 'APM', :who)"
        ),
        {"t": tx, "o": f.org, "s": f.school, "who": f.treasurer},
    )
    return tx


# --- the life of a fee ------------------------------------------------------------------------------


def test_a_fee_is_recorded_approved_without_an_approver_and_settled_by_whoever_records_it(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    fee: Any = record_fee(conn, f, 590)

    row = conn.execute(
        text(
            "SELECT e.approved_amount_cents, e.approved_by_user_id, e.approved_at, e.paid_by, "
            "e.purchase_reason, e.payment_method FROM expenses e WHERE e.transaction_id = :t"
        ),
        {"t": fee},
    ).one()
    assert row.approved_amount_cents == 590  # filled in by the database, never by the caller
    assert (row.approved_by_user_id, row.approved_at) == (None, None)
    assert (row.purchase_reason, row.payment_method) == (None, None)
    assert status_of(conn, fee) == "APPROVED"

    set_status(conn, fee, "PAID", WHEN)  # whoever records it settles it: no other actor in between
    check_consistency(conn)
    assert status_of(conn, fee) == "PAID"


def test_a_fee_moves_the_cash_and_has_a_report_group_of_its_own(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 10000, WHEN)
    add_bank_fee(conn, f, 590, settled_at=WHEN)
    add_bank_fee(conn, f, 1000, settled_at=WHEN, description="Tarifa Pacote de Servicos")
    add_expense(conn, f, 2000, status="PAID", settled_at=WHEN)
    check_consistency(conn)

    s = summary_row(conn, f, *DAY)
    # the decision of the architect: expenses_out includes the fees, the breakdown separates them
    assert s.expenses_out_cents == 590 + 1000 + 2000
    assert s.closing_balance_cents == 10000 - 3590
    rows = entries(conn, f, *DAY)
    fees = [r for r in rows if r.report_group == "BANK_FEES"]
    assert [(r.category_key, r.direction, r.amount_cents) for r in fees] == [
        ("bank_fees", "OUT", 590),
        ("bank_fees", "OUT", 1000),
    ]
    assert {r.origin_type for r in fees} == {"BANK"}

    breakdown: Any = conn.execute(
        text("SELECT closing_breakdown(:s, :a, :b)"), {"s": f.school, "a": DAY[0], "b": DAY[1]}
    ).scalar_one()
    assert breakdown["BANK_FEES"] == {
        "in": 0,
        "out": 1590,
        "count": 2,
        "categories": {"bank_fees": {"in": 0, "out": 1590, "count": 2}},
    }
    assert breakdown["EXPENSES_REIMBURSEMENTS"]["out"] == 2000


def test_a_fee_waiting_to_be_settled_is_payable_not_cash(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    fee: Any = record_fee(conn, f, 590)
    check_consistency(conn)

    assert summary_row(conn, f, *DAY).total_out_cents == 0
    section: Any = conn.execute(
        text("SELECT section FROM statement_pending(:s) WHERE transaction_id = :t"),
        {"s": f.school, "t": fee},
    ).scalar_one()
    assert section == "PAYABLE"


def test_a_fee_recorded_by_mistake_can_be_cancelled_before_it_is_settled(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    fee: Any = record_fee(conn, f, 590)
    set_status(conn, fee, "CANCELLED")
    check_consistency(conn)
    assert summary_row(conn, f, *DAY).total_out_cents == 0


def test_the_bank_can_give_a_fee_back_as_a_refund(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    fee: Any = add_bank_fee(conn, f, 590, settled_at=WHEN)
    add_refund(
        conn,
        f,
        590,
        parent=fee,
        parent_kind="EXPENSE",
        status="CONFIRMED",
        settled_at=WHEN,
        origin_type="BANK",
    )
    check_consistency(conn)
    s = summary_row(conn, f, *DAY)
    assert (s.refunds_in_cents, s.expenses_out_cents, s.closing_balance_cents) == (590, 590, 0)


# --- it is audited ---------------------------------------------------------------------------------------


def test_a_fee_is_audited_with_its_actor_from_insert_to_settlement(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    conn.execute(
        text(
            "SELECT set_config('app.user_id', :u, true), set_config('app.actor_type', 'USER', true)"
        ),
        {"u": str(f.treasurer)},
    )
    fee: Any = add_bank_fee(conn, f, 590, settled_at=WHEN)

    rows = list(
        conn.execute(
            text(
                "SELECT action, actor_user_id, actor_type, before_data, after_data, entity_reference "
                "FROM audit_logs WHERE entity_id = :t AND entity_type IN ('financial_transactions', 'expenses') "
                "ORDER BY occurred_at, id"
            ),
            {"t": fee},
        )
    )
    actions = [r.action for r in rows]
    assert actions == [
        "financial_transactions.insert",
        "expenses.insert",
        "financial_transactions.update",
    ]
    assert all(r.actor_user_id == f.treasurer and r.actor_type == "USER" for r in rows)
    insert, expense, settle = rows
    assert insert.after_data["status"] == "APPROVED" and insert.after_data["origin_type"] == "BANK"
    assert expense.after_data["approved_amount_cents"] == 590
    assert expense.after_data["approved_by_user_id"] is None
    assert (settle.before_data["status"], settle.after_data["status"]) == ("APPROVED", "PAID")
    assert insert.entity_reference == expense.entity_reference == settle.entity_reference
    assert "Tarifa" not in json.dumps(
        [expense.before_data, expense.after_data]
    )  # free text is only "present"


def test_the_category_without_approval_is_audited_too(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    category: Any = bank_fees_category(conn, f)
    after: Any = conn.execute(
        text(
            "SELECT after_data FROM audit_logs WHERE entity_type = 'categories' AND entity_id = :c"
        ),
        {"c": category},
    ).scalar_one()
    assert (after["report_group"], after["requires_approval"]) == ("BANK_FEES", False)


# --- nothing else can use the exemption ---------------------------------------------------------------------


@pytest.mark.parametrize("status", ["DRAFT", "SUBMITTED", "PAID", "REJECTED"])
def test_an_expense_in_the_category_without_approval_is_born_approved_only(
    world: tuple[Connection, Fresh], status: str
) -> None:
    conn, f = world
    with (
        pytest.raises(DBAPIError, match="born APPROVED and its origin is the bank"),
        conn.begin_nested(),
    ):
        record_fee(conn, f, status=status)


@pytest.mark.parametrize("origin", ["APM", "TEACHER", "OTHER"])
def test_the_origin_of_a_fee_is_the_bank(world: tuple[Connection, Fresh], origin: str) -> None:
    conn, f = world
    with (
        pytest.raises(DBAPIError, match="born APPROVED and its origin is the bank"),
        conn.begin_nested(),
    ):
        record_fee(conn, f, origin_type=origin)


def test_an_approved_start_is_refused_in_a_category_that_needs_approval(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    with (
        pytest.raises(DBAPIError, match="a new EXPENSE cannot start as APPROVED"),
        conn.begin_nested(),
    ):
        add_transaction(
            conn, f, kind="EXPENSE", direction="OUT", amount=590, status="APPROVED",
            created_by=f.treasurer, origin_type="BANK",
        )  # fmt: skip


def test_a_fee_is_never_paid_by_a_collaborator(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx = add_transaction(
        conn, f, kind="EXPENSE", direction="OUT", amount=590, status="APPROVED",
        category=bank_fees_category(conn, f), created_by=f.treasurer, origin_type="BANK",
    )  # fmt: skip
    with pytest.raises(DBAPIError, match="is paid by the APM"), conn.begin_nested():
        conn.execute(
            text(
                "INSERT INTO expenses (transaction_id, organization_id, school_id, description, paid_by, "
                "submitted_by_user_id) VALUES (:t, :o, :s, 'Tarifa', 'COLLABORATOR', :who)"
            ),
            {"t": tx, "o": f.org, "s": f.school, "who": f.treasurer},
        )


def test_a_fee_never_has_an_approver(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    fee: Any = record_fee(conn, f)
    conn.execute(
        text("UPDATE expenses SET approved_by_user_id = :u WHERE transaction_id = :t"),
        {"u": f.admin, "t": fee},
    )
    with pytest.raises(DBAPIError, match="has no approver"), conn.begin_nested():
        check_consistency(conn)


def test_a_draft_cannot_be_moved_into_the_category_without_approval(
    world: tuple[Connection, Fresh],
) -> None:
    """The draft is editable, but a request that needs an approver cannot reach the exemption by
    changing its category: the check at commit refuses the combination."""
    conn, f = world
    draft = add_expense(conn, f, 590, status="DRAFT")
    conn.execute(
        text("UPDATE financial_transactions SET category_id = :c WHERE id = :t"),
        {"c": bank_fees_category(conn, f), "t": draft},
    )
    with pytest.raises(DBAPIError, match="never sent for review"), conn.begin_nested():
        check_consistency(conn)


def test_a_settled_expense_cannot_be_moved_into_the_category_without_approval(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    paid = add_expense(conn, f, 590, status="PAID", settled_at=WHEN)
    with pytest.raises(DBAPIError, match="is final"), conn.begin_nested():
        conn.execute(
            text("UPDATE financial_transactions SET category_id = :c WHERE id = :t"),
            {"c": bank_fees_category(conn, f), "t": paid},
        )


def test_a_fee_cannot_be_edited_after_it_is_born(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    fee: Any = record_fee(conn, f, 590)
    with pytest.raises(DBAPIError, match="amount changes only while"), conn.begin_nested():
        conn.execute(
            text("UPDATE financial_transactions SET amount_cents = 1 WHERE id = :t"), {"t": fee}
        )
    with pytest.raises(DBAPIError, match="edited only while it is a draft"), conn.begin_nested():
        conn.execute(
            text("UPDATE expenses SET description = 'other' WHERE transaction_id = :t"), {"t": fee}
        )


def test_attachments_are_welcome_but_not_required(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    fee: Any = record_fee(conn, f, 590)
    add_attachment(conn, f, fee, "PAYMENT_PROOF")
    check_consistency(conn)


# --- the category ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("applies_to", "group", "approval", "constraint"),
    [
        ("OUT", "EXPENSES_REIMBURSEMENTS", False, "ck_categories_no_approval_only_for_bank_fees"),
        ("IN", "CONTRIBUTIONS", False, "ck_categories_no_approval_only_for_bank_fees"),
        ("IN", "BANK_FEES", True, "ck_categories_group_matches_direction"),
        ("IN", "BANK_FEES", False, "ck_categories_group_matches_direction"),
    ],
)
def test_only_an_outgoing_bank_fees_category_can_go_without_approval(
    world: tuple[Connection, Fresh], applies_to: str, group: str, approval: bool, constraint: str
) -> None:
    conn, f = world
    with pytest.raises(IntegrityError, match=constraint), conn.begin_nested():
        conn.execute(
            text(
                "INSERT INTO categories (organization_id, school_id, key, name, applies_to, report_group, "
                "requires_approval) VALUES (:o, :s, 'x_cat', 'X', :a, :g, :r)"
            ),
            {"o": f.org, "s": f.school, "a": applies_to, "g": group, "r": approval},
        )


def test_a_category_that_needs_approval_is_the_default(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    assert (
        conn.execute(
            text("SELECT requires_approval FROM categories WHERE id = :c"), {"c": f.cat_out}
        ).scalar_one()
        is True
    )


def test_requires_approval_never_changes(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    category: Any = bank_fees_category(conn, f)
    with pytest.raises(DBAPIError, match="requires_approval can never change"), conn.begin_nested():
        conn.execute(
            text("UPDATE categories SET requires_approval = true WHERE id = :c"), {"c": category}
        )
    with pytest.raises(DBAPIError, match="requires_approval can never change"), conn.begin_nested():
        conn.execute(
            text("UPDATE categories SET requires_approval = false WHERE id = :c"), {"c": f.cat_out}
        )


# --- as the application role ------------------------------------------------------------------------------------


def test_the_application_role_records_and_settles_a_fee_under_row_level_security(
    app_engine: Engine, tenants: Tenants
) -> None:
    """The exemption works for apm_app inside a school, with only the columns it is granted: the
    database, not the caller, fills in the approved amount."""
    school_context = TenantContext(tenants.org_a, tenants.school_a1)
    with transaction(app_engine, context=school_context) as conn:
        category: Any = conn.execute(
            text(
                "INSERT INTO categories (organization_id, school_id, key, name, applies_to, report_group, "
                "requires_approval) VALUES (:o, :s, 'bank_fees', 'Tarifas bancárias', 'OUT', 'BANK_FEES', false) "
                "RETURNING id"
            ),
            {"o": tenants.org_a, "s": tenants.school_a1},
        ).scalar_one()
        conn.execute(
            text("SELECT set_config('app.user_id', :u, true)"), {"u": str(tenants.user_a1)}
        )
        fee: Any = conn.execute(
            text(
                "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, amount_cents, "
                "status, category_id, origin_type, created_by_user_id) "
                "VALUES (:o, :s, 'EXPENSE', 'OUT', 590, 'APPROVED', :c, 'BANK', :u) RETURNING id"
            ),
            {"o": tenants.org_a, "s": tenants.school_a1, "c": category, "u": tenants.user_a1},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO expenses (transaction_id, organization_id, school_id, description, paid_by, "
                "submitted_by_user_id) VALUES (:t, :o, :s, 'Tarifa Pix Enviado', 'APM', :u)"
            ),
            {"t": fee, "o": tenants.org_a, "s": tenants.school_a1, "u": tenants.user_a1},
        )
        conn.execute(
            text(
                "UPDATE financial_transactions SET status = 'PAID', settled_at = :at WHERE id = :t"
            ),
            {"at": WHEN, "t": fee},
        )
        conn.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")

        approved: Any = conn.execute(
            text("SELECT approved_amount_cents FROM expenses WHERE transaction_id = :t"), {"t": fee}
        ).scalar_one()
        assert approved == 590

    # ... and it cannot choose the approved amount itself: the column is not granted
    with (
        transaction(app_engine, context=school_context) as conn,
        pytest.raises(DBAPIError, match="permission denied"),
    ):
        conn.execute(
            text(
                "INSERT INTO expenses (transaction_id, organization_id, school_id, description, paid_by, "
                "submitted_by_user_id, approved_amount_cents) "
                "SELECT transaction_id, organization_id, school_id, 'x', 'APM', submitted_by_user_id, 1 "
                "FROM expenses LIMIT 1"
            )
        )
