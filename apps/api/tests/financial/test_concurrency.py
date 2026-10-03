# ruff: noqa: E501, S311  (SQL text; a seeded random delay only varies the interleaving)
"""F5: concurrency, on real connections and real threads (each transaction is committed).

  * The same settlement in two simultaneous transactions settles ONCE (compare-and-swap).
  * reference_code never repeats and never skips, per school, however many writers; a rollback gives
    the number back.
  * A settlement and a month closing never cross: the entry is either in the closing's snapshot or
    booked as a late adjustment, and verify_closing holds either way.
  * A duplicated webhook leaves one row; two refunds cannot both fit; one reimbursement per expense.

Each test makes a school of its own, committed, and removes it (and everything under it) at the end.
"""

import random
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from app.core.config import AdminSettings
from tests.financial.support import (
    Fresh,
    add_cash_contribution,
    add_expense,
    add_pix_contribution,
    add_refund,
    add_reimbursement,
    drop_school,
    end_to_end_id,
    make_school,
    utc,
)


@pytest.fixture(scope="module")
def pool(admin_settings: AdminSettings) -> Iterator[Engine]:
    """Enough connections for every thread to hold one at the same time."""
    engine = create_engine(
        admin_settings.database_admin_url.get_secret_value(), pool_size=40, max_overflow=0
    )
    yield engine
    engine.dispose()


@pytest.fixture
def school(pool: Engine) -> Iterator[Fresh]:
    with pool.begin() as conn:
        fresh = make_school(conn)
    yield fresh
    with pool.begin() as conn:
        drop_school(conn, fresh)


@contextmanager
def committed(pool: Engine, *schools: Fresh) -> Iterator[None]:
    """Make extra schools for a test and remove them afterwards."""
    try:
        yield
    finally:
        with pool.begin() as conn:
            for fresh in schools:
                drop_school(conn, fresh)


def run_together(
    pool: Engine, jobs: list[Callable[[Connection], Any]], *, hold: float = 0.0
) -> list[Any]:
    """Run every job in its own thread and its own transaction, all released at the same moment.
    Each job returns a value (or raises: the exception is the result); the transaction commits when
    the job returns, after `hold` seconds, so that the others pile up behind its locks."""
    barrier = threading.Barrier(len(jobs))
    results: list[Any] = [None] * len(jobs)

    def worker(index: int, job: Callable[[Connection], Any]) -> None:
        with pool.connect() as conn:
            transaction = conn.begin()
            try:
                barrier.wait(timeout=20)
                results[index] = job(conn)
                time.sleep(hold)
                transaction.commit()
            except Exception as error:  # noqa: BLE001  (the error IS the result under test)
                transaction.rollback()
                results[index] = error

    threads = [threading.Thread(target=worker, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "a transaction is stuck: possible deadlock"
    return results


def codes(pool: Engine, fresh: Fresh) -> list[int]:
    with pool.connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT reference_code FROM financial_transactions WHERE school_id = :s ORDER BY 1"
                ),
                {"s": fresh.school},
            )
        ]


# --- one settlement, once ---------------------------------------------------------------------------


@pytest.mark.parametrize("round_", range(6))
def test_the_same_settlement_in_two_transactions_settles_once(
    pool: Engine, school: Fresh, round_: int
) -> None:
    with pool.begin() as conn:
        expense = add_expense(conn, school, 2500, status="APPROVED")

    def settle(conn: Connection) -> int:
        # The compare-and-swap of ADR-015: only the row still APPROVED is taken.
        return conn.execute(
            text(
                "UPDATE financial_transactions SET status = 'PAID', settled_at = now() "
                "WHERE id = :id AND status = 'APPROVED'"
            ),
            {"id": expense},
        ).rowcount

    results = run_together(pool, [settle, settle], hold=0.2)

    assert sorted(results) == [0, 1]  # exactly one transaction changed the row
    with pool.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, settled_at IS NOT NULL FROM financial_transactions WHERE id = :id"
            ),
            {"id": expense},
        ).one()
        paid_records: int = conn.execute(
            text(
                "SELECT count(*) FROM audit_logs WHERE entity_id = :id "
                "AND action = 'financial_transactions.update' AND after_data ->> 'status' = 'PAID'"
            ),
            {"id": expense},
        ).scalar_one()
    assert tuple(row) == ("PAID", True)
    assert paid_records == 1  # and it was audited once


def test_a_pix_charge_confirmed_twice_is_confirmed_once(pool: Engine, school: Fresh) -> None:
    with pool.begin() as conn:
        _, charge = add_pix_contribution(conn, school, 3000)
    e2e = end_to_end_id()

    def confirm(conn: Connection) -> int:
        return conn.execute(
            text(
                "UPDATE pix_charges SET status = 'PAID', end_to_end_id = :e, paid_at = now(), "
                "received_amount_cents = amount_cents WHERE id = :c AND status = 'PENDING'"
            ),
            {"e": e2e, "c": charge},
        ).rowcount

    results = run_together(pool, [confirm, confirm, confirm], hold=0.2)

    assert sorted(results) == [0, 0, 1]


# --- reference_code -----------------------------------------------------------------------------------


@pytest.mark.parametrize("round_", range(3))
def test_the_reference_code_never_repeats_or_skips_under_concurrency(
    pool: Engine, school: Fresh, round_: int
) -> None:
    writers = 20

    def write(conn: Connection) -> uuid.UUID:
        return add_cash_contribution(conn, school, 100, utc(2025, 3, 1, 15))

    results = run_together(pool, [write] * writers)

    assert all(isinstance(r, uuid.UUID) for r in results), results
    assert codes(pool, school) == list(range(1, writers + 1))  # no gap, no duplicate


def test_two_schools_number_independently_and_at_the_same_time(pool: Engine, school: Fresh) -> None:
    with pool.begin() as conn:
        other = make_school(conn)
    with committed(pool, other):

        def write_to(target: Fresh) -> Callable[[Connection], uuid.UUID]:
            return lambda conn: add_cash_contribution(conn, target, 100, utc(2025, 3, 1, 15))

        results = run_together(pool, [write_to(school)] * 8 + [write_to(other)] * 8)

        assert all(isinstance(r, uuid.UUID) for r in results), results
        assert codes(pool, school) == list(range(1, 9))
        assert codes(pool, other) == list(range(1, 9))


def test_a_rollback_gives_the_number_back(pool: Engine, school: Fresh) -> None:
    with pool.begin() as conn:
        add_cash_contribution(conn, school, 100, utc(2025, 3, 1, 15))  # reference 1
    started = threading.Event()
    release = threading.Event()
    outcome: dict[str, Any] = {}

    def doomed() -> None:
        with pool.connect() as conn:
            transaction = conn.begin()
            add_cash_contribution(conn, school, 100, utc(2025, 3, 2, 15))  # takes reference 2
            started.set()
            release.wait(timeout=20)
            transaction.rollback()  # ... and gives it back

    def next_writer() -> None:
        started.wait(timeout=20)
        with pool.connect() as conn:
            transaction = conn.begin()
            add_cash_contribution(conn, school, 100, utc(2025, 3, 3, 15))  # waits for the first one
            transaction.commit()
            outcome["done"] = True

    first, second = threading.Thread(target=doomed), threading.Thread(target=next_writer)
    first.start()
    second.start()
    started.wait(timeout=20)
    time.sleep(0.3)  # the second writer is now blocked behind the lock of the first
    release.set()
    first.join(timeout=30)
    second.join(timeout=30)

    assert outcome == {"done": True}
    assert codes(pool, school) == [1, 2]  # the rolled-back 2 was reused, nothing skipped


# --- settlement x closing ----------------------------------------------------------------------------


@pytest.mark.parametrize("round_", range(12))
def test_a_settlement_and_a_closing_never_cross(pool: Engine, round_: int) -> None:
    """The entry is either in the snapshot (as dated) or booked as a late adjustment; never in the
    closed month but missing from its snapshot. verify_closing is the judge."""
    rng = random.Random(round_)
    with pool.begin() as conn:
        fresh = make_school(conn)
        add_cash_contribution(conn, fresh, 10000, utc(2025, 3, 5, 15))
    with committed(pool, fresh):

        def close(conn: Connection) -> uuid.UUID:
            time.sleep(rng.random() * 0.03)
            result: uuid.UUID = conn.execute(
                text(
                    "INSERT INTO monthly_closings (organization_id, school_id, period_start, "
                    "closed_by_user_id) VALUES (:o, :s, '2025-03-01', :u) RETURNING id"
                ),
                {"o": fresh.org, "s": fresh.school, "u": fresh.treasurer},
            ).scalar_one()
            return result

        def settle(conn: Connection) -> uuid.UUID:
            time.sleep(rng.random() * 0.03)
            return add_cash_contribution(conn, fresh, 4000, utc(2025, 3, 28, 15))

        closing, entry = run_together(pool, [close, settle], hold=0.05)

        assert isinstance(closing, uuid.UUID) and isinstance(entry, uuid.UUID), (closing, entry)
        with pool.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT settled_at, late_adjustment FROM financial_transactions WHERE id = :t"
                ),
                {"t": entry},
            ).one()
            figures = conn.execute(
                text(
                    "SELECT entries_count, total_in_cents, verify_closing(id) FROM monthly_closings WHERE id = :c"
                ),
                {"c": closing},
            ).one()
        assert figures[2] is True  # the snapshot matches the ledger, whatever the interleaving
        if row.late_adjustment:
            assert row.settled_at > utc(2025, 4, 1, 0)  # booked now, outside the closed month
            assert tuple(figures)[:2] == (1, 10000)
        else:
            assert row.settled_at == utc(2025, 3, 28, 15)
            assert tuple(figures)[:2] == (2, 14000)  # in the closed month AND in its snapshot


# --- idempotence and the sums that must not be exceeded ----------------------------------------------


def test_a_duplicated_webhook_leaves_one_row(pool: Engine, school: Fresh) -> None:
    key = uuid.uuid4().hex

    def deliver(conn: Connection) -> int:
        return conn.execute(
            text(
                "INSERT INTO webhook_events (organization_id, school_id, provider, idempotency_key, "
                "raw_payload, signature_valid) VALUES (:o, :s, 'SANDBOX', :k, '{}'::jsonb, true) "
                "ON CONFLICT DO NOTHING"
            ),
            {"o": school.org, "s": school.school, "k": key},
        ).rowcount

    results = run_together(pool, [deliver] * 6, hold=0.1)

    assert sorted(results) == [0, 0, 0, 0, 0, 1]
    with pool.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM webhook_events WHERE school_id = :s"),
                {"s": school.school},
            ).scalar_one()
            == 1
        )


def test_two_returns_that_do_not_both_fit_cannot_both_be_created(
    pool: Engine, school: Fresh
) -> None:
    """Devoluções of the same expense add up to at most what was paid."""
    with pool.begin() as conn:
        expense = add_expense(conn, school, 5000, status="PAID", settled_at=utc(2025, 3, 5, 15))

    def give_back(conn: Connection) -> uuid.UUID:
        return add_refund(conn, school, 3000, parent=expense, parent_kind="EXPENSE")

    results = run_together(pool, [give_back, give_back], hold=0.2)

    created = [r for r in results if isinstance(r, uuid.UUID)]
    failed = [r for r in results if isinstance(r, Exception)]
    assert len(created) == 1 and len(failed) == 1
    assert "returns would exceed the amount paid" in str(failed[0])


def test_one_expense_gets_one_reimbursement_even_when_asked_twice_at_once(
    pool: Engine, school: Fresh
) -> None:
    with pool.begin() as conn:
        expense = add_expense(conn, school, 1000, paid_by="COLLABORATOR", status="APPROVED")

    def reimburse(conn: Connection) -> uuid.UUID:
        return add_reimbursement(conn, school, expense, 1000)

    results = run_together(pool, [reimburse, reimburse], hold=0.2)

    assert sum(isinstance(r, uuid.UUID) for r in results) == 1
    failed = [r for r in results if isinstance(r, Exception)]
    assert len(failed) == 1
    assert "uq_financial_transactions_one_active_reimbursement" in str(failed[0])
