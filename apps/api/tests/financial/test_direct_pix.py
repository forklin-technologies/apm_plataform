# ruff: noqa: E501  (SQL text)
"""F14: a Pix paid straight to the key of the APM (ADR-015, revision 3).

The parents pay the Pix key of the APM outside the charge of the platform, and that credit shows on
the bank statement. It is a contribution with method PIX_DIRECT and the end-to-end id of the Pix as
external_reference. Rules checked here, in the database:

  * it needs no charge: it is born PAID, or born REVIEW_REQUIRED (seen on the bank, not yet
    confirmed) and decided by the management with a reason, the amount unchanged;
  * REVIEW_REQUIRED is a birth state of a direct Pix ONLY, and a direct Pix is never PENDING_PAYMENT;
  * the same Pix is never two contributions: the reference is unique per school, never global, and it
    is distinct from every end-to-end id of the charges of the school, in both directions, even when
    the two writers run at the same moment;
  * the reference is written once and the audit log records only that it is present.
"""

import json
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import AdminSettings
from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction
from tests.financial.support import (
    Fresh,
    add_cash_contribution,
    add_direct_pix,
    add_pix_contribution,
    add_transaction,
    check_consistency,
    drop_school,
    end_to_end_id,
    make_school,
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


def write_reason(
    conn: Connection, tx: uuid.UUID, reason: str = "confirmed on the bank statement"
) -> None:
    conn.execute(
        text("UPDATE contributions SET review_decision_reason = :r WHERE transaction_id = :t"),
        {"r": reason, "t": tx},
    )


# --- born PAID: cash without a charge -----------------------------------------------------------------


def test_a_direct_pix_is_cash_without_any_charge(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx = add_direct_pix(conn, f, 3000, settled_at=WHEN, guardian="Maria Exemplo")
    check_consistency(conn)

    assert status_of(conn, tx) == "PAID"
    assert (
        conn.execute(
            text("SELECT count(*) FROM pix_charges WHERE transaction_id = :t"), {"t": tx}
        ).scalar_one()
        == 0
    )
    row = summary_row(conn, f, *DAY)
    assert (row.contributions_in_cents, row.total_in_cents, row.entries_count) == (3000, 3000, 1)


def test_the_pix_rule_of_the_platform_still_holds_for_method_pix(
    world: tuple[Connection, Fresh],
) -> None:
    """Only a PIX_DIRECT contribution is exempt from 'PAID needs a PAID charge'."""
    conn, f = world
    tx, _ = add_pix_contribution(conn, f, 3000)
    conn.execute(
        text("UPDATE financial_transactions SET status = 'PAID', settled_at = :at WHERE id = :t"),
        {"at": WHEN, "t": tx},
    )
    with pytest.raises(DBAPIError, match="only PAID with a charge"), conn.begin_nested():
        check_consistency(conn)


# --- born REVIEW_REQUIRED: the management decides ----------------------------------------------------------


def test_a_direct_pix_in_review_is_not_cash_until_the_management_accepts_it(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    tx = add_direct_pix(conn, f, 3000, guardian="Maria Exemplo")
    check_consistency(conn)

    assert status_of(conn, tx) == "REVIEW_REQUIRED"
    assert summary_row(conn, f, *DAY).total_in_cents == 0
    section: Any = conn.execute(
        text("SELECT section FROM statement_pending(:s) WHERE transaction_id = :t"),
        {"s": f.school, "t": tx},
    ).scalar_one()
    assert section == "REVIEW"

    write_reason(conn, tx)
    conn.execute(
        text("UPDATE financial_transactions SET status = 'PAID', settled_at = :at WHERE id = :t"),
        {"at": WHEN, "t": tx},
    )
    check_consistency(conn)
    assert summary_row(conn, f, *DAY).contributions_in_cents == 3000


def test_the_management_can_cancel_a_direct_pix_in_review(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx = add_direct_pix(conn, f, 3000)
    write_reason(conn, tx, "not our account")
    conn.execute(
        text("UPDATE financial_transactions SET status = 'CANCELLED' WHERE id = :t"), {"t": tx}
    )
    check_consistency(conn)

    assert status_of(conn, tx) == "CANCELLED"
    assert summary_row(conn, f, *DAY).total_in_cents == 0


@pytest.mark.parametrize("target", ["PAID", "CANCELLED"])
def test_a_decision_records_its_reason_first(world: tuple[Connection, Fresh], target: str) -> None:
    conn, f = world
    tx = add_direct_pix(conn, f, 3000)
    with pytest.raises(DBAPIError, match="records its reason"), conn.begin_nested():
        conn.execute(
            text("UPDATE financial_transactions SET status = :s, settled_at = :at WHERE id = :t"),
            {"s": target, "at": WHEN if target == "PAID" else None, "t": tx},
        )


@pytest.mark.parametrize("target", ["PAID", "CANCELLED"])
def test_a_decision_never_changes_the_amount_of_a_direct_pix(
    world: tuple[Connection, Fresh], target: str
) -> None:
    """There is no charge to say what was received: the amount is the one registered."""
    conn, f = world
    tx = add_direct_pix(conn, f, 3000)
    write_reason(conn, tx)
    with pytest.raises(DBAPIError, match="does not change the amount"), conn.begin_nested():
        conn.execute(
            text(
                "UPDATE financial_transactions SET status = :s, settled_at = :at, amount_cents = 3050 WHERE id = :t"
            ),
            {"s": target, "at": WHEN if target == "PAID" else None, "t": tx},
        )


def test_a_decided_direct_pix_is_final(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx = add_direct_pix(conn, f, 3000, settled_at=WHEN)
    with pytest.raises(DBAPIError, match="is final"), conn.begin_nested():
        conn.execute(
            text("UPDATE financial_transactions SET status = 'CANCELLED' WHERE id = :t"), {"t": tx}
        )


# --- the birth states -------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["CASH", "TRANSFER", "OTHER", "PIX"])
def test_review_required_is_a_birth_state_of_a_direct_pix_only(
    world: tuple[Connection, Fresh], method: str
) -> None:
    conn, f = world
    tx = add_transaction(
        conn, f, kind="CONTRIBUTION", direction="IN", amount=3000, status="REVIEW_REQUIRED",
        created_by=f.treasurer,
    )  # fmt: skip
    with (
        pytest.raises(
            DBAPIError, match="only a direct Pix contribution can be born REVIEW_REQUIRED"
        ),
        conn.begin_nested(),
    ):
        conn.execute(
            text(
                "INSERT INTO contributions (transaction_id, organization_id, school_id, method) "
                "VALUES (:t, :o, :s, :m)"
            ),
            {"t": tx, "o": f.org, "s": f.school, "m": method},
        )


@pytest.mark.parametrize("status", ["PENDING_PAYMENT", "PAID", "REVIEW_REQUIRED"])
def test_a_direct_pix_is_born_paid_or_in_review_only(
    world: tuple[Connection, Fresh], status: str
) -> None:
    conn, f = world
    tx = add_transaction(
        conn, f, kind="CONTRIBUTION", direction="IN", amount=3000, status=status,
        created_by=f.treasurer, settled_at=WHEN if status == "PAID" else None,
    )  # fmt: skip

    def register() -> None:
        conn.execute(
            text(
                "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
                "external_reference) VALUES (:t, :o, :s, 'PIX_DIRECT', :r)"
            ),
            {"t": tx, "o": f.org, "s": f.school, "r": end_to_end_id()},
        )

    if status == "PENDING_PAYMENT":
        with pytest.raises(DBAPIError, match="born PAID or REVIEW_REQUIRED"), conn.begin_nested():
            register()
    else:
        register()
        check_consistency(conn)


@pytest.mark.parametrize("status", ["PAID", "REVIEW_REQUIRED"])
def test_a_direct_pix_always_records_who_registered_it(
    world: tuple[Connection, Fresh], status: str
) -> None:
    conn, f = world
    tx = add_transaction(
        conn, f, kind="CONTRIBUTION", direction="IN", amount=3000, status=status,
        settled_at=WHEN if status == "PAID" else None,
    )  # fmt: skip
    conn.execute(
        text(
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
            "external_reference) VALUES (:t, :o, :s, 'PIX_DIRECT', :r)"
        ),
        {"t": tx, "o": f.org, "s": f.school, "r": end_to_end_id()},
    )
    with pytest.raises(DBAPIError, match="records who registered it"), conn.begin_nested():
        check_consistency(conn)


def test_a_manual_contribution_is_still_born_paid_with_an_author(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_cash_contribution(conn, f, 3000, WHEN)
    check_consistency(conn)


# --- the reference ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "reference", "constraint"),
    [
        ("PIX_DIRECT", None, "ck_contributions_external_reference_iff_pix_direct"),
        ("CASH", "E" + "a" * 31, "ck_contributions_external_reference_iff_pix_direct"),
        ("PIX_DIRECT", "not-an-end-to-end-id", "ck_contributions_external_reference_format"),
        ("PIX_DIRECT", "e" + "a" * 31, "ck_contributions_external_reference_format"),
        ("PIX_DIRECT", "E" + "a" * 30, "ck_contributions_external_reference_format"),
        ("PIX_DIRECT", "E" + "a" * 32, "ck_contributions_external_reference_format"),
    ],
)
def test_the_reference_exists_for_a_direct_pix_and_only_for_it(
    world: tuple[Connection, Fresh], method: str, reference: str | None, constraint: str
) -> None:
    conn, f = world
    tx = add_transaction(
        conn, f, kind="CONTRIBUTION", direction="IN", amount=3000, status="PAID",
        created_by=f.treasurer, settled_at=WHEN,
    )  # fmt: skip
    with pytest.raises(IntegrityError, match=constraint), conn.begin_nested():
        conn.execute(
            text(
                "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
                "external_reference) VALUES (:t, :o, :s, :m, :r)"
            ),
            {"t": tx, "o": f.org, "s": f.school, "m": method, "r": reference},
        )


def test_the_same_pix_is_never_two_contributions_of_a_school(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    reference = end_to_end_id()
    add_direct_pix(conn, f, 3000, settled_at=WHEN, reference=reference)
    with (
        pytest.raises(IntegrityError, match="uq_contributions_school_id_external_reference"),
        conn.begin_nested(),
    ):
        add_direct_pix(conn, f, 3000, settled_at=WHEN, reference=reference)


def test_a_cancelled_direct_pix_keeps_its_reference_taken(world: tuple[Connection, Fresh]) -> None:
    """Cancelled means 'seen and refused': registering the same Pix again would be a duplicate."""
    conn, f = world
    reference = end_to_end_id()
    tx = add_direct_pix(conn, f, 3000, reference=reference)
    write_reason(conn, tx, "not our account")
    conn.execute(
        text("UPDATE financial_transactions SET status = 'CANCELLED' WHERE id = :t"), {"t": tx}
    )
    with (
        pytest.raises(IntegrityError, match="uq_contributions_school_id_external_reference"),
        conn.begin_nested(),
    ):
        add_direct_pix(conn, f, 3000, settled_at=WHEN, reference=reference)


def test_the_reference_is_unique_per_school_never_global(world: tuple[Connection, Fresh]) -> None:
    """Two APMs have two bank accounts: the same id in two schools is two different Pix, and a
    global UNIQUE would let one school learn that another holds a given id (the M1 class)."""
    conn, f = world
    other = make_school(conn)
    reference = end_to_end_id()
    add_direct_pix(conn, f, 3000, settled_at=WHEN, reference=reference)
    add_direct_pix(conn, other, 3000, settled_at=WHEN, reference=reference)
    check_consistency(conn)


def test_the_reference_is_written_once(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx = add_direct_pix(conn, f, 3000, settled_at=WHEN)
    with (
        pytest.raises(DBAPIError, match="external_reference can never change"),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE contributions SET external_reference = :r WHERE transaction_id = :t"),
            {"r": end_to_end_id(), "t": tx},
        )


def test_a_direct_pix_never_gets_a_charge(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx = add_direct_pix(conn, f, 3000, settled_at=WHEN)
    with (
        pytest.raises(DBAPIError, match="needs a PENDING_PAYMENT Pix contribution"),
        conn.begin_nested(),
    ):
        conn.execute(
            text(
                "INSERT INTO pix_charges (organization_id, school_id, transaction_id, payment_account_id, "
                "provider, txid, amount_cents, expires_at) "
                "VALUES (:o, :s, :t, :a, 'SANDBOX', :txid, 3000, now() + interval '30 minutes')"
            ),
            {"o": f.org, "s": f.school, "t": tx, "a": f.account, "txid": uuid.uuid4().hex},
        )


# --- distinct from the charges of the platform ----------------------------------------------------------------


def confirm_charge(conn: Connection, charge: uuid.UUID, reference: str, amount: int = 3000) -> None:
    conn.execute(
        text(
            "UPDATE pix_charges SET status = 'PAID', end_to_end_id = :e, paid_at = :at, "
            "received_amount_cents = :a WHERE id = :c"
        ),
        {"e": reference, "at": WHEN, "a": amount, "c": charge},
    )


def test_a_direct_pix_cannot_reuse_the_end_to_end_id_of_a_charge(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    reference = end_to_end_id()
    _, charge = add_pix_contribution(conn, f, 3000)
    confirm_charge(conn, charge, reference)

    with (
        pytest.raises(DBAPIError, match="already the end-to-end id of a charge") as caught,
        conn.begin_nested(),
    ):
        add_direct_pix(conn, f, 3000, settled_at=WHEN, reference=reference)
    assert getattr(caught.value.orig, "sqlstate", None) == "23505"  # unique_violation


def test_a_charge_cannot_be_confirmed_with_the_id_of_a_direct_pix(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    reference = end_to_end_id()
    add_direct_pix(conn, f, 3000, settled_at=WHEN, reference=reference)
    _, charge = add_pix_contribution(conn, f, 3000)

    with (
        pytest.raises(DBAPIError, match="already registered as a direct Pix") as caught,
        conn.begin_nested(),
    ):
        confirm_charge(conn, charge, reference)
    assert getattr(caught.value.orig, "sqlstate", None) == "23505"


def test_the_same_id_in_another_school_is_not_a_clash(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    other = make_school(conn)
    reference = end_to_end_id()
    contribution, charge = add_pix_contribution(conn, other, 3000)
    confirm_charge(conn, charge, reference)
    set_status(conn, contribution, "PAID", WHEN)  # the webhook settles both together
    add_direct_pix(conn, f, 3000, settled_at=WHEN, reference=reference)  # school f, not `other`
    check_consistency(conn)


# --- the audit log --------------------------------------------------------------------------------------------------


def test_the_audit_log_says_the_reference_is_present_and_never_shows_it(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    reference = end_to_end_id()
    tx = add_direct_pix(
        conn, f, 3000, settled_at=WHEN, reference=reference, guardian="Maria Exemplo"
    )

    after: Any = conn.execute(
        text(
            "SELECT after_data FROM audit_logs WHERE entity_type = 'contributions' AND entity_id = :t"
        ),
        {"t": tx},
    ).scalar_one()
    assert after["method"] == "PIX_DIRECT"
    assert after["external_reference_present"] is True
    assert reference not in json.dumps(after)
    assert "Maria Exemplo" not in json.dumps(after)


# --- two writers at the same moment -------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pool(admin_settings: AdminSettings) -> Iterator[Engine]:
    engine = create_engine(
        admin_settings.database_admin_url.get_secret_value(), pool_size=8, max_overflow=0
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


@pytest.mark.parametrize("round_", range(4))
def test_a_direct_pix_and_a_charge_with_the_same_id_never_both_commit(
    pool: Engine, school: Fresh, round_: int
) -> None:
    """The two inserters of a school take the same advisory lock, so neither misses the row of the
    other: exactly one commits, the other fails with a unique violation."""
    with pool.begin() as conn:
        contribution, charge = add_pix_contribution(conn, school, 3000)
    reference = end_to_end_id()
    barrier = threading.Barrier(2)
    results: list[object] = [None, None]

    def register_direct(conn: Connection) -> None:
        add_direct_pix(conn, school, 3000, settled_at=WHEN, reference=reference)

    def confirm(conn: Connection) -> None:
        confirm_charge(conn, charge, reference)
        set_status(conn, contribution, "PAID", WHEN)  # what the webhook does, in one transaction

    def worker(index: int, job: object, hold: float) -> None:
        import time

        with pool.connect() as conn:
            transaction = conn.begin()
            try:
                barrier.wait(timeout=20)
                job(conn)  # type: ignore[operator]
                time.sleep(hold)
                transaction.commit()
                results[index] = "committed"
            except Exception as error:  # noqa: BLE001  (the error IS the result under test)
                transaction.rollback()
                results[index] = error

    threads = [
        threading.Thread(target=worker, args=(0, register_direct, 0.3)),
        threading.Thread(target=worker, args=(1, confirm, 0.3)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "a transaction is stuck: possible deadlock"

    winners = [r for r in results if r == "committed"]
    losers = [r for r in results if isinstance(r, DBAPIError)]
    assert len(winners) == 1, results
    assert len(losers) == 1, results
    assert getattr(losers[0].orig, "sqlstate", None) == "23505"


# --- the application role, under row level security --------------------------------------------------------------------


def _register_as_app(conn: Connection, tenants: Tenants, reference: str, status: str) -> uuid.UUID:
    """What the service will do for a school (a staff member of A1 registers a direct Pix): a
    category of its own, the ledger row, then the detail row with the end-to-end id."""
    category: Any = conn.execute(
        text(
            "INSERT INTO categories (organization_id, school_id, key, name, applies_to, report_group) "
            "VALUES (:o, :s, :k, 'Contribuição', 'IN', 'CONTRIBUTIONS') RETURNING id"
        ),
        {"o": tenants.org_a, "s": tenants.school_a1, "k": f"f14_{uuid.uuid4().hex[:8]}"},
    ).scalar_one()
    tx: Any = conn.execute(
        text(
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, amount_cents, "
            "status, category_id, origin_type, created_by_user_id, settled_at) "
            "VALUES (:o, :s, 'CONTRIBUTION', 'IN', 3000, :st, :c, 'GUARDIAN', :u, "
            "CASE WHEN :st = 'PAID' THEN now() END) RETURNING id"
        ),
        {
            "o": tenants.org_a,
            "s": tenants.school_a1,
            "st": status,
            "c": category,
            "u": tenants.user_a1,
        },
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
            "external_reference, guardian_name) VALUES (:t, :o, :s, 'PIX_DIRECT', :r, 'Responsável Exemplo')"
        ),
        {"t": tx, "o": tenants.org_a, "s": tenants.school_a1, "r": reference},
    )
    return uuid.UUID(str(tx))


def test_the_application_role_registers_and_confirms_a_direct_pix_in_its_own_school(
    app_engine: Engine, tenants: Tenants
) -> None:
    """With only the columns it is granted: it registers the Pix in review, writes the reason, and
    confirms it; it cannot touch the end-to-end id afterwards."""
    context = TenantContext(tenants.org_a, tenants.school_a1)
    with transaction(app_engine, context=context) as conn:
        tx = _register_as_app(conn, tenants, end_to_end_id(), "REVIEW_REQUIRED")
        write_reason(conn, tx)
        conn.execute(
            text(
                "UPDATE financial_transactions SET status = 'PAID', settled_at = now() WHERE id = :t"
            ),
            {"t": tx},
        )
        conn.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")  # the consistency check, now

        assert status_of(conn, tx) == "PAID"
        with pytest.raises(DBAPIError, match="permission denied"), conn.begin_nested():
            conn.execute(
                text("UPDATE contributions SET external_reference = :r WHERE transaction_id = :t"),
                {"r": end_to_end_id(), "t": tx},
            )


def test_the_application_role_can_register_a_direct_pix_born_paid(
    app_engine: Engine, tenants: Tenants
) -> None:
    context = TenantContext(tenants.org_a, tenants.school_a1)
    with transaction(app_engine, context=context) as conn:
        tx = _register_as_app(conn, tenants, end_to_end_id(), "PAID")
        conn.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
        assert status_of(conn, tx) == "PAID"


def test_another_school_refuses_a_direct_pix_and_never_says_whether_its_id_exists(
    app_engine: Engine, tenants: Tenants, pool: Engine, school: Fresh
) -> None:
    """A context of A1 writes into a school of another organization: the policy refuses it, with
    the very same error whether or not that school already holds a direct Pix with this id (the
    unique index and the trigger never get the chance to answer); and the same id in the own
    school is no clash at all, since the reference is unique per school."""
    reference = end_to_end_id()
    with pool.begin() as conn:
        other = add_direct_pix(conn, school, 3000, settled_at=WHEN, reference=reference)
    context = TenantContext(tenants.org_a, tenants.school_a1)

    def attempt(external_reference: str) -> str:
        with transaction(app_engine, context=context) as conn:
            try:
                conn.execute(
                    text(
                        "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
                        "external_reference) VALUES (:t, :o, :s, 'PIX_DIRECT', :r)"
                    ),
                    {"t": other, "o": school.org, "s": school.school, "r": external_reference},
                )
            except DBAPIError as error:
                return str(error.orig)
        return ""

    known, unknown = attempt(reference), attempt(end_to_end_id())
    assert "row-level security" in known
    assert known == unknown

    with transaction(app_engine, context=context) as conn:
        _register_as_app(conn, tenants, reference, "PAID")  # the same id, in the own school
