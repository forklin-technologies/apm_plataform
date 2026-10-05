# ruff: noqa: E501  (SQL text)
"""F10: partial approval, the reimbursement of the approved amount, and the devolução limits.

The product document has three values: requested, approved (maybe partial) and reimbursed. Here the
requested one is financial_transactions.amount_cents, the approved one is
expenses.approved_amount_cents, and the reimbursed one is the amount of the REIMBURSEMENT, which
must be the approved one. An approval can be partial only when a collaborator paid.
"""

import uuid

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from tests.financial.support import (
    Fresh,
    add_collaborator_expense,
    add_expense,
    add_refund,
    add_reimbursement,
    check_consistency,
    entries,
    summary,
    utc,
)

MAR = (utc(2025, 3, 1).date(), utc(2025, 3, 31).date())


def _approve(conn: Connection, f: Fresh, expense: uuid.UUID, approved: int | None) -> None:
    conn.execute(
        text(
            "UPDATE expenses SET approved_by_user_id = :u, approved_amount_cents = :a WHERE transaction_id = :t"
        ),
        {"u": f.treasurer, "a": approved, "t": expense},
    )
    conn.execute(
        text("UPDATE financial_transactions SET status = 'APPROVED' WHERE id = :t"), {"t": expense}
    )


def _submitted(conn: Connection, f: Fresh, amount: int, paid_by: str) -> uuid.UUID:
    return add_expense(conn, f, amount, paid_by=paid_by)


def test_a_collaborator_expense_can_be_approved_for_less_and_is_reimbursed_for_the_approved_amount(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense = _submitted(conn, f, 3000, "COLLABORATOR")
    _approve(conn, f, expense, 2200)
    check_consistency(conn)
    reimbursement = add_reimbursement(conn, f, expense)  # the default is the approved amount
    assert (
        conn.execute(
            text("SELECT amount_cents FROM financial_transactions WHERE id = :t"),
            {"t": reimbursement},
        ).scalar_one()
        == 2200
    )
    check_consistency(conn)


@pytest.mark.parametrize("amount", [3000, 2199, 2201, 1])
def test_the_reimbursement_must_be_exactly_the_approved_amount(
    world: tuple[Connection, Fresh], amount: int
) -> None:
    conn, f = world
    expense = _submitted(conn, f, 3000, "COLLABORATOR")
    _approve(conn, f, expense, 2200)
    with (
        pytest.raises(
            DBAPIError, match="needs an APPROVED collaborator expense and its approved amount"
        ),
        conn.begin_nested(),
    ):
        add_reimbursement(conn, f, expense, amount)


def test_a_partial_approval_is_refused_for_an_expense_paid_by_the_apm(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense = _submitted(conn, f, 3000, "APM")
    _approve(conn, f, expense, 2200)
    with (
        pytest.raises(DBAPIError, match="lower only for an expense paid by a collaborator"),
        conn.begin_nested(),
    ):
        check_consistency(conn)


def test_the_apm_expense_is_approved_for_the_full_amount(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    expense = _submitted(conn, f, 3000, "APM")
    _approve(conn, f, expense, 3000)
    check_consistency(conn)


@pytest.mark.parametrize("approved", [3001, 1000000])
def test_the_approved_amount_cannot_exceed_the_requested_one(
    world: tuple[Connection, Fresh], approved: int
) -> None:
    conn, f = world
    expense = _submitted(conn, f, 3000, "COLLABORATOR")
    _approve(conn, f, expense, approved)
    with pytest.raises(DBAPIError, match="at most the requested amount"), conn.begin_nested():
        check_consistency(conn)


@pytest.mark.parametrize("approved", [None, 0, -5])
def test_an_approval_records_a_positive_approved_amount(
    world: tuple[Connection, Fresh], approved: int | None
) -> None:
    conn, f = world
    expense = _submitted(conn, f, 3000, "COLLABORATOR")
    if approved is None:
        _approve(conn, f, expense, None)
        with pytest.raises(DBAPIError, match="at most the requested amount"), conn.begin_nested():
            check_consistency(conn)
    else:
        with (
            pytest.raises(DBAPIError, match="ck_expenses_approved_amount_positive"),
            conn.begin_nested(),
        ):
            _approve(conn, f, expense, approved)


def test_the_approved_amount_is_written_with_the_decision_and_never_rewritten(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense = _submitted(conn, f, 3000, "COLLABORATOR")
    _approve(conn, f, expense, 2200)
    with pytest.raises(DBAPIError, match="already set"), conn.begin_nested():
        conn.execute(
            text("UPDATE expenses SET approved_amount_cents = 2500 WHERE transaction_id = :t"),
            {"t": expense},
        )


def test_a_partially_approved_expense_moves_the_cash_by_the_approved_amount_only(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_collaborator_expense(
        conn, f, 3000, approved_amount=2200, reimbursed_at=utc(2025, 3, 21, 15)
    )

    assert summary(conn, f, *MAR) == (0, 0, 2200, -2200, 1)
    (row,) = entries(conn, f, *MAR)
    assert (row.kind, row.signed_amount_cents, row.status_label) == (
        "REIMBURSEMENT",
        -2200,
        "REIMBURSED",
    )


# --- devoluções: money back, up to what was paid ----------------------------------------------------


def _paid_apm_expense(conn: Connection, f: Fresh, amount: int = 1500) -> uuid.UUID:
    return add_expense(conn, f, amount, status="PAID", settled_at=utc(2025, 3, 5, 15))


def test_returns_of_an_expense_add_up_to_at_most_the_approved_amount(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense = _paid_apm_expense(conn, f)
    add_refund(conn, f, 1000, parent=expense, parent_kind="EXPENSE")
    with (
        pytest.raises(DBAPIError, match="returns would exceed the amount paid"),
        conn.begin_nested(),
    ):
        add_refund(conn, f, 501, parent=expense, parent_kind="EXPENSE")
    add_refund(
        conn,
        f,
        500,
        parent=expense,
        parent_kind="EXPENSE",
        status="CONFIRMED",
        settled_at=utc(2025, 3, 6, 15),
    )  # exactly what is left
    with (
        pytest.raises(DBAPIError, match="returns would exceed the amount paid"),
        conn.begin_nested(),
    ):
        add_refund(conn, f, 1, parent=expense, parent_kind="EXPENSE")


def test_a_return_of_a_partially_approved_expense_is_limited_to_the_approved_amount(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense, _ = add_collaborator_expense(
        conn, f, 3000, approved_amount=2200, reimbursed_at=utc(2025, 3, 5, 15)
    )
    add_refund(conn, f, 2200, parent=expense, parent_kind="EXPENSE")  # not the requested 3000
    with (
        pytest.raises(DBAPIError, match="returns would exceed the amount paid"),
        conn.begin_nested(),
    ):
        add_refund(conn, f, 1, parent=expense, parent_kind="EXPENSE")


def test_returns_of_a_reimbursement_are_limited_to_the_amount_reimbursed(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    _, reimbursement = add_collaborator_expense(
        conn, f, 3000, approved_amount=2200, reimbursed_at=utc(2025, 3, 5, 15)
    )
    add_refund(conn, f, 2000, parent=reimbursement, parent_kind="REIMBURSEMENT")
    with (
        pytest.raises(DBAPIError, match="returns would exceed the amount paid"),
        conn.begin_nested(),
    ):
        add_refund(conn, f, 201, parent=reimbursement, parent_kind="REIMBURSEMENT")
    add_refund(conn, f, 200, parent=reimbursement, parent_kind="REIMBURSEMENT")


def test_open_and_confirmed_returns_count_and_a_rejected_one_frees_its_amount(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense = _paid_apm_expense(conn, f, 1000)
    requested = add_refund(conn, f, 400, parent=expense, parent_kind="EXPENSE")  # REQUESTED counts
    add_refund(
        conn, f, 300, parent=expense, parent_kind="EXPENSE", status="AWAITING_CONFIRMATION"
    )  # so does this
    with (
        pytest.raises(DBAPIError, match="returns would exceed the amount paid"),
        conn.begin_nested(),
    ):
        add_refund(conn, f, 400, parent=expense, parent_kind="EXPENSE")
    conn.execute(
        text("UPDATE financial_transactions SET status = 'REJECTED' WHERE id = :t"),
        {"t": requested},
    )
    add_refund(conn, f, 400, parent=expense, parent_kind="EXPENSE")


@pytest.mark.parametrize("status", ["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CANCELLED"])
def test_only_a_paid_expense_can_be_returned(world: tuple[Connection, Fresh], status: str) -> None:
    conn, f = world
    expense = add_expense(conn, f, 1500, status=status)
    with (
        pytest.raises(DBAPIError, match="only a PAID expense or reimbursement can be returned"),
        conn.begin_nested(),
    ):
        add_refund(conn, f, 100, parent=expense, parent_kind="EXPENSE")


def test_a_pending_reimbursement_cannot_be_returned_yet(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    _, reimbursement = add_collaborator_expense(conn, f, 3000)
    with (
        pytest.raises(DBAPIError, match="only a PAID expense or reimbursement can be returned"),
        conn.begin_nested(),
    ):
        add_refund(conn, f, 100, parent=reimbursement, parent_kind="REIMBURSEMENT")


def test_a_return_may_have_no_parent_and_then_has_no_limit_to_check(
    world: tuple[Connection, Fresh],
) -> None:
    """A payment made by mistake, returned: there is no expense to measure it against."""
    conn, f = world
    add_refund(
        conn,
        f,
        99999,
        reason="paid twice by mistake",
        origin_type="OTHER",
        origin_name="Pagador anônimo",
    )
    check_consistency(conn)


def test_a_return_needs_a_reason_of_at_least_three_characters(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    with pytest.raises(DBAPIError, match="ck_refunds_reason_length"), conn.begin_nested():
        add_refund(conn, f, 100, reason="ab")


def test_the_condition_2_of_the_first_round_is_gone_a_collaborator_expense_can_be_returned(
    world: tuple[Connection, Fresh],
) -> None:
    """The old rule ("a refund of an expense only when the APM paid it") was revoked in revision 2."""
    conn, f = world
    expense, _ = add_collaborator_expense(conn, f, 3000, reimbursed_at=utc(2025, 3, 5, 15))
    add_refund(conn, f, 500, parent=expense, parent_kind="EXPENSE")
