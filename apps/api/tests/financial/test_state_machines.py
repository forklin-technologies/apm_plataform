# ruff: noqa: E501  (SQL text)
"""F9: the state machines, in the database.

Every pair of statuses of a kind is tried: the valid transitions of the ADR (revision 2) must work
and every other one must be refused, the initial status of a new row is checked, and the request of
an expense (amount, purpose, date, texts) can be edited only while it is a draft or being
corrected. The expected edges are written out here by hand, independently of the migration.
"""

import uuid

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from tests.financial.support import (
    Fresh,
    add_attachment,
    add_cash_contribution,
    add_collaborator_expense,
    add_expense,
    add_pix_contribution,
    add_pix_review,
    add_refund,
    add_reimbursement,
    add_transaction,
    check_consistency,
    set_status,
    utc,
)

STATUSES = {
    "CONTRIBUTION": ["PENDING_PAYMENT", "PAID", "EXPIRED", "CANCELLED", "REVIEW_REQUIRED"],
    "EXPENSE": [
        "DRAFT",
        "SUBMITTED",
        "CORRECTION_REQUESTED",
        "APPROVED",
        "REJECTED",
        "PAID",
        "CANCELLED",
    ],
    "REIMBURSEMENT": ["PENDING", "PAID", "CANCELLED"],
    "REFUND": ["REQUESTED", "AWAITING_CONFIRMATION", "CONFIRMED", "REJECTED"],
}
VALID_EDGES: dict[str, dict[str, set[str]]] = {
    "CONTRIBUTION": {
        "PENDING_PAYMENT": {"PAID", "EXPIRED", "CANCELLED", "REVIEW_REQUIRED"},
        "REVIEW_REQUIRED": {"PAID", "CANCELLED"},
    },
    "EXPENSE": {
        "DRAFT": {"SUBMITTED", "CANCELLED"},
        "SUBMITTED": {"APPROVED", "REJECTED", "CORRECTION_REQUESTED"},
        "CORRECTION_REQUESTED": {"SUBMITTED", "CANCELLED"},
        "APPROVED": {"PAID", "CANCELLED"},
    },
    "REIMBURSEMENT": {"PENDING": {"PAID", "CANCELLED"}},
    "REFUND": {
        "REQUESTED": {"AWAITING_CONFIRMATION", "REJECTED"},
        "AWAITING_CONFIRMATION": {"CONFIRMED", "REJECTED"},
    },
}
INITIAL = {
    "CONTRIBUTION": {"PENDING_PAYMENT", "PAID"},
    "EXPENSE": {"DRAFT", "SUBMITTED"},
    "REIMBURSEMENT": {"PENDING"},
    "REFUND": {"REQUESTED"},
}
SETTLED = {"PAID", "CONFIRMED"}
WHEN = utc(2025, 3, 10, 15)


def row_in_state(conn: Connection, f: Fresh, kind: str, status: str) -> uuid.UUID:
    if kind == "CONTRIBUTION":
        if status == "PAID":
            return add_cash_contribution(conn, f, 3300, WHEN)
        if status == "REVIEW_REQUIRED":
            return add_pix_review(conn, f, 3300, 3350)[0]
        tx = add_pix_contribution(conn, f, 3300)[0]
        if status != "PENDING_PAYMENT":
            set_status(conn, tx, status)
        return tx
    if kind == "EXPENSE":
        return add_expense(
            conn, f, 3300, status=status, settled_at=WHEN if status == "PAID" else None
        )
    if kind == "REIMBURSEMENT":
        if status == "PAID":
            return add_collaborator_expense(conn, f, 3300, reimbursed_at=WHEN)[1]
        if status == "CANCELLED":
            expense = add_expense(conn, f, 3300, paid_by="COLLABORATOR", status="APPROVED")
            return add_reimbursement(conn, f, expense, status="CANCELLED")
        return add_collaborator_expense(conn, f, 3300)[1]
    return add_refund(
        conn, f, 300, status=status, settled_at=WHEN if status == "CONFIRMED" else None
    )


PAIRS = [
    pytest.param(kind, source, target, id=f"{kind}:{source}->{target}")
    for kind, statuses in STATUSES.items()
    for source in statuses
    for target in statuses
    if source != target
]


@pytest.mark.parametrize(("kind", "source", "target"), PAIRS)
def test_a_transition_works_if_and_only_if_the_adr_allows_it(
    world: tuple[Connection, Fresh], kind: str, source: str, target: str
) -> None:
    conn, f = world
    tx = row_in_state(conn, f, kind, source)
    allowed = target in VALID_EDGES[kind].get(source, set())
    if (
        source == "REVIEW_REQUIRED"
    ):  # the management decides: the reason first, then (to accept) the amount
        conn.execute(
            text(
                "UPDATE contributions SET review_decision_reason = 'decided by the management' WHERE transaction_id = :t"
            ),
            {"t": tx},
        )
    amount: int = conn.execute(
        text("SELECT amount_cents FROM financial_transactions WHERE id = :t"), {"t": tx}
    ).scalar_one()
    if (source, target) == ("REVIEW_REQUIRED", "PAID"):
        amount = 3350  # accepting a review sets the amount received
    sql = text(
        "UPDATE financial_transactions SET status = :to, settled_at = :at, amount_cents = :amount WHERE id = :t"
    )
    params = {"to": target, "at": WHEN if target in SETTLED else None, "amount": amount, "t": tx}

    if allowed:
        conn.execute(sql, params)
        assert (
            conn.execute(
                text("SELECT status FROM financial_transactions WHERE id = :t"), {"t": tx}
            ).scalar_one()
            == target
        )
    else:
        with pytest.raises(DBAPIError, match="cannot go from|is final"), conn.begin_nested():
            conn.execute(sql, params)


def test_every_pair_is_covered_and_the_edges_are_the_ones_of_the_adr() -> None:
    assert len(PAIRS) == 20 + 42 + 6 + 12
    assert sum(len(targets) for edges in VALID_EDGES.values() for targets in edges.values()) == 21


# --- the initial status ---------------------------------------------------------------------------

INITIAL_CASES = [
    pytest.param(kind, status, id=f"{kind}:{status}")
    for kind, statuses in STATUSES.items()
    for status in statuses
]


@pytest.mark.parametrize(("kind", "status"), INITIAL_CASES)
def test_a_new_row_may_only_start_in_its_initial_states(
    world: tuple[Connection, Fresh], kind: str, status: str
) -> None:
    conn, f = world
    parent = parent_kind = None
    direction = {"CONTRIBUTION": "IN", "EXPENSE": "OUT", "REIMBURSEMENT": "OUT", "REFUND": "IN"}[
        kind
    ]
    if kind == "REIMBURSEMENT":
        parent = add_expense(conn, f, 3300, paid_by="COLLABORATOR", status="APPROVED")
        parent_kind = "EXPENSE"

    def create() -> None:
        add_transaction(
            conn, f, kind=kind, direction=direction, amount=3300, status=status,
            created_by=f.treasurer, settled_at=WHEN if status in SETTLED else None,
            parent=parent, parent_kind=parent_kind,
        )  # fmt: skip

    if status in INITIAL[kind]:
        create()
    else:
        with pytest.raises(DBAPIError, match="cannot start as"), conn.begin_nested():
            create()


# --- the request of an expense: editable only as a draft or being corrected --------------------------

EDITABLE = {"DRAFT", "CORRECTION_REQUESTED"}
EXPENSE_STATES = STATUSES["EXPENSE"]


@pytest.mark.parametrize("status", EXPENSE_STATES)
@pytest.mark.parametrize(
    "assignment",
    [
        "amount_cents = amount_cents + 100",
        "category_id = :other",
        "occurred_at = occurred_at + interval '1 day'",
    ],
    ids=["amount", "category", "date"],
)
def test_the_value_the_purpose_and_the_date_change_only_in_draft_or_correction(
    world: tuple[Connection, Fresh], status: str, assignment: str
) -> None:
    conn, f = world
    tx = row_in_state(conn, f, "EXPENSE", status)
    sql = text(f"UPDATE financial_transactions SET {assignment} WHERE id = :t")  # noqa: S608
    params = {"t": tx, "other": f.cat_out}  # an OUT category of the same school
    # Move to another category for the category case so that the value really changes.
    other: uuid.UUID = conn.execute(
        text(
            "INSERT INTO categories (organization_id, school_id, key, name, applies_to, report_group) "
            "VALUES (:o, :s, 'services', 'Serviços', 'OUT', 'EXPENSES_REIMBURSEMENTS') RETURNING id"
        ),
        {"o": f.org, "s": f.school},
    ).scalar_one()
    params["other"] = other

    if status in EDITABLE:
        conn.execute(sql, params)
    else:
        with (
            pytest.raises(DBAPIError, match="changes only while|change only while|is final"),
            conn.begin_nested(),
        ):
            conn.execute(sql, params)


@pytest.mark.parametrize("status", EXPENSE_STATES)
@pytest.mark.parametrize("column", ["description", "vendor", "purchase_reason", "payment_method"])
def test_the_texts_of_an_expense_change_only_in_draft_or_correction(
    world: tuple[Connection, Fresh], status: str, column: str
) -> None:
    conn, f = world
    tx = row_in_state(conn, f, "EXPENSE", status)
    value = "'PIX'" if column == "payment_method" else "'Changed text'"
    sql = text(f"UPDATE expenses SET {column} = {value} WHERE transaction_id = :t")  # noqa: S608

    if status in EDITABLE:
        conn.execute(sql, {"t": tx})
    else:
        with pytest.raises(DBAPIError, match="edited only while|is final"), conn.begin_nested():
            conn.execute(sql, {"t": tx})


def test_the_amount_of_the_other_kinds_never_changes_by_edit(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    rows = {
        "pending Pix contribution": add_pix_contribution(conn, f, 3300)[0],
        "pending reimbursement": add_collaborator_expense(conn, f, 3300)[1],
        "requested devolução": add_refund(conn, f, 300),
    }
    for name, tx in rows.items():
        with pytest.raises(DBAPIError, match="amount changes only while"), conn.begin_nested():
            conn.execute(
                text(
                    "UPDATE financial_transactions SET amount_cents = amount_cents + 1 WHERE id = :t"
                ),
                {"t": tx},
            )
        assert name


def test_a_corrected_expense_goes_back_to_review_with_the_new_value(
    world: tuple[Connection, Fresh],
) -> None:
    """DRAFT -> SUBMITTED -> CORRECTION_REQUESTED -> (edit) -> SUBMITTED -> APPROVED, end to end."""
    conn, f = world
    tx = add_expense(conn, f, 1000, status="DRAFT")
    conn.execute(
        text("UPDATE financial_transactions SET amount_cents = 1200 WHERE id = :t"), {"t": tx}
    )  # still a draft
    add_attachment(conn, f, tx)
    set_status(conn, tx, "SUBMITTED")
    conn.execute(
        text(
            "UPDATE expenses SET correction_reason = 'the amount is wrong' WHERE transaction_id = :t"
        ),
        {"t": tx},
    )
    set_status(conn, tx, "CORRECTION_REQUESTED")

    conn.execute(
        text("UPDATE financial_transactions SET amount_cents = 1100 WHERE id = :t"), {"t": tx}
    )  # corrected
    conn.execute(
        text("UPDATE expenses SET description = 'Corrected description' WHERE transaction_id = :t"),
        {"t": tx},
    )
    set_status(conn, tx, "SUBMITTED")
    with pytest.raises(DBAPIError, match="amount changes only while"), conn.begin_nested():
        conn.execute(
            text("UPDATE financial_transactions SET amount_cents = 1 WHERE id = :t"), {"t": tx}
        )  # under review again
    conn.execute(
        text(
            "UPDATE expenses SET approved_by_user_id = :u, approved_amount_cents = 1100 WHERE transaction_id = :t"
        ),
        {"u": f.treasurer, "t": tx},
    )
    set_status(conn, tx, "APPROVED")
    check_consistency(conn)
    assert (
        conn.execute(
            text("SELECT amount_cents FROM financial_transactions WHERE id = :t"), {"t": tx}
        ).scalar_one()
        == 1100
    )


def test_a_correction_request_records_what_to_correct_and_only_while_under_review(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    draft = add_expense(conn, f, 1000, status="DRAFT")
    with (
        pytest.raises(DBAPIError, match="correction request is written while"),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE expenses SET correction_reason = 'too early' WHERE transaction_id = :t"),
            {"t": draft},
        )
    submitted = add_expense(conn, f, 1000)
    set_status(conn, submitted, "CORRECTION_REQUESTED")  # no reason written
    with pytest.raises(DBAPIError, match="records what to correct"), conn.begin_nested():
        check_consistency(conn)


def test_an_expense_needs_an_attachment_the_reason_and_the_payment_method_to_leave_the_draft(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    tx = add_expense(conn, f, 1000, status="DRAFT")
    check_consistency(conn)  # a draft needs nothing
    set_status(conn, tx, "SUBMITTED")
    with pytest.raises(DBAPIError, match="needs at least one attachment"), conn.begin_nested():
        check_consistency(conn)
    add_attachment(conn, f, tx, "PAYMENT_PROOF")
    check_consistency(conn)
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    conn.execute(
        text("UPDATE expenses SET purchase_reason = NULL WHERE transaction_id = :t"), {"t": tx}
    )
    conn.execute(text("RESET session_replication_role"))
    # The change above skipped the triggers (so nothing was queued): touch the row to queue the check.
    conn.execute(
        text("UPDATE financial_transactions SET updated_at = now() WHERE id = :t"), {"t": tx}
    )
    with (
        pytest.raises(DBAPIError, match="reason of the purchase and how it was paid"),
        conn.begin_nested(),
    ):
        check_consistency(conn)


@pytest.mark.parametrize("status", ["REJECTED", "PAID", "CANCELLED"])
def test_no_attachment_is_added_to_a_final_expense(
    world: tuple[Connection, Fresh], status: str
) -> None:
    conn, f = world
    tx = row_in_state(conn, f, "EXPENSE", status)
    with pytest.raises(DBAPIError, match="cannot be added to a final expense"), conn.begin_nested():
        add_attachment(conn, f, tx)


@pytest.mark.parametrize("kind", ["INVOICE", "PAYMENT_PROOF", "OTHER"])
def test_the_three_kinds_of_attachment_are_accepted(
    world: tuple[Connection, Fresh], kind: str
) -> None:
    conn, f = world
    add_attachment(conn, f, add_expense(conn, f, 1000), kind)


# --- REVIEW_REQUIRED: the management decides, with a reason (condition 3 of the round 2) -------------


def _review(conn: Connection, f: Fresh) -> uuid.UUID:
    return add_pix_review(conn, f, 3300, 3350)[0]


def _accept(conn: Connection, tx: uuid.UUID, amount: int) -> None:
    conn.execute(
        text(
            "UPDATE financial_transactions SET status = 'PAID', amount_cents = :a, settled_at = :at WHERE id = :t"
        ),
        {"a": amount, "at": WHEN, "t": tx},
    )


def test_accepting_a_review_needs_a_reason_first(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx = _review(conn, f)
    with pytest.raises(DBAPIError, match="records its reason"), conn.begin_nested():
        _accept(conn, tx, 3350)
    with (
        pytest.raises(DBAPIError, match="ck_contributions_review_decision_reason_length"),
        conn.begin_nested(),
    ):
        conn.execute(
            text(
                "UPDATE contributions SET review_decision_reason = 'ab' WHERE transaction_id = :t"
            ),
            {"t": tx},
        )
    conn.execute(
        text(
            "UPDATE contributions SET review_decision_reason = 'accepted by the management' WHERE transaction_id = :t"
        ),
        {"t": tx},
    )
    _accept(conn, tx, 3350)
    check_consistency(conn)
    assert conn.execute(
        text("SELECT amount_cents, status FROM financial_transactions WHERE id = :t"), {"t": tx}
    ).one() == (3350, "PAID")


def test_accepting_a_review_sets_the_amount_to_the_one_received(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    tx = _review(conn, f)
    conn.execute(
        text(
            "UPDATE contributions SET review_decision_reason = 'accepted by the management' WHERE transaction_id = :t"
        ),
        {"t": tx},
    )
    for wrong in (
        3300,
        3349,
        9999,
    ):  # the expected amount, or any amount that is not the received one
        with (
            pytest.raises(DBAPIError, match="sets the amount to the amount received"),
            conn.begin_nested(),
        ):
            _accept(conn, tx, wrong)


def test_cancelling_a_review_needs_a_reason_and_does_not_touch_the_amount(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    tx = _review(conn, f)
    with pytest.raises(DBAPIError, match="records its reason"), conn.begin_nested():
        set_status(conn, tx, "CANCELLED")
    conn.execute(
        text(
            "UPDATE contributions SET review_decision_reason = 'not our payment' WHERE transaction_id = :t"
        ),
        {"t": tx},
    )
    with pytest.raises(DBAPIError, match="does not change the amount"), conn.begin_nested():
        conn.execute(
            text(
                "UPDATE financial_transactions SET status = 'CANCELLED', amount_cents = 3350 WHERE id = :t"
            ),
            {"t": tx},
        )
    set_status(conn, tx, "CANCELLED")


def test_the_review_reason_is_written_once_and_only_during_the_review(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    paying = add_pix_contribution(conn, f, 3000)[0]
    with (
        pytest.raises(DBAPIError, match="only while the contribution is REVIEW_REQUIRED"),
        conn.begin_nested(),
    ):
        conn.execute(
            text(
                "UPDATE contributions SET review_decision_reason = 'too early' WHERE transaction_id = :t"
            ),
            {"t": paying},
        )
    tx = _review(conn, f)
    conn.execute(
        text(
            "UPDATE contributions SET review_decision_reason = 'first reason' WHERE transaction_id = :t"
        ),
        {"t": tx},
    )
    with pytest.raises(DBAPIError, match="already set"), conn.begin_nested():
        conn.execute(
            text(
                "UPDATE contributions SET review_decision_reason = 'another reason' WHERE transaction_id = :t"
            ),
            {"t": tx},
        )


@pytest.mark.parametrize(
    ("source", "target"),
    [("PENDING_PAYMENT", target) for target in ("PAID", "EXPIRED", "CANCELLED", "REVIEW_REQUIRED")],
)
def test_the_amount_adjustment_is_not_valid_in_any_other_transition(
    world: tuple[Connection, Fresh], source: str, target: str
) -> None:
    conn, f = world
    tx, _ = add_pix_contribution(conn, f, 3300)
    with pytest.raises(DBAPIError, match="amount changes only while"), conn.begin_nested():
        conn.execute(
            text(
                "UPDATE financial_transactions SET status = :to, amount_cents = 3350, settled_at = :at WHERE id = :t"
            ),
            {"to": target, "at": WHEN if target in SETTLED else None, "t": tx},
        )


def test_a_pix_contribution_cannot_skip_the_review_by_changing_its_amount(
    world: tuple[Connection, Fresh],
) -> None:
    """The only way to a different amount is the review: the charge in review must exist."""
    conn, f = world
    tx, charge = add_pix_contribution(conn, f, 3300)
    conn.execute(
        text(
            "UPDATE pix_charges SET status = 'REVIEW_REQUIRED', end_to_end_id = 'E' || repeat('1', 31), "
            "paid_at = now(), received_amount_cents = 3350, divergence_reason = 'received amount differs' WHERE id = :c"
        ),
        {"c": charge},
    )
    set_status(conn, tx, "REVIEW_REQUIRED")
    conn.execute(
        text(
            "UPDATE contributions SET review_decision_reason = 'accepted by the management' WHERE transaction_id = :t"
        ),
        {"t": tx},
    )
    _accept(conn, tx, 3350)
    check_consistency(conn)  # the received amount and the charge in review agree


def test_a_contribution_in_review_needs_a_charge_in_review(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx, _ = add_pix_contribution(conn, f, 3300)
    set_status(conn, tx, "REVIEW_REQUIRED")
    with pytest.raises(DBAPIError, match="needs a charge in review"), conn.begin_nested():
        check_consistency(conn)
