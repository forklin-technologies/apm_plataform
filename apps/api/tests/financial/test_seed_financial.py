# ruff: noqa: E501  (SQL text)
"""The financial seed: default categories and fake demonstration movements, repeatable."""

from typing import Any

from sqlalchemy import create_engine, text

from app.db.tenant import TenantContext
from app.seed import seed
from app.seed_financial import DEFAULT_CATEGORIES
from tests.dbsupport import ScratchDb, run_alembic, transaction

MOVEMENTS_PER_SCHOOL = 20  # 8 contributions, 8 expenses, 2 reimbursements, 2 devoluções


def test_the_seed_writes_categories_and_demonstration_movements_and_repeats_cleanly(
    scratch_db: ScratchDb,
) -> None:
    assert run_alembic(scratch_db, "upgrade", "head").returncode == 0
    admin = create_engine(scratch_db.admin_url)
    app = create_engine(scratch_db.app_url)
    try:
        with admin.begin() as connection:
            first = seed(connection)
        with (
            admin.begin() as connection
        ):  # a new transaction: the consistency checks ran at the first commit
            second = seed(connection)
        # The trigger of the school installed the categories when seed() created the schools, so
        # this step adds none; the rows themselves are checked below.
        assert first.created["categories"] == 0
        assert first.created["financial_transactions"] == 3 * MOVEMENTS_PER_SCHOOL
        assert set(second.created.values()) == {0}  # repeatable

        with admin.connect() as connection:
            for school in ("demo-aurora", "demo-horizonte", "demo-central"):
                rows = connection.execute(
                    text(
                        "SELECT c.key, c.name, c.applies_to, c.report_group FROM categories c "
                        "JOIN schools s ON s.id = c.school_id WHERE s.slug = :s ORDER BY c.key"
                    ),
                    {"s": school},
                ).all()
                assert [tuple(r) for r in rows] == sorted(
                    DEFAULT_CATEGORIES
                )  # keys in English, names in Portuguese
                approvals: Any = dict(
                    connection.execute(
                        text(
                            "SELECT c.key, c.requires_approval FROM categories c "
                            "JOIN schools s ON s.id = c.school_id WHERE s.slug = :s"
                        ),
                        {"s": school},
                    ).all()
                )
                assert [k for k, needs in approvals.items() if not needs] == ["bank_fees"]
                methods = {
                    r[0]
                    for r in connection.execute(
                        text(
                            "SELECT DISTINCT c.method FROM contributions c "
                            "JOIN schools s ON s.id = c.school_id WHERE s.slug = :s"
                        ),
                        {"s": school},
                    )
                }
                assert methods == {"CASH", "TRANSFER", "PIX", "PIX_DIRECT"}
                fee = connection.execute(
                    text(
                        "SELECT f.status, f.origin_type, e.approved_amount_cents, e.approved_by_user_id "
                        "FROM financial_transactions f JOIN expenses e ON e.transaction_id = f.id "
                        "JOIN categories c ON c.id = f.category_id JOIN schools s ON s.id = f.school_id "
                        "WHERE s.slug = :s AND c.key = 'bank_fees'"
                    ),
                    {"s": school},
                ).one()
                assert tuple(fee) == ("PAID", "BANK", 590, None)  # settled with no approver
                count: Any = connection.execute(
                    text(
                        "SELECT count(*) FROM financial_transactions f JOIN schools s ON s.id = f.school_id "
                        "WHERE s.slug = :s"
                    ),
                    {"s": school},
                ).scalar_one()
                assert count == MOVEMENTS_PER_SCHOOL
                summary = connection.execute(
                    text(
                        "SELECT opening_balance_cents, total_in_cents, total_out_cents, closing_balance_cents "
                        "FROM statement_summary((SELECT id FROM schools WHERE slug = :s), "
                        "current_date - 60, current_date)"
                    ),
                    {"s": school},
                ).one()
                assert summary.total_in_cents > 0 and summary.total_out_cents > 0
                assert summary.closing_balance_cents == (
                    summary.opening_balance_cents + summary.total_in_cents - summary.total_out_cents
                )
                sections = {
                    r[0]
                    for r in connection.execute(
                        text(
                            "SELECT section FROM statement_pending((SELECT id FROM schools WHERE slug = :s))"
                        ),
                        {"s": school},
                    )
                }
                assert sections == {
                    "RECEIVABLE", "PAYABLE", "AWAITING_APPROVAL", "AWAITING_CORRECTION", "REVIEW"
                }  # fmt: skip
                # Every state of the product document is represented, and no credential is stored.
                states = {
                    r[0]
                    for r in connection.execute(
                        text(
                            "SELECT DISTINCT f.status FROM financial_transactions f "
                            "JOIN schools s ON s.id = f.school_id WHERE s.slug = :s"
                        ),
                        {"s": school},
                    )
                }
                assert {"DRAFT", "SUBMITTED", "CORRECTION_REQUESTED", "APPROVED", "PAID",
                        "REVIEW_REQUIRED", "PENDING_PAYMENT", "CONFIRMED", "REQUESTED",
                        "PENDING"} <= states  # fmt: skip
                account = connection.execute(
                    text(
                        "SELECT status, secret_ref FROM payment_accounts a "
                        "JOIN schools s ON s.id = a.school_id WHERE s.slug = :s"
                    ),
                    {"s": school},
                ).one()
                assert tuple(account) == ("ACTIVE", "env:APM_DEMO_PIX_SECRET")
            # Only fake data, and the audit of the demonstration is attributed to the system.
            names = [
                r[0]
                for r in connection.execute(
                    text("SELECT guardian_name FROM contributions WHERE guardian_name IS NOT NULL")
                )
            ]
            assert names and all("Demo" in name for name in names)
            actors = {
                r[0]
                for r in connection.execute(
                    text(
                        "SELECT DISTINCT actor_type FROM audit_logs WHERE entity_type = 'financial_transactions'"
                    )
                )
            }
            assert actors == {"SYSTEM"}

        # The application role sees the movements of ITS organization only.
        with admin.connect() as connection:
            rede: Any = connection.execute(
                text("SELECT id FROM organizations WHERE slug = 'demo-rede'")
            ).scalar_one()
            instituto: Any = connection.execute(
                text("SELECT id FROM organizations WHERE slug = 'demo-instituto'")
            ).scalar_one()
        with transaction(app, context=TenantContext(rede)) as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM financial_transactions")).scalar_one()
                == 2 * MOVEMENTS_PER_SCHOOL
            )
        with transaction(app, context=TenantContext(instituto)) as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM financial_transactions")).scalar_one()
                == MOVEMENTS_PER_SCHOOL
            )
    finally:
        admin.dispose()
        app.dispose()
