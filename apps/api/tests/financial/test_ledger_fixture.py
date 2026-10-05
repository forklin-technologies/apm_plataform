"""The ledger fixture itself: it must build, and every invariant it relies on must hold."""

from sqlalchemy import Engine, text

from tests.financial.conftest import Ledger


def test_the_ledger_fixture_builds_a_consistent_ledger(
    admin_engine: Engine, ledger: Ledger
) -> None:
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT kind, status, count(*) FROM financial_transactions "
                "WHERE school_id = :s GROUP BY 1, 2 ORDER BY 1, 2"
            ),
            {"s": ledger.fresh.school},
        ).all()
        closings: int = conn.execute(
            text("SELECT count(*) FROM monthly_closings WHERE school_id = :s"),
            {"s": ledger.fresh.school},
        ).scalar_one()
    assert len(rows) >= 8
    assert closings == 1
