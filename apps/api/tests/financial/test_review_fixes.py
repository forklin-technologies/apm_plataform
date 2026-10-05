# ruff: noqa: E501  (SQL text)
"""F17: the holes the code review of 2026-10-05 found in the ledger invariants, each one reproduced
against the database before it was closed.

1. an expense with a live reimbursement is not cancelled (and a reimbursement cannot outlive it);
2. the returns of an expense and of its reimbursement are the same money and share one cap;
3. a Pix charge cannot be PAID (or in review) while its contribution says something else;
4. every school is born with the default categories.
"""

import uuid

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.seed_financial import DEFAULT_CATEGORIES as SEED_DEFAULTS
from tests.financial.support import (
    Fresh,
    add_collaborator_expense,
    add_expense,
    add_pix_contribution,
    add_refund,
    add_reimbursement,
    check_consistency,
    end_to_end_id,
    set_status,
    utc,
)

# --- 1. an expense and its reimbursement ----------------------------------------------------------


def test_an_expense_with_a_pending_reimbursement_is_not_cancelled(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense, _ = add_collaborator_expense(conn, f, 3000)  # APPROVED, reimbursement PENDING
    with pytest.raises(DBAPIError, match="live reimbursement"), conn.begin_nested():
        set_status(conn, expense, "CANCELLED")


def test_an_expense_with_a_paid_reimbursement_is_not_cancelled(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense = add_expense(conn, f, 3000, paid_by="COLLABORATOR", status="APPROVED")
    add_reimbursement(conn, f, expense, status="PAID", settled_at=utc(2025, 3, 10), reference="b1")
    with pytest.raises(DBAPIError, match="live reimbursement"), conn.begin_nested():
        set_status(conn, expense, "CANCELLED")
    # The ending the model wants still works: the expense follows its reimbursement to PAID.
    set_status(conn, expense, "PAID", utc(2025, 3, 10))
    check_consistency(conn)


def test_cancelling_the_reimbursement_first_lets_the_expense_be_cancelled(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense, reimbursement = add_collaborator_expense(conn, f, 3000)
    set_status(conn, reimbursement, "CANCELLED")
    set_status(conn, expense, "CANCELLED")
    check_consistency(conn)


def test_both_can_be_cancelled_in_one_transaction_in_any_order(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense, reimbursement = add_collaborator_expense(conn, f, 3000)
    set_status(conn, reimbursement, "CANCELLED")
    check_consistency(conn)
    assert (
        conn.execute(
            text("SELECT status FROM financial_transactions WHERE id = :t"), {"t": reimbursement}
        ).scalar_one()
        == "CANCELLED"
    )


# --- 2. one cap for the returns of the family ----------------------------------------------------


def test_the_returns_of_an_expense_and_of_its_reimbursement_share_one_cap(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense, reimbursement = add_collaborator_expense(
        conn, f, 10000, reimbursed_at=utc(2025, 3, 10)
    )
    add_refund(conn, f, 6000, parent=expense, parent_kind="EXPENSE")
    with pytest.raises(DBAPIError, match="returns would exceed"), conn.begin_nested():
        add_refund(conn, f, 4001, parent=reimbursement, parent_kind="REIMBURSEMENT")
    add_refund(conn, f, 4000, parent=reimbursement, parent_kind="REIMBURSEMENT")
    with pytest.raises(DBAPIError, match="returns would exceed"), conn.begin_nested():
        add_refund(conn, f, 1, parent=expense, parent_kind="EXPENSE")
    with pytest.raises(DBAPIError, match="returns would exceed"), conn.begin_nested():
        add_refund(conn, f, 1, parent=reimbursement, parent_kind="REIMBURSEMENT")
    check_consistency(conn)


def test_the_money_cannot_come_back_twice_in_the_statement(
    world: tuple[Connection, Fresh],
) -> None:
    """The case of the review: R$ 100 out, two confirmed returns of R$ 100 each."""
    conn, f = world
    expense, reimbursement = add_collaborator_expense(
        conn, f, 10000, reimbursed_at=utc(2025, 3, 10)
    )
    add_refund(
        conn, f, 10000, parent=expense, parent_kind="EXPENSE", status="CONFIRMED",
        settled_at=utc(2025, 3, 12),
    )  # fmt: skip
    with pytest.raises(DBAPIError, match="returns would exceed"), conn.begin_nested():
        add_refund(
            conn, f, 10000, parent=reimbursement, parent_kind="REIMBURSEMENT",
            status="CONFIRMED", settled_at=utc(2025, 3, 12),
        )  # fmt: skip


# --- 3. the charge side of the charge <-> contribution consistency --------------------------------


def _charge_paid(conn: Connection, charge: uuid.UUID, amount: int) -> None:
    conn.execute(
        text(
            "UPDATE pix_charges SET status = 'PAID', end_to_end_id = :e, paid_at = now(), "
            "received_amount_cents = :a WHERE id = :c"
        ),
        {"e": end_to_end_id(), "a": amount, "c": charge},
    )


@pytest.mark.parametrize("contribution_status", ["PENDING_PAYMENT", "EXPIRED", "CANCELLED"])
def test_a_paid_charge_needs_its_contribution_paid(
    world: tuple[Connection, Fresh], contribution_status: str
) -> None:
    conn, f = world
    transaction, charge = add_pix_contribution(conn, f, 3000)
    if contribution_status != "PENDING_PAYMENT":
        set_status(conn, transaction, contribution_status)
    with (
        pytest.raises(DBAPIError, match="PAID Pix charge needs its contribution PAID"),
        conn.begin_nested(),
    ):
        _charge_paid(conn, charge, 3000)
        check_consistency(conn)


def test_the_order_of_the_two_updates_does_not_matter(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    transaction, charge = add_pix_contribution(conn, f, 3000)
    _charge_paid(conn, charge, 3000)
    set_status(conn, transaction, "PAID", utc(2025, 3, 10))
    check_consistency(conn)


def test_a_charge_in_review_needs_its_contribution_in_review(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    _, charge = add_pix_contribution(conn, f, 3000)
    with (
        pytest.raises(DBAPIError, match="charge in review needs its contribution in review"),
        conn.begin_nested(),
    ):
        conn.execute(
            text(
                "UPDATE pix_charges SET status = 'REVIEW_REQUIRED', end_to_end_id = :e, "
                "paid_at = now(), received_amount_cents = 30000, "
                "divergence_reason = 'amount differs' WHERE id = :c"
            ),
            {"e": end_to_end_id(), "c": charge},
        )
        check_consistency(conn)


# --- 4. the default categories --------------------------------------------------------------------


def test_a_school_is_born_with_the_default_categories(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    rows = conn.execute(
        text(
            "SELECT key, applies_to, report_group, requires_approval FROM categories "
            "WHERE school_id = :s"
        ),
        {"s": f.school},
    ).all()
    assert {(r.key, r.applies_to, r.report_group) for r in rows} == {
        (key, applies_to, group) for key, _, applies_to, group in SEED_DEFAULTS
    }
    assert {r.key for r in rows if not r.requires_approval} == {"bank_fees"}
