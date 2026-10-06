"""TASK-008 / ADR-019: the expiry of Pix charges and contributions, by the job and by the page.

The job scans every school of the (shared) test database, so each test looks only at its own rows.
"""

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.contributions import expiry
from app.contributions.expiry import CONTRIBUTION_TTL_HOURS, EXPIRY_GRACE_SECONDS
from app.db.request_context import bind_request_context
from app.db.session import build_session_factory
from app.db.tenant import TenantContext, bind_tenant
from app.jobs import expire as job
from tests.authsupport import Api
from tests.financial.support import add_pix_contribution
from tests.public.conftest import PublicSchool
from tests.public.support import charge_url, contribute

_FIND = text(
    "SELECT organization_id, school_id FROM "
    "public.find_schools_with_stale_contributions(CAST(:after AS uuid), :page)"
)


@pytest.fixture
def factory(app_engine: Engine) -> sessionmaker[Session]:
    return build_session_factory(app_engine)


def _backdate(conn: Connection, tx: uuid.UUID, *, hours: float) -> None:
    """created_at never changes for the application (ft_05_immutable): only a test, as the
    admin and inside its own transaction, moves it back (triggers off for this statement)."""
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    conn.execute(
        text(
            "UPDATE financial_transactions SET created_at = now() - make_interval(secs => :s) "
            "WHERE id = :tx"
        ),
        {"s": hours * 3600, "tx": tx},
    )
    conn.execute(text("SET LOCAL session_replication_role = origin"))


def _charge_expires(conn: Connection, charge: uuid.UUID, *, seconds_ago: float) -> None:
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    conn.execute(
        text(
            "UPDATE pix_charges SET created_at = now() - interval '2 hours', "
            "expires_at = now() - make_interval(secs => :s) WHERE id = :id"
        ),
        {"s": seconds_ago, "id": charge},
    )
    conn.execute(text("SET LOCAL session_replication_role = origin"))


def _contribution(
    admin_engine: Engine, school: PublicSchool, *, age_hours: float, charge: str, amount: int = 2500
) -> tuple[uuid.UUID, uuid.UUID]:
    """A Pix contribution `age_hours` old; the charge is `live`, `lapsed` (past its grace) or
    `expired` (already final)."""
    with admin_engine.begin() as conn:
        tx, charge_id = add_pix_contribution(conn, school.fresh, amount)
        if charge == "lapsed":
            _charge_expires(conn, charge_id, seconds_ago=EXPIRY_GRACE_SECONDS + 600)
        elif charge == "expired":
            _charge_expires(conn, charge_id, seconds_ago=EXPIRY_GRACE_SECONDS + 600)
            conn.execute(
                text("UPDATE pix_charges SET status = 'EXPIRED' WHERE id = :c"), {"c": charge_id}
            )
        _backdate(conn, tx, hours=age_hours)
    return tx, charge_id


def tx_status(admin_engine: Engine, tx: uuid.UUID) -> str:
    with admin_engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT status FROM financial_transactions WHERE id = :i"), {"i": tx}
            ).scalar_one()
        )


def charge_status(admin_engine: Engine, charge: uuid.UUID) -> str:
    with admin_engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT status FROM pix_charges WHERE id = :i"), {"i": charge}
            ).scalar_one()
        )


# --- the rules ------------------------------------------------------------------------------------


def test_a_charge_expires_only_after_its_grace(
    admin_engine: Engine, school: PublicSchool, factory: sessionmaker[Session]
) -> None:
    _tx_a, lapsed = _contribution(admin_engine, school, age_hours=1, charge="lapsed")
    _tx_b, inside = _contribution(admin_engine, school, age_hours=1, charge="live")
    with admin_engine.begin() as conn:
        _charge_expires(
            conn, inside, seconds_ago=EXPIRY_GRACE_SECONDS - 60
        )  # past expiry, in grace

    job.run_once(factory)

    assert charge_status(admin_engine, lapsed) == "EXPIRED"
    assert charge_status(admin_engine, inside) == "PENDING"


def test_a_contribution_expires_at_24_hours_and_only_without_a_live_charge(
    admin_engine: Engine, school: PublicSchool, factory: sessionmaker[Session]
) -> None:
    old_expired, _ = _contribution(
        admin_engine, school, age_hours=CONTRIBUTION_TTL_HOURS + 1, charge="expired"
    )
    old_lapsed, lapsed_charge = _contribution(
        admin_engine, school, age_hours=CONTRIBUTION_TTL_HOURS + 1, charge="lapsed"
    )
    old_live, live_charge = _contribution(
        admin_engine, school, age_hours=CONTRIBUTION_TTL_HOURS + 1, charge="live"
    )
    young, _ = _contribution(
        admin_engine, school, age_hours=CONTRIBUTION_TTL_HOURS - 1, charge="expired"
    )

    job.run_once(factory)

    assert tx_status(admin_engine, old_expired) == "EXPIRED"
    # the charge lapsed in the same pass, so the contribution follows it
    assert charge_status(admin_engine, lapsed_charge) == "EXPIRED"
    assert tx_status(admin_engine, old_lapsed) == "EXPIRED"
    # a charge still waiting for the family keeps the contribution alive
    assert charge_status(admin_engine, live_charge) == "PENDING"
    assert tx_status(admin_engine, old_live) == "PENDING_PAYMENT"
    assert tx_status(admin_engine, young) == "PENDING_PAYMENT"


def test_a_paid_contribution_is_never_touched(
    admin_engine: Engine, school: PublicSchool, factory: sessionmaker[Session]
) -> None:
    with admin_engine.begin() as conn:
        paid, _ = add_pix_contribution(conn, school.fresh, 1000, paid_at=datetime.now(UTC))
        _backdate(conn, paid, hours=100)

    job.run_once(factory)

    assert tx_status(admin_engine, paid) == "PAID"


def test_the_job_acts_as_system_and_the_database_audits_it(
    admin_engine: Engine, school: PublicSchool, factory: sessionmaker[Session]
) -> None:
    tx, _ = _contribution(
        admin_engine, school, age_hours=CONTRIBUTION_TTL_HOURS + 2, charge="expired"
    )

    job.run_once(factory)

    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT actor_type, actor_user_id FROM audit_logs WHERE entity_id = :t "
                "AND entity_type = 'financial_transactions' ORDER BY occurred_at DESC LIMIT 1"
            ),
            {"t": tx},
        ).one()
    assert rows[0] == "SYSTEM" and rows[1] is None


# --- the function that says where to look ---------------------------------------------------------


def _schools_with_work(
    factory: sessionmaker[Session], after: uuid.UUID | None = None, page: int = 500
) -> list[Any]:
    with factory() as db:
        return list(
            db.execute(_FIND, {"after": None if after is None else str(after), "page": page}).all()
        )


def test_the_function_returns_only_ids_and_only_schools_with_work(
    admin_engine: Engine,
    school: PublicSchool,
    other_school: PublicSchool,
    factory: sessionmaker[Session],
) -> None:
    _contribution(admin_engine, school, age_hours=CONTRIBUTION_TTL_HOURS + 1, charge="expired")
    _contribution(admin_engine, other_school, age_hours=1, charge="live")  # nothing due

    found = _schools_with_work(factory)

    assert all(len(r) == 2 for r in found)  # (organization_id, school_id) and nothing else
    ids = {r[1] for r in found}
    assert school.fresh.school in ids
    assert other_school.fresh.school not in ids
    org = next(r[0] for r in found if r[1] == school.fresh.school)
    assert org == school.fresh.org


def test_the_function_pages_by_school_id_and_clamps_the_page(
    admin_engine: Engine,
    school: PublicSchool,
    other_school: PublicSchool,
    factory: sessionmaker[Session],
) -> None:
    for s in (school, other_school):
        _contribution(admin_engine, s, age_hours=CONTRIBUTION_TTL_HOURS + 1, charge="expired")

    everything = [r[1] for r in _schools_with_work(factory)]
    assert everything == sorted(everything)  # ordered by school id
    first = _schools_with_work(factory, page=1)
    assert len(first) == 1 and first[0][1] == everything[0]
    second = _schools_with_work(factory, after=first[0][1], page=1)
    assert [r[1] for r in second] == everything[1:2]
    assert len(_schools_with_work(factory, page=0)) == 1  # a page below 1 is 1
    assert len(_schools_with_work(factory, page=10**9)) == len(everything)  # above 500 is 500


def test_the_sql_and_the_python_rules_agree_at_the_edges(
    admin_engine: Engine, school: PublicSchool, factory: sessionmaker[Session]
) -> None:
    inside_grace, charge_in = _contribution(admin_engine, school, age_hours=1, charge="live")
    with admin_engine.begin() as conn:
        _charge_expires(conn, charge_in, seconds_ago=EXPIRY_GRACE_SECONDS - 20)
    assert school.fresh.school not in {r[1] for r in _schools_with_work(factory)}

    with admin_engine.begin() as conn:
        _charge_expires(conn, charge_in, seconds_ago=EXPIRY_GRACE_SECONDS + 20)
    assert school.fresh.school in {r[1] for r in _schools_with_work(factory)}
    job.run_once(factory)
    assert charge_status(admin_engine, charge_in) == "EXPIRED"
    assert school.fresh.school not in {r[1] for r in _schools_with_work(factory)}  # age is 1 h

    young, _ = _contribution(
        admin_engine, school, age_hours=CONTRIBUTION_TTL_HOURS - 0.05, charge="expired"
    )
    assert school.fresh.school not in {r[1] for r in _schools_with_work(factory)}
    with admin_engine.begin() as conn:
        _backdate(conn, young, hours=CONTRIBUTION_TTL_HOURS + 0.05)
    assert school.fresh.school in {r[1] for r in _schools_with_work(factory)}
    job.run_once(factory)
    assert tx_status(admin_engine, young) == "EXPIRED"
    assert tx_status(admin_engine, inside_grace) == "PENDING_PAYMENT"


# --- robustness -----------------------------------------------------------------------------------


def test_one_failing_school_does_not_stop_the_others(
    admin_engine: Engine,
    school: PublicSchool,
    other_school: PublicSchool,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _tx_a, charge_a = _contribution(admin_engine, school, age_hours=1, charge="lapsed")
    _tx_b, charge_b = _contribution(admin_engine, other_school, age_hours=1, charge="lapsed")
    original = job._expire_school  # noqa: SLF001

    def flaky(f: sessionmaker[Session], org: uuid.UUID, school_id: uuid.UUID) -> tuple[int, int]:
        if school_id == school.fresh.school:
            raise RuntimeError("boom")
        return original(f, org, school_id)

    monkeypatch.setattr(job, "_expire_school", flaky)

    report = job.run_once(factory)

    assert report.failed >= 1
    assert charge_status(admin_engine, charge_a) == "PENDING"  # the failed school was left alone
    assert charge_status(admin_engine, charge_b) == "EXPIRED"


def test_main_runs_once_and_validates_the_interval(school: PublicSchool) -> None:
    assert job.main([]) == 0
    with pytest.raises(SystemExit):
        job.main(["--interval", "0"])


@contextmanager
def _bound(factory: sessionmaker[Session], school: PublicSchool) -> Iterator[Session]:
    with factory() as db:
        bind_tenant(db, TenantContext(school.fresh.org, school.fresh.school))
        bind_request_context(db, actor_type="SYSTEM", request_id="job-expire-test0001")
        yield db


def test_a_renewal_that_commits_while_the_job_waits_keeps_the_contribution(
    admin_engine: Engine, school: PublicSchool, factory: sessionmaker[Session]
) -> None:
    """The family renews: it holds the lock of the contribution while it inserts the new PENDING
    charge. The job, waiting for that lock, must read again and find the new charge."""
    tx, _old = _contribution(
        admin_engine, school, age_hours=CONTRIBUTION_TTL_HOURS + 1, charge="expired"
    )
    outcome: dict[str, Any] = {}

    def run_job() -> None:
        with _bound(factory, school) as db:
            outcome["expired"] = expiry.expire_contribution(db, tx)
            db.commit()

    renewal = admin_engine.connect()
    transaction = renewal.begin()
    try:
        renewal.execute(
            text("SELECT id FROM financial_transactions WHERE id = :t FOR UPDATE"), {"t": tx}
        )
        renewal.execute(
            text(
                "INSERT INTO pix_charges (organization_id, school_id, transaction_id, "
                "payment_account_id, provider, txid, amount_cents, expires_at) "
                "VALUES (:org, :school, :tx, :account, 'SANDBOX', :txid, 2500, "
                "now() + interval '30 minutes')"
            ),
            {
                "org": school.fresh.org,
                "school": school.fresh.school,
                "tx": tx,
                "account": school.fresh.account,
                "txid": uuid.uuid4().hex,
            },
        )
        thread = threading.Thread(target=run_job)
        thread.start()
        thread.join(timeout=1.0)
        assert thread.is_alive(), "the job should be waiting for the lock of the contribution"
        transaction.commit()
        thread.join(timeout=15)
        assert not thread.is_alive()
    finally:
        if transaction.is_active:
            transaction.rollback()
        renewal.close()

    assert outcome["expired"] is False
    assert tx_status(admin_engine, tx) == "PENDING_PAYMENT"


# --- the page expires what it reads ---------------------------------------------------------------


def test_a_stale_contribution_reads_as_expired_and_cannot_be_renewed(
    api: Api, admin_engine: Engine, school: PublicSchool
) -> None:
    created = contribute(api, school.slug, 3000)
    assert created.status_code == 201
    token = created.json()["token"]
    with admin_engine.begin() as conn:
        tx: uuid.UUID = conn.execute(
            text("SELECT id FROM financial_transactions WHERE school_id = :s"),
            {"s": school.fresh.school},
        ).scalar_one()
        conn.execute(
            text("UPDATE pix_charges SET status = 'EXPIRED' WHERE transaction_id = :t"), {"t": tx}
        )
        _backdate(conn, tx, hours=CONTRIBUTION_TTL_HOURS + 1)

    state = api.get(charge_url(school.slug, token))
    assert state.status_code == 200
    assert state.json()["status"] == "EXPIRED"
    assert tx_status(admin_engine, tx) == "EXPIRED"

    renew = api.post(charge_url(school.slug, token, "charges"), {}, csrf=False)
    assert renew.status_code == 409
    assert renew.json()["code"] == "contribution_closed"


def test_a_fresh_contribution_polls_without_changing(
    api: Api, admin_engine: Engine, school: PublicSchool
) -> None:
    created = contribute(api, school.slug, 3000)
    token = created.json()["token"]
    state = api.get(charge_url(school.slug, token))
    assert state.json()["status"] == "PENDING_PAYMENT"
    assert state.json()["charge"]["status"] == "PENDING"
