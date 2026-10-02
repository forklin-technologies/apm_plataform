# ruff: noqa: E501, S311  (SQL text; the seeded random generator only makes test data)
"""F4: the statement is correct.

Part 1 are scenarios worked out BY HAND, with the expected numbers written as literals (cash basis:
a contribution that is PAID comes in; an expense paid by the APM that is PAID goes out; a
reimbursement that is PAID goes out; a refund goes out for a contribution and comes in for an
expense; an expense paid by a collaborator moves the cash only through its reimbursement; pending
items are listed apart and never enter the balance).

Part 2 is a property over many generated ledgers, checked against a model written in Python:
closing = opening + in - out; the opening of a period is the closing of the one before; the periods
add up to the total; the running balance ends at the closing.
"""

import hashlib
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction
from tests.financial.conftest import Ledger
from tests.financial.support import (
    Fresh,
    add_cash_contribution,
    add_collaborator_expense,
    add_expense,
    add_pix_contribution,
    add_refund,
    check_consistency,
    entries,
    make_school,
    summary,
    utc,
)

D = date
MAR = (D(2025, 3, 1), D(2025, 3, 31))
APR = (D(2025, 4, 1), D(2025, 4, 30))


def signed(rows: list[Any]) -> list[int]:
    return [row.signed_amount_cents for row in rows]


def running(rows: list[Any]) -> list[int]:
    return [row.running_balance_cents for row in rows]


# --- 1. scenarios worked out by hand ---------------------------------------------------------------


def test_a_contribution_comes_in(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 10000, utc(2025, 3, 10, 15))

    assert summary(conn, f, *MAR) == (0, 10000, 0, 10000, 1)
    rows = entries(conn, f, *MAR)
    assert signed(rows) == [10000]
    assert [r.opening_balance_cents for r in rows] == [0]
    assert running(rows) == [10000]
    assert [r.kind for r in rows] == ["CONTRIBUTION"]


def test_an_expense_paid_by_the_apm_goes_out_and_a_quiet_month_carries_the_balance(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    add_expense(conn, f, 2500, status="PAID", settled_at=utc(2025, 3, 20, 15))

    assert summary(conn, f, *MAR) == (0, 10000, 2500, 7500, 2)
    rows = entries(conn, f, *MAR)
    assert signed(rows) == [10000, -2500]
    assert running(rows) == [10000, 7500]
    # A month with no movement: the opening balance is carried and nothing is listed.
    assert summary(conn, f, *APR) == (7500, 0, 0, 7500, 0)
    assert entries(conn, f, *APR) == []
    # A period before everything.
    assert summary(conn, f, D(2025, 2, 1), D(2025, 2, 28)) == (0, 0, 0, 0, 0)


def test_an_expense_paid_by_a_collaborator_moves_the_cash_only_through_its_reimbursement(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense, reimbursement = add_collaborator_expense(conn, f, 4000)

    # Before the payment: an obligation, outside the balance.
    pending = conn.execute(
        text("SELECT transaction_id, kind, section, amount_cents FROM statement_pending(:s)"),
        {"s": f.school},
    ).all()
    assert [(r.kind, r.section, r.amount_cents) for r in pending] == [
        ("REIMBURSEMENT", "PAYABLE", 4000)
    ]
    assert summary(conn, f, *MAR) == (0, 0, 0, 0, 0)

    # Paid on March 25: ONE outflow of 4000, not two.
    conn.execute(
        text("UPDATE reimbursements SET payment_reference = 'bank-1' WHERE transaction_id = :t"),
        {"t": reimbursement},
    )
    conn.execute(
        text(
            "UPDATE financial_transactions SET status = 'PAID', settled_at = :at WHERE id = ANY(:ids)"
        ),
        {"at": utc(2025, 3, 25, 15), "ids": [reimbursement, expense]},
    )
    check_consistency(conn)

    assert summary(conn, f, *MAR) == (0, 0, 4000, -4000, 1)
    rows = entries(conn, f, *MAR)
    assert [(r.kind, r.signed_amount_cents) for r in rows] == [("REIMBURSEMENT", -4000)]
    assert (
        conn.execute(
            text("SELECT count(*) FROM statement_pending(:s)"), {"s": f.school}
        ).scalar_one()
        == 0
    )


def test_a_refund_of_a_contribution_goes_out(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    contribution = add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    add_refund(
        conn, f, contribution, "CONTRIBUTION", 3000, status="PAID",
        settled_at=utc(2025, 3, 18, 15), reference="r-1",
    )  # fmt: skip

    assert summary(conn, f, *MAR) == (0, 10000, 3000, 7000, 2)
    assert signed(entries(conn, f, *MAR)) == [10000, -3000]


def test_a_refund_of_an_expense_comes_in(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    expense = add_expense(conn, f, 2500, status="PAID", settled_at=utc(2025, 3, 20, 15))
    add_refund(
        conn,
        f,
        expense,
        "EXPENSE",
        1000,
        status="PAID",
        settled_at=utc(2025, 4, 2, 15),
        reference="r-2",
    )

    assert summary(conn, f, *MAR) == (0, 10000, 2500, 7500, 2)
    assert summary(conn, f, *APR) == (7500, 1000, 0, 8500, 1)
    rows = entries(conn, f, *APR)
    assert signed(rows) == [1000]
    assert [(r.opening_balance_cents, r.running_balance_cents) for r in rows] == [(7500, 8500)]
    assert [r.direction for r in rows] == ["IN"]


def test_the_running_balance_follows_the_order_of_booking_then_the_reference_code(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    same = utc(2025, 3, 10, 15)
    add_cash_contribution(conn, f, 100, same)  # reference 1
    add_expense(conn, f, 30, status="PAID", settled_at=same)  # reference 2, same instant
    add_cash_contribution(conn, f, 5, utc(2025, 3, 9, 15))  # reference 3, earlier

    rows = entries(conn, f, *MAR)

    assert [r.reference_code for r in rows] == [3, 1, 2]
    assert signed(rows) == [5, 100, -30]
    assert running(rows) == [5, 105, 75]


def test_pending_items_are_listed_apart_and_never_enter_the_balance(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    add_pix_contribution(conn, f, 3000)  # a receber
    add_expense(conn, f, 1500)  # submitted: aguardando aprovação
    add_expense(conn, f, 1200, status="APPROVED")  # a pagar (the APM pays)
    add_collaborator_expense(conn, f, 4000)  # only its reimbursement is a payable
    contribution_paid = add_cash_contribution(conn, f, 900, utc(2025, 3, 6, 15))
    add_refund(conn, f, contribution_paid, "CONTRIBUTION", 300)  # a pagar
    paid_expense = add_expense(conn, f, 800, status="PAID", settled_at=utc(2025, 3, 7, 15))
    add_refund(conn, f, paid_expense, "EXPENSE", 200)  # a receber
    cancelled = add_pix_contribution(conn, f, 700)[0]
    conn.execute(
        text("UPDATE financial_transactions SET status = 'CANCELLED' WHERE id = :t"),
        {"t": cancelled},
    )
    rejected = add_expense(conn, f, 600)
    conn.execute(
        text(
            "UPDATE expenses SET approved_by_user_id = :u, decision_reason = 'not allowed' WHERE transaction_id = :t"
        ),
        {"u": f.treasurer, "t": rejected},
    )
    conn.execute(
        text("UPDATE financial_transactions SET status = 'REJECTED' WHERE id = :t"), {"t": rejected}
    )

    pending = conn.execute(
        text("SELECT kind, section, amount_cents FROM statement_pending(:s)"), {"s": f.school}
    ).all()

    assert sorted((r.kind, r.section, r.amount_cents) for r in pending) == sorted(
        [
            ("CONTRIBUTION", "RECEIVABLE", 3000),
            ("EXPENSE", "AWAITING_APPROVAL", 1500),
            ("EXPENSE", "PAYABLE", 1200),
            ("REIMBURSEMENT", "PAYABLE", 4000),
            ("REFUND", "PAYABLE", 300),
            ("REFUND", "RECEIVABLE", 200),
        ]
    )
    # The balance only knows what was settled: 10000 + 900 - 800 = 10100.
    assert summary(conn, f, *MAR) == (0, 10900, 800, 10100, 3)


def test_a_settlement_belongs_to_the_day_in_the_time_zone_of_the_school(
    world: tuple[Connection, Fresh],
) -> None:
    """São Paulo is UTC-3: 23:30 on March 31 is already April 1 in UTC, but still March here."""
    conn, f = world
    add_cash_contribution(conn, f, 1, utc(2025, 3, 1, 3, 0, 0))  # 00:00:00 Mar 1 local: March
    add_cash_contribution(conn, f, 10, utc(2025, 3, 1, 2, 59, 59))  # 23:59:59 Feb 28: February
    add_cash_contribution(conn, f, 100, utc(2025, 4, 1, 2, 30))  # 23:30 Mar 31 local: March
    add_cash_contribution(conn, f, 1000, utc(2025, 4, 1, 3, 0, 0))  # 00:00 Apr 1 local: April
    add_cash_contribution(conn, f, 10000, utc(2025, 4, 1, 3, 30))  # 00:30 Apr 1 local: April

    assert summary(conn, f, D(2025, 2, 1), D(2025, 2, 28)) == (0, 10, 0, 10, 1)
    assert summary(conn, f, *MAR) == (10, 101, 0, 111, 2)
    assert summary(conn, f, *APR) == (111, 11000, 0, 11111, 2)
    assert [r.local_date for r in entries(conn, f, *MAR)] == [D(2025, 3, 1), D(2025, 3, 31)]


def test_another_time_zone_moves_the_boundary(world: tuple[Connection, Fresh]) -> None:
    """Tokyo is UTC+9: 16:00 UTC on March 31 is 01:00 on April 1 there."""
    conn, _ = world
    tokyo = make_school(conn, timezone="Asia/Tokyo")
    add_cash_contribution(conn, tokyo, 100, utc(2025, 3, 31, 14, 59))  # 23:59 Mar 31 JST
    add_cash_contribution(conn, tokyo, 1000, utc(2025, 3, 31, 16, 0))  # 01:00 Apr 1 JST

    assert summary(conn, tokyo, *MAR) == (0, 100, 0, 100, 1)
    assert summary(conn, tokyo, *APR) == (100, 1000, 0, 1100, 1)


def test_the_statement_of_a_school_never_includes_another_school(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    other = make_school(conn)
    add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    add_cash_contribution(conn, other, 777, utc(2025, 3, 5, 15))

    assert summary(conn, f, *MAR) == (0, 10000, 0, 10000, 1)
    assert summary(conn, other, *MAR) == (0, 777, 0, 777, 1)


# --- the late adjustment and the monthly closing -----------------------------------------------------


def _close(conn: Connection, f: Fresh, month: date) -> uuid.UUID:
    closing: uuid.UUID = conn.execute(
        text(
            "INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id) "
            "VALUES (:o, :s, :m, :u) RETURNING id"
        ),
        {"o": f.org, "s": f.school, "m": month, "u": f.treasurer},
    ).scalar_one()
    return closing


@dataclass(frozen=True)
class Cash:
    reference: int
    id: uuid.UUID
    kind: str
    signed: int
    settled_at: datetime
    late: bool


def _python_hash(conn: Connection, f: Fresh, start: date, end: date, tz: str) -> str:
    """The canonical text of a closing, rebuilt in Python from the raw rows (NOT through the SQL
    function): header school|start|end|opening, then one line per cash entry of the period."""
    zone = ZoneInfo(tz)
    rows = conn.execute(
        text(
            "SELECT f.reference_code, f.id, f.kind, f.direction, f.amount_cents, f.settled_at, "
            "f.late_adjustment, e.paid_by FROM financial_transactions f "
            "LEFT JOIN expenses e ON e.transaction_id = f.id "
            "WHERE f.school_id = :s AND f.settled_at IS NOT NULL"
        ),
        {"s": f.school},
    ).all()
    cash: list[Cash] = []
    for ref, id_, kind, direction, amount, settled_at, late, paid_by in rows:
        if kind == "EXPENSE" and paid_by != "APM":
            continue
        cash.append(
            Cash(ref, id_, kind, amount if direction == "IN" else -amount, settled_at, late)
        )
    first = datetime.combine(start, datetime.min.time(), tzinfo=zone)
    after = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=zone)
    opening = sum(c.signed for c in cash if c.settled_at < first)
    inside = sorted(
        (c for c in cash if first <= c.settled_at < after),
        key=lambda c: (c.settled_at, c.reference),
    )
    text_ = f"{f.school}|{start.isoformat()}|{end.isoformat()}|{opening}"
    for c in inside:
        stamp = c.settled_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        text_ += (
            f"\n{c.reference}|{c.id}|{c.kind}|{c.signed}|{stamp}|{'true' if c.late else 'false'}"
        )
    return hashlib.sha256(text_.encode()).hexdigest()


def test_closing_a_month_stores_the_figures_and_a_hash_that_python_reproduces(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    add_expense(conn, f, 2500, status="PAID", settled_at=utc(2025, 3, 20, 15))
    add_cash_contribution(conn, f, 4000, utc(2025, 4, 10, 15))

    march = _close(conn, f, D(2025, 3, 1))
    april = _close(conn, f, D(2025, 4, 1))

    row = conn.execute(
        text(
            "SELECT period_start, period_end, timezone, opening_balance_cents, total_in_cents, "
            "total_out_cents, closing_balance_cents, entries_count, entries_hash "
            "FROM monthly_closings WHERE id = :c"
        ),
        {"c": march},
    ).one()
    assert tuple(row)[:8] == (
        D(2025, 3, 1),
        D(2025, 3, 31),
        "America/Sao_Paulo",
        0,
        10000,
        2500,
        7500,
        2,
    )
    assert row.entries_hash == _python_hash(
        conn, f, D(2025, 3, 1), D(2025, 3, 31), "America/Sao_Paulo"
    )
    second = conn.execute(
        text(
            "SELECT opening_balance_cents, closing_balance_cents, entries_hash FROM monthly_closings WHERE id = :c"
        ),
        {"c": april},
    ).one()
    assert (second[0], second[1]) == (7500, 11500)  # the opening is the closing before
    assert second[2] == _python_hash(conn, f, D(2025, 4, 1), D(2025, 4, 30), "America/Sao_Paulo")
    for closing in (march, april):
        assert conn.execute(text("SELECT verify_closing(:c)"), {"c": closing}).scalar_one() is True


def test_the_figures_of_a_closing_are_computed_by_the_database_never_taken_from_the_caller(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    closing: uuid.UUID = conn.execute(
        text(
            "INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id, "
            "opening_balance_cents, total_in_cents, total_out_cents, closing_balance_cents, "
            "entries_count, entries_hash, timezone, period_end, closed_at) "
            "VALUES (:o, :s, '2025-03-01', :u, 1, 2, 3, 0, 9, :h, 'UTC', '2025-03-31', '2020-01-01') RETURNING id"
        ),
        {"o": f.org, "s": f.school, "u": f.treasurer, "h": "0" * 64},
    ).scalar_one()

    row = conn.execute(
        text(
            "SELECT opening_balance_cents, total_in_cents, total_out_cents, closing_balance_cents, "
            "entries_count, timezone, closed_at > '2024-01-01', entries_hash <> :h "
            "FROM monthly_closings WHERE id = :c"
        ),
        {"c": closing, "h": "0" * 64},
    ).one()
    assert tuple(row) == (0, 10000, 0, 10000, 1, "America/Sao_Paulo", True, True)


def test_a_month_must_have_ended_and_closings_are_sequential(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    today: date = conn.execute(
        text("SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date")
    ).scalar_one()
    this_month = today.replace(day=1)
    with pytest.raises(DBAPIError, match="has not ended yet"), conn.begin_nested():
        _close(conn, f, this_month)
    with pytest.raises(DBAPIError, match="has not ended yet"), conn.begin_nested():
        _close(conn, f, (this_month + timedelta(days=40)).replace(day=1))

    _close(conn, f, D(2025, 1, 1))
    with (
        pytest.raises(
            DBAPIError, match="closings are sequential: the next period starts on 2025-02-01"
        ),
        conn.begin_nested(),
    ):
        _close(conn, f, D(2025, 3, 1))
    with pytest.raises(DBAPIError, match="closings are sequential"), conn.begin_nested():
        _close(conn, f, D(2025, 1, 1))  # the same month twice
    _close(conn, f, D(2025, 2, 1))


def test_the_first_closing_may_start_anywhere_and_carries_what_came_before(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 700, utc(2025, 1, 10, 15))
    closing = _close(conn, f, D(2025, 3, 1))
    assert conn.execute(
        text(
            "SELECT opening_balance_cents, closing_balance_cents FROM monthly_closings WHERE id = :c"
        ),
        {"c": closing},
    ).one() == (700, 700)


def test_a_period_that_is_not_a_calendar_month_is_refused(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    with pytest.raises(DBAPIError, match="ck_monthly_closings_calendar_month"), conn.begin_nested():
        _close(conn, f, D(2025, 3, 15))


def test_a_settlement_dated_in_a_closed_month_is_booked_now_and_flagged(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    march = _close(conn, f, D(2025, 3, 1))
    before: str = conn.execute(
        text("SELECT entries_hash FROM monthly_closings WHERE id = :c"), {"c": march}
    ).scalar_one()

    late = add_cash_contribution(conn, f, 4000, utc(2025, 3, 28, 15))  # dated in the closed month
    in_april = add_cash_contribution(conn, f, 500, utc(2025, 4, 10, 15))  # an open month: as given
    before_first = add_cash_contribution(
        conn, f, 60, utc(2025, 2, 10, 15)
    )  # before the first closing

    rows = {
        r.id: r
        for r in conn.execute(
            text(
                "SELECT id, settled_at, occurred_at, late_adjustment FROM financial_transactions WHERE school_id = :s"
            ),
            {"s": f.school},
        )
    }
    now: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    for tx in (late, before_first):
        assert rows[tx].late_adjustment is True
        assert abs((rows[tx].settled_at - now).total_seconds()) < 60  # booked now
    assert rows[late].occurred_at == utc(2025, 3, 28, 15)  # the real date is kept
    assert rows[in_april].late_adjustment is False and rows[in_april].settled_at == utc(
        2025, 4, 10, 15
    )
    # The closed month did not move.
    assert summary(conn, f, *MAR) == (0, 10000, 0, 10000, 1)
    assert conn.execute(text("SELECT verify_closing(:c)"), {"c": march}).scalar_one() is True
    assert (
        conn.execute(
            text("SELECT entries_hash FROM monthly_closings WHERE id = :c"), {"c": march}
        ).scalar_one()
        == before
    )
    # And it shows in the month where it was booked, flagged.
    today: date = conn.execute(
        text("SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date")
    ).scalar_one()
    start = today.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    flagged = [r for r in entries(conn, f, start, end) if r.late_adjustment]
    assert sorted(signed(flagged)) == [60, 4000]


def test_tampering_with_a_closed_entry_is_detected_by_verify_closing(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    tx = add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    march = _close(conn, f, D(2025, 3, 1))
    assert conn.execute(text("SELECT verify_closing(:c)"), {"c": march}).scalar_one() is True

    conn.execute(text("SET LOCAL session_replication_role = replica"))  # only a superuser can
    conn.execute(
        text("UPDATE financial_transactions SET amount_cents = 10001 WHERE id = :t"), {"t": tx}
    )
    conn.execute(text("RESET session_replication_role"))

    assert conn.execute(text("SELECT verify_closing(:c)"), {"c": march}).scalar_one() is False


def test_only_the_latest_closing_can_be_reopened_and_it_can_be_closed_again(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    jan = _close(conn, f, D(2025, 1, 1))
    feb = _close(conn, f, D(2025, 2, 1))
    reopen = text(
        "UPDATE monthly_closings SET reopened_by_user_id = :u, reopen_reason = :r WHERE id = :c"
    )

    with pytest.raises(DBAPIError, match="only the latest active closing"), conn.begin_nested():
        conn.execute(reopen, {"u": f.admin, "r": "wrong figure in January", "c": jan})
    with (
        pytest.raises(DBAPIError, match="ck_monthly_closings_reopen_reason_length"),
        conn.begin_nested(),
    ):
        conn.execute(reopen, {"u": f.admin, "r": "short", "c": feb})
    conn.execute(reopen, {"u": f.admin, "r": "a receipt was missing", "c": feb})
    assert conn.execute(
        text("SELECT reopened_at IS NOT NULL FROM monthly_closings WHERE id = :c"), {"c": feb}
    ).scalar_one()

    # February is open again: a settlement dated in it is booked as given, and it closes again.
    add_cash_contribution(conn, f, 55, utc(2025, 2, 10, 15))
    feb_again = _close(conn, f, D(2025, 2, 1))
    assert feb_again != feb
    assert conn.execute(
        text(
            "SELECT count(*), count(*) FILTER (WHERE reopened_at IS NULL) FROM monthly_closings WHERE school_id = :s AND period_start = '2025-02-01'"
        ),
        {"s": f.school},
    ).one() == (2, 1)  # the history is kept
    assert (
        conn.execute(
            text("SELECT total_in_cents FROM monthly_closings WHERE id = :c"), {"c": feb_again}
        ).scalar_one()
        == 55
    )
    # Now January is the one before the latest again: the latest is February.
    with pytest.raises(DBAPIError, match="only the latest active closing"), conn.begin_nested():
        conn.execute(reopen, {"u": f.admin, "r": "wrong figure in January", "c": jan})


def test_a_reopening_cannot_be_undone_or_rewritten_and_a_report_is_set_once(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    march = _close(conn, f, D(2025, 3, 1))
    conn.execute(
        text("UPDATE monthly_closings SET report_ref = 'r1.pdf' WHERE id = :c"), {"c": march}
    )
    with pytest.raises(DBAPIError, match="already set"), conn.begin_nested():
        conn.execute(
            text("UPDATE monthly_closings SET report_ref = 'r2.pdf' WHERE id = :c"), {"c": march}
        )
    conn.execute(
        text(
            "UPDATE monthly_closings SET reopened_by_user_id = :u, reopen_reason = 'a good reason here' WHERE id = :c"
        ),
        {"u": f.admin, "c": march},
    )
    with pytest.raises(DBAPIError, match="already set"), conn.begin_nested():
        conn.execute(
            text(
                "UPDATE monthly_closings SET reopen_reason = 'another reason entirely' WHERE id = :c"
            ),
            {"c": march},
        )


def test_a_month_is_not_closed_inside_a_repeatable_read_transaction(admin_engine: Engine) -> None:
    """The snapshot must see every committed settlement: it needs READ COMMITTED."""
    with admin_engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        trans = conn.begin()
        try:
            f = make_school(conn)
            with pytest.raises(DBAPIError, match="READ COMMITTED"):
                _close(conn, f, D(2025, 3, 1))
        finally:
            trans.rollback()


def test_the_statement_functions_respect_row_level_security(
    app_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    """They are SECURITY INVOKER: the application role sees its own school and nothing of another."""
    own = TenantContext(tenants.org_a, tenants.school_a1)
    query = text("SELECT count(*) FROM statement_entries(:s, '2025-01-01', '2035-01-01')")
    with transaction(app_engine, context=own) as conn:
        assert conn.execute(query, {"s": tenants.school_a1}).scalar_one() >= 1
        assert conn.execute(query, {"s": tenants.school_b1}).scalar_one() == 0
        assert (
            conn.execute(
                text("SELECT count(*) FROM statement_summary(:s, '2025-01-01', '2025-01-31')"),
                {"s": tenants.school_b1},
            ).scalar_one()
            == 0
        )  # no settings visible: no row at all
    with transaction(app_engine, context=TenantContext(tenants.org_b)) as conn:
        assert conn.execute(query, {"s": tenants.school_a1}).scalar_one() == 0
    with transaction(app_engine) as conn:  # no context at all
        assert conn.execute(query, {"s": tenants.school_a1}).scalar_one() == 0


# --- 2. the property --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    id: uuid.UUID
    signed: int
    at: datetime  # UTC


def _generate(conn: Connection, f: Fresh, rng: random.Random, zone: ZoneInfo) -> list[Move]:
    """A random ledger over January-June 2025 (mixed kinds, some pending ones that must not count)."""
    moves: list[Move] = []

    def when() -> datetime:
        # Random instant; every so often exactly on a month boundary of the school's time zone.
        if rng.random() < 0.15:
            month = rng.randint(2, 6)
            local_midnight = datetime(2025, month, 1, tzinfo=zone)
            return (local_midnight + timedelta(seconds=rng.choice([-1, 0, 1]))).astimezone(UTC)
        start = datetime(2025, 1, 1, tzinfo=UTC)
        return start + timedelta(seconds=rng.randint(0, 180 * 86400))

    for _ in range(rng.randint(8, 22)):
        amount = rng.randint(1, 50000)
        at = when()
        kind = rng.choice(["cash", "pix", "apm", "collab", "refund_c", "refund_e", "pending"])
        if kind == "cash":
            moves.append(Move(add_cash_contribution(conn, f, amount, at), amount, at))
        elif kind == "pix":
            tx, _ = add_pix_contribution(conn, f, amount, paid_at=at)
            moves.append(Move(tx, amount, at))
        elif kind == "apm":
            moves.append(
                Move(add_expense(conn, f, amount, status="PAID", settled_at=at), -amount, at)
            )
        elif kind == "collab":
            _, reimbursement = add_collaborator_expense(conn, f, amount, reimbursed_at=at)
            moves.append(Move(reimbursement, -amount, at))  # the expense itself never counts
        elif kind == "refund_c":
            parent = add_cash_contribution(conn, f, amount, at)
            moves.append(Move(parent, amount, at))
            part = rng.randint(1, amount)
            rat = when()
            refund = add_refund(
                conn,
                f,
                parent,
                "CONTRIBUTION",
                part,
                status="PAID",
                settled_at=rat,
                reference="ref",
            )
            moves.append(Move(refund, -part, rat))
        elif kind == "refund_e":
            parent = add_expense(conn, f, amount, status="PAID", settled_at=at)
            moves.append(Move(parent, -amount, at))
            part = rng.randint(1, amount)
            rat = when()
            refund = add_refund(
                conn, f, parent, "EXPENSE", part, status="PAID", settled_at=rat, reference="ref"
            )
            moves.append(Move(refund, part, rat))
        else:  # pending: must never count
            which = rng.randint(0, 2)
            if which == 0:
                add_pix_contribution(conn, f, amount)
            elif which == 1:
                add_expense(conn, f, amount)
            else:
                add_expense(conn, f, amount, status="APPROVED")
    return moves


def _month_ranges() -> list[tuple[date, date]]:
    return [(D(2025, m, 1), (D(2025, m + 1, 1) - timedelta(days=1))) for m in range(1, 7)]


@pytest.mark.parametrize("seed", range(40))
def test_the_balance_property_holds_for_a_random_ledger(
    world: tuple[Connection, Fresh], seed: int
) -> None:
    rng = random.Random(seed)
    conn, _ = world
    zone_name = rng.choice(
        ["America/Sao_Paulo", "America/Sao_Paulo", "Asia/Tokyo", "America/Manaus"]
    )
    f = make_school(conn, timezone=zone_name)
    zone = ZoneInfo(zone_name)
    moves = _generate(conn, f, rng, zone)
    check_consistency(conn)

    def local(m: Move) -> date:
        return m.at.astimezone(zone).date()

    total = sum(m.signed for m in moves)
    first_day, last_day = D(2025, 1, 1), D(2025, 12, 31)
    # In a zone behind UTC the first hours of January 1 (UTC) are still December 31 (local).
    before_range = sum(m.signed for m in moves if local(m) < first_day)
    in_range = [m for m in moves if first_day <= local(m) <= last_day]
    full = summary(conn, f, first_day, last_day)
    assert full[0] == before_range and full[3] == total and full[4] == len(in_range)
    assert full[3] == full[0] + full[1] - full[2]  # closing = opening + in - out

    carried = before_range
    total_in = total_out = count = 0
    for start_day, end_day in _month_ranges():
        inside = [m for m in moves if start_day <= local(m) <= end_day]
        expected_in = sum(m.signed for m in inside if m.signed > 0)
        expected_out = -sum(m.signed for m in inside if m.signed < 0)
        opening, got_in, got_out, closing, got_count = summary(conn, f, start_day, end_day)
        assert (opening, got_in, got_out, got_count) == (
            carried,
            expected_in,
            expected_out,
            len(inside),
        )
        assert closing == opening + got_in - got_out  # the identity
        carried = closing  # the opening of the next period is this closing
        total_in, total_out, count = total_in + got_in, total_out + got_out, count + got_count
    assert (total_in, total_out, count) == (full[1], full[2], full[4])  # the periods add up
    assert carried == total

    rows = entries(conn, f, first_day, last_day)
    assert sorted((r.transaction_id, r.signed_amount_cents) for r in rows) == sorted(
        (m.id, m.signed) for m in in_range
    )
    assert [r.running_balance_cents for r in rows][-1:] == ([total] if rows else [])
    cumulative = before_range
    for row in rows:
        cumulative += row.signed_amount_cents
        assert row.running_balance_cents == cumulative
        assert row.opening_balance_cents == before_range

    # A random sub-period: the entries and the summary agree, and the opening is what came before.
    a = D(2025, 1, 1) + timedelta(days=rng.randint(0, 150))
    b = a + timedelta(days=rng.randint(0, 30))
    sub = summary(conn, f, a, b)
    inside = [m for m in moves if a <= local(m) <= b]
    before = sum(m.signed for m in moves if local(m) < a)
    assert sub == (before, sum(m.signed for m in inside if m.signed > 0),
                   -sum(m.signed for m in inside if m.signed < 0), before + sum(m.signed for m in inside), len(inside))  # fmt: skip
    sub_rows = entries(conn, f, a, b)
    assert len(sub_rows) == sub[4]
    assert [r.running_balance_cents for r in sub_rows][-1:] == ([sub[3]] if sub_rows else [])
