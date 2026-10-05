# ruff: noqa: E501  (SQL text)
"""F12: the consolidated statement of a network.

org_statement_summary(organization, from, to) has one row per school. It is SECURITY INVOKER, so row
level security decides who sees what: an organization-wide context sees every school of the
organization, a school context only its own school, and another organization nothing at all.
"""

from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction
from tests.financial.conftest import Ledger

PERIOD = ("2025-01-01", "2035-12-31")
QUERY = text("SELECT * FROM org_statement_summary(:org, :from, :to) ORDER BY school_name")
FIGURES = (
    "timezone", "opening_balance_cents", "contributions_in_cents", "other_in_cents",
    "refunds_in_cents", "total_in_cents", "expenses_out_cents", "reimbursements_out_cents",
    "total_out_cents", "closing_balance_cents", "pending_reimbursements_cents",
    "balance_after_pending_cents", "entries_count",
)  # fmt: skip


def _rows(engine: Engine, context: TenantContext | None, org: Any) -> list[Any]:
    with transaction(engine, context=context) as conn:
        return list(conn.execute(QUERY, {"org": org, "from": PERIOD[0], "to": PERIOD[1]}))


def _school_summary(admin_engine: Engine, school: Any) -> tuple[Any, ...]:
    with admin_engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM statement_summary(:s, :from, :to)"),
            {"s": school, "from": PERIOD[0], "to": PERIOD[1]},
        ).one()
    return tuple(getattr(row, name) for name in FIGURES)


def test_the_network_context_sees_every_school_with_the_figures_of_each(
    app_engine: Engine, admin_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    rows = _rows(app_engine, TenantContext(tenants.org_a), tenants.org_a)

    # School A3 has no settings (so no time zone to compute a period in): it has no row.
    assert [(r.school_id, r.school_name) for r in rows] == [
        (tenants.school_a1, "School A1"),
        (tenants.school_a2, "School A2"),
    ]
    for row in rows:
        assert tuple(getattr(row, name) for name in FIGURES) == _school_summary(
            admin_engine, row.school_id
        )
    a1, a2 = rows
    assert a2.total_in_cents == 100 and a2.entries_count == 1  # the cash contribution of A2
    assert a1.total_in_cents > a2.total_in_cents
    # The consolidated total of the network is the sum of its schools.
    assert sum(r.total_in_cents for r in rows) == a1.total_in_cents + 100


def test_a_school_context_sees_only_its_own_school(
    app_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    only_a1 = _rows(app_engine, TenantContext(tenants.org_a, tenants.school_a1), tenants.org_a)
    only_a2 = _rows(app_engine, TenantContext(tenants.org_a, tenants.school_a2), tenants.org_a)

    assert [r.school_id for r in only_a1] == [tenants.school_a1]
    assert [r.school_id for r in only_a2] == [tenants.school_a2]
    assert only_a2[0].total_in_cents == 100  # and not a cent of its sister school


def test_another_organization_sees_nothing_of_this_one(
    app_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    assert _rows(app_engine, TenantContext(tenants.org_b), tenants.org_a) == []
    own = _rows(app_engine, TenantContext(tenants.org_b), tenants.org_b)
    assert [r.school_id for r in own] == [tenants.school_b1]
    assert own[0].total_in_cents == 200


def test_without_a_context_nothing_is_visible(
    app_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    assert _rows(app_engine, None, tenants.org_a) == []


@pytest.mark.parametrize("asked", ["org_a", "org_b"])
def test_the_organization_argument_is_not_a_way_around_the_context(
    app_engine: Engine, tenants: Tenants, ledger: Ledger, asked: str
) -> None:
    """Asking for another organization by id returns nothing: the argument is not trusted, the policies are."""
    org = {"org_a": tenants.org_a, "org_b": tenants.org_b}[asked]
    context = TenantContext(tenants.org_b if asked == "org_a" else tenants.org_a)
    assert _rows(app_engine, context, org) == []


def test_the_administrator_without_row_level_security_sees_the_whole_network(
    admin_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    """The control: the data is there, it is the context that decides what the application sees."""
    rows = _rows(admin_engine, None, tenants.org_a)
    assert {r.school_id for r in rows} == {tenants.school_a1, tenants.school_a2}
