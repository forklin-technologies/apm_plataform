# ruff: noqa: E501  (SQL text)
"""F16: reconciliation with the bank statement (ADR-015, revision 3).

The treasury gives the final balance according to the bank when the month is closed
(monthly_closings.bank_balance_reported_cents). The database computes the difference to the cash
balance of the platform (bank_difference_cents = bank - platform) and the monthly report prints it.
Rules checked here:

  * the difference is computed by the database (a generated column) and is visible in the closing;
  * a difference does NOT stop the closing: it is recorded and shown;
  * the reported balance is written once, with the closing, and never changes; it is not covered by
    the hash of the movements and verify_closing does not look at it;
  * it is audited with the closing.
"""

import uuid

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.financial.support import (
    Fresh,
    add_bank_fee,
    add_cash_contribution,
    add_direct_pix,
    check_consistency,
    utc,
)

PERIOD = "2025-10-01"


def _month(conn: Connection, f: Fresh) -> None:
    """The platform's October: 100,00 in, a 5,90 fee out, so the cash balance is 94,10."""
    add_cash_contribution(conn, f, 10000, utc(2025, 10, 5, 15))
    add_bank_fee(conn, f, 590, settled_at=utc(2025, 10, 8, 15))


def _close(conn: Connection, f: Fresh, bank_balance: int | None = None) -> uuid.UUID:
    closing: uuid.UUID = conn.execute(
        text(
            "INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id, "
            "bank_balance_reported_cents) VALUES (:o, :s, :p, :u, :b) RETURNING id"
        ),
        {"o": f.org, "s": f.school, "p": PERIOD, "u": f.treasurer, "b": bank_balance},
    ).scalar_one()
    return closing


def _figures(conn: Connection, closing: uuid.UUID) -> tuple[int, int | None, int | None]:
    row = conn.execute(
        text(
            "SELECT closing_balance_cents, bank_balance_reported_cents, bank_difference_cents "
            "FROM monthly_closings WHERE id = :c"
        ),
        {"c": closing},
    ).one()
    return (row[0], row[1], row[2])


# --- the difference is calculated and visible -----------------------------------------------------------


def test_the_difference_is_the_bank_balance_minus_the_balance_of_the_platform(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    _month(conn, f)
    # The bank holds 30,00 more than the platform: a direct Pix nobody has registered yet.
    closing = _close(conn, f, bank_balance=12410)

    assert _figures(conn, closing) == (9410, 12410, 3000)


@pytest.mark.parametrize(
    ("bank_balance", "difference"),
    [(9410, 0), (9000, -410), (12410, 3000), (0, -9410), (-5, -9415)],
)
def test_the_difference_has_a_sign_and_zero_means_reconciled(
    world: tuple[Connection, Fresh], bank_balance: int, difference: int
) -> None:
    conn, f = world
    _month(conn, f)
    closing = _close(conn, f, bank_balance=bank_balance)
    assert _figures(conn, closing) == (9410, bank_balance, difference)


def test_without_a_reported_balance_there_is_no_difference_and_the_month_still_closes(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    _month(conn, f)
    closing = _close(conn, f)

    assert _figures(conn, closing) == (9410, None, None)
    assert conn.execute(text("SELECT verify_closing(:c)"), {"c": closing}).scalar_one() is True


def test_a_difference_does_not_stop_the_closing(world: tuple[Connection, Fresh]) -> None:
    """A huge disagreement with the bank is recorded and shown, never a reason to refuse the month."""
    conn, f = world
    _month(conn, f)
    closing = _close(conn, f, bank_balance=999_999_999)

    assert _figures(conn, closing)[2] == 999_999_999 - 9410
    check_consistency(conn)


def test_the_story_of_a_missing_direct_pix(world: tuple[Connection, Fresh]) -> None:
    """The platform closes October with the bank 30,00 above it. The Pix is registered afterwards
    (a late adjustment, booked in the open month): the closing keeps the difference it was born with."""
    conn, f = world
    _month(conn, f)
    closing = _close(conn, f, bank_balance=12410)

    add_direct_pix(conn, f, 3000, settled_at=utc(2025, 10, 20, 15))
    check_consistency(conn)

    assert _figures(conn, closing) == (9410, 12410, 3000)
    assert conn.execute(text("SELECT verify_closing(:c)"), {"c": closing}).scalar_one() is True
    late = conn.execute(
        text(
            "SELECT late_adjustment FROM financial_transactions WHERE school_id = :s AND kind = 'CONTRIBUTION' "
            "AND amount_cents = 3000"
        ),
        {"s": f.school},
    ).scalar_one()
    assert late is True


# --- who writes what -------------------------------------------------------------------------------------------


def test_the_difference_cannot_be_written_by_anyone(world: tuple[Connection, Fresh]) -> None:
    """It is a generated column: not even the admin of the database can give it a value."""
    conn, f = world
    _month(conn, f)
    with pytest.raises(DBAPIError, match="generated column"), conn.begin_nested():
        conn.execute(
            text(
                "INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id, "
                "bank_balance_reported_cents, bank_difference_cents) VALUES (:o, :s, :p, :u, 1, 1)"
            ),
            {"o": f.org, "s": f.school, "p": PERIOD, "u": f.treasurer},
        )
    closing = _close(conn, f, bank_balance=9410)
    with pytest.raises(DBAPIError, match="generated column"), conn.begin_nested():
        conn.execute(
            text("UPDATE monthly_closings SET bank_difference_cents = 1 WHERE id = :c"),
            {"c": closing},
        )


def test_the_reported_balance_is_written_with_the_closing_and_never_changes(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    _month(conn, f)
    with_balance = _close(conn, f, bank_balance=9410)
    with (
        pytest.raises(DBAPIError, match="bank_balance_reported_cents can never change"),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE monthly_closings SET bank_balance_reported_cents = 1 WHERE id = :c"),
            {"c": with_balance},
        )

    # not even to give a balance to a closing made without one (reopen it and close again instead)
    conn.execute(
        text(
            "UPDATE monthly_closings SET reopened_by_user_id = :u, reopen_reason = 'the bank statement arrived' "
            "WHERE id = :c"
        ),
        {"u": f.treasurer, "c": with_balance},
    )
    without = _close(conn, f)
    with (
        pytest.raises(DBAPIError, match="bank_balance_reported_cents can never change"),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE monthly_closings SET bank_balance_reported_cents = 9410 WHERE id = :c"),
            {"c": without},
        )
    # ... and the second closing is the one that can carry the right balance
    assert _figures(conn, _close_again(conn, f, without)) == (9410, 9410, 0)


def _close_again(conn: Connection, f: Fresh, previous: uuid.UUID) -> uuid.UUID:
    conn.execute(
        text(
            "UPDATE monthly_closings SET reopened_by_user_id = :u, reopen_reason = 'balance to be reported' "
            "WHERE id = :c"
        ),
        {"u": f.treasurer, "c": previous},
    )
    return _close(conn, f, bank_balance=9410)


@pytest.mark.parametrize("value", [1_000_000_000_001, -1_000_000_000_001])
def test_the_reported_balance_is_inside_a_sane_range(
    world: tuple[Connection, Fresh], value: int
) -> None:
    conn, f = world
    _month(conn, f)
    with (
        pytest.raises(IntegrityError, match="ck_monthly_closings_bank_balance_range"),
        conn.begin_nested(),
    ):
        _close(conn, f, bank_balance=value)


# --- outside the seal ---------------------------------------------------------------------------------------------


def test_the_reconciliation_is_outside_the_hash_and_outside_verify_closing(
    world: tuple[Connection, Fresh],
) -> None:
    """The hash seals the movements. Two closings of the same month, one with a bank balance and one
    without, carry the very same hash; and verify_closing does not recompute the reported balance."""
    conn, f = world
    _month(conn, f)
    plain = _close(conn, f)
    conn.execute(
        text(
            "UPDATE monthly_closings SET reopened_by_user_id = :u, reopen_reason = 'balance to be reported' "
            "WHERE id = :c"
        ),
        {"u": f.treasurer, "c": plain},
    )
    reconciled = _close(conn, f, bank_balance=12410)

    hashes = [
        conn.execute(
            text("SELECT entries_hash FROM monthly_closings WHERE id = :c"), {"c": c}
        ).scalar_one()
        for c in (plain, reconciled)
    ]
    assert hashes[0] == hashes[1]
    assert conn.execute(text("SELECT verify_closing(:c)"), {"c": reconciled}).scalar_one() is True


# --- the audit log ---------------------------------------------------------------------------------------------------


def test_the_closing_is_audited_with_the_reported_balance_and_the_difference(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    _month(conn, f)
    closing = _close(conn, f, bank_balance=12410)

    after = conn.execute(
        text(
            "SELECT after_data FROM audit_logs WHERE entity_type = 'monthly_closings' AND entity_id = :c"
        ),
        {"c": closing},
    ).scalar_one()
    assert after["bank_balance_reported_cents"] == 12410
    assert after["bank_difference_cents"] == 3000
    assert after["closing_balance_cents"] == 9410
