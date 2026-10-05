# ruff: noqa: E501  (SQL text)
"""The trigger inventory (derived from the catalog) and the rules the triggers enforce.

Every trigger the model relies on must exist, in the right order, and every function behind it
must be SECURITY INVOKER with a fixed search_path (so the closed list of SECURITY DEFINER functions
of ADR-016 does not change). Then each rule is exercised on its own, in a rolled-back transaction of
the admin (a superuser: the triggers fire, row level security does not get in the way).
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from tests.financial.support import (
    Fresh,
    add_cash_contribution,
    add_collaborator_expense,
    add_expense,
    add_pix_contribution,
    add_pix_review,
    add_refund,
    add_reimbursement,
    add_transaction,
    check_consistency,
    make_school,
    set_status,
    utc,
)
from tests.financial.test_isolation import TABLES

# --- the inventory -----------------------------------------------------------------------------

Trigger = tuple[str, frozenset[str], str, str]  # timing, events, level, function
B, A = "BEFORE", "AFTER"
INS, UPD, DEL, TRU = "INSERT", "UPDATE", "DELETE", "TRUNCATE"


def _t(timing: str, events: set[str], function: str, level: str = "ROW") -> Trigger:
    return timing, frozenset(events), level, function


EXPECTED_TRIGGERS: dict[tuple[str, str], Trigger] = {
    ("financial_transactions", "ft_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("financial_transactions", "ft_06_freeze"): _t(B, {UPD}, "freeze_when_final"),
    ("financial_transactions", "ft_07_change"): _t(B, {UPD}, "ft_check_change"),
    ("financial_transactions", "ft_08_initial"): _t(B, {INS}, "ft_check_initial"),
    ("financial_transactions", "ft_10_reference_code"): _t(B, {INS}, "ft_assign_reference_code"),
    ("financial_transactions", "ft_20_relations"): _t(B, {INS}, "ft_check_relations"),
    ("financial_transactions", "ft_25_member"): _t(B, {INS}, "assert_active_member"),
    ("financial_transactions", "ft_30_settle"): _t(B, {INS, UPD}, "ft_settle"),
    ("financial_transactions", "ft_90_consistency"): _t(A, {INS, UPD}, "ft_check_consistency"),
    ("contributions", "contributions_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("contributions", "contributions_10_anonymize"): _t(B, {UPD}, "assert_anonymize_only"),
    ("contributions", "contributions_12_set_once"): _t(B, {UPD}, "assert_set_once_columns"),
    ("contributions", "contributions_15_review"): _t(B, {UPD}, "contributions_check_review"),
    ("contributions", "contributions_20_insert"): _t(B, {INS}, "contributions_check_insert"),
    ("expenses", "expenses_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("expenses", "expenses_10_set_once"): _t(B, {UPD}, "assert_set_once_columns"),
    ("expenses", "expenses_12_waiver"): _t(B, {INS}, "expenses_apply_approval_waiver"),
    ("expenses", "expenses_15_decision_time"): _t(B, {INS, UPD}, "expenses_set_decision_time"),
    ("expenses", "expenses_20_editable"): _t(B, {UPD}, "expenses_edit_only_when_editable"),
    ("expenses", "expenses_25_member"): _t(B, {INS, UPD}, "assert_active_member"),
    ("reimbursements", "reimbursements_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("reimbursements", "reimbursements_10_set_once"): _t(B, {UPD}, "assert_set_once_columns"),
    ("reimbursements", "reimbursements_25_member"): _t(B, {INS, UPD}, "assert_active_member"),
    ("refunds", "refunds_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("refunds", "refunds_10_set_once"): _t(B, {UPD}, "assert_set_once_columns"),
    ("refunds", "refunds_25_member"): _t(B, {INS, UPD}, "assert_active_member"),
    ("expense_attachments", "expense_attachments_05_no_update"): _t(B, {UPD}, "forbid_update"),
    ("expense_attachments", "expense_attachments_20_state"): _t(
        B, {INS}, "expense_attachments_check_state"
    ),
    ("expense_attachments", "expense_attachments_25_member"): _t(B, {INS}, "assert_active_member"),
    ("categories", "categories_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("payment_accounts", "payment_accounts_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("school_settings", "school_settings_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("school_settings", "school_settings_10_timezone"): _t(
        B, {UPD}, "school_settings_lock_timezone"
    ),
    ("pix_charges", "pix_charges_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("pix_charges", "pix_charges_06_freeze"): _t(B, {UPD}, "freeze_when_final"),
    ("pix_charges", "pix_charges_10_set_once"): _t(B, {UPD}, "assert_set_once_columns"),
    ("pix_charges", "pix_charges_20_contribution"): _t(B, {INS}, "pix_charges_check_contribution"),
    ("pix_charges", "pix_charges_20_end_to_end_id"): _t(
        B, {INS, UPD}, "pix_charges_check_end_to_end_id"
    ),
    ("pix_charges", "pix_charges_90_consistency"): _t(
        A, {INS, UPD}, "pix_charges_check_consistency"
    ),
    ("webhook_events", "webhook_events_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("webhook_events", "webhook_events_10_set_once"): _t(B, {UPD}, "assert_set_once_columns"),
    ("audit_logs", "audit_logs_05_actor"): _t(B, {INS}, "audit_logs_fill_actor"),
    ("audit_logs", "audit_logs_10_member"): _t(B, {INS}, "assert_active_member"),
    ("audit_logs", "audit_logs_90_no_update"): _t(B, {UPD}, "forbid_update"),
    ("monthly_closings", "monthly_closings_05_immutable"): _t(B, {UPD}, "assert_immutable_columns"),
    ("monthly_closings", "monthly_closings_10_set_once"): _t(B, {UPD}, "assert_set_once_columns"),
    ("monthly_closings", "monthly_closings_15_reopen"): _t(B, {UPD}, "monthly_closings_reopen"),
    ("monthly_closings", "monthly_closings_20_snapshot"): _t(B, {INS}, "monthly_closings_snapshot"),
    ("monthly_closings", "monthly_closings_25_member"): _t(B, {INS, UPD}, "assert_active_member"),
    ("schools", "schools_10_settings"): _t(A, {INS}, "schools_create_settings"),
}
AUDITED = [
    "financial_transactions",
    "contributions",
    "expenses",
    "reimbursements",
    "refunds",
    "pix_charges",
    "payment_accounts",
    "expense_attachments",
    "categories",
    "school_settings",
    "monthly_closings",
]
for _table in AUDITED:
    EXPECTED_TRIGGERS[(_table, f"{_table}_95_audit")] = _t(A, {INS, UPD}, "audit_row_change")
for _table in TABLES:
    EXPECTED_TRIGGERS[(_table, f"{_table}_no_delete")] = _t(B, {DEL}, "forbid_delete")
    EXPECTED_TRIGGERS[(_table, f"{_table}_no_truncate")] = _t(
        B, {TRU}, "forbid_truncate", "STATEMENT"
    )

# Within a table and a timing, triggers fire in alphabetical order: this is the order the model needs.
EXPECTED_ORDER: dict[tuple[str, str, str], list[str]] = {
    ("financial_transactions", B, INS): [
        "ft_08_initial",
        "ft_10_reference_code",
        "ft_20_relations",
        "ft_25_member",
        "ft_30_settle",
    ],
    ("financial_transactions", B, UPD): [
        "ft_05_immutable",
        "ft_06_freeze",
        "ft_07_change",
        "ft_30_settle",
    ],
    ("contributions", B, UPD): [
        "contributions_05_immutable",
        "contributions_10_anonymize",
        "contributions_12_set_once",
        "contributions_15_review",
    ],
    ("contributions", B, INS): ["contributions_20_insert"],
    ("expenses", B, INS): [
        "expenses_12_waiver",
        "expenses_15_decision_time",
        "expenses_25_member",
    ],
    ("pix_charges", B, INS): ["pix_charges_20_contribution", "pix_charges_20_end_to_end_id"],
    ("pix_charges", B, UPD): [
        "pix_charges_05_immutable",
        "pix_charges_06_freeze",
        "pix_charges_10_set_once",
        "pix_charges_20_end_to_end_id",
    ],
    ("expenses", B, UPD): [
        "expenses_05_immutable",
        "expenses_10_set_once",
        "expenses_15_decision_time",
        "expenses_20_editable",
        "expenses_25_member",
    ],
    ("audit_logs", B, INS): ["audit_logs_05_actor", "audit_logs_10_member"],
    ("monthly_closings", B, UPD): [
        "monthly_closings_05_immutable",
        "monthly_closings_10_set_once",
        "monthly_closings_15_reopen",
        "monthly_closings_25_member",
    ],
}

EXPECTED_FUNCTIONS = {
    "app_org", "app_school", "assert_active_member", "assert_anonymize_only",
    "assert_immutable_columns", "assert_set_once_columns", "audit_logs_fill_actor",
    "audit_row_change", "closing_breakdown", "closing_entries_hash", "contributions_check_review",
    "expense_attachments_check_state", "expenses_edit_only_when_editable", "ft_check_change",
    "ft_check_initial", "org_statement_summary",
    "expenses_set_decision_time", "forbid_delete", "forbid_truncate", "forbid_update",
    "freeze_when_final", "ft_assign_reference_code", "ft_check_consistency",
    "ft_check_relations", "ft_settle", "monthly_closings_reopen", "monthly_closings_snapshot",
    "pix_charges_check_contribution", "school_settings_lock_timezone", "schools_create_settings",
    "pix_charges_check_consistency", "contributions_check_insert", "expenses_apply_approval_waiver", "pix_charges_check_end_to_end_id",
    "statement_entries", "statement_pending", "statement_summary", "verify_closing",
    # tenancy and authentication (0003 to 0006): the context functions and the closed list of ADR-016
    "app_session_id", "app_user_id", "accept_invitation", "find_login_identity",
    "list_memberships_for_user",
}  # fmt: skip
# The only SECURITY DEFINER functions of the schema (ADR-016, owned by apm_definer; the financial
# schema has none). tests/test_app_role.py asserts their owner, search_path and who may execute them.
DEFINER_FUNCTIONS = {"accept_invitation", "find_login_identity", "list_memberships_for_user"}


def _decode(trigger_type: int) -> tuple[str, frozenset[str], str]:
    level = "ROW" if trigger_type & 1 else "STATEMENT"
    timing = "BEFORE" if trigger_type & 2 else ("INSTEAD OF" if trigger_type & 64 else "AFTER")
    events = frozenset(
        name for bit, name in ((4, INS), (8, DEL), (16, UPD), (32, TRU)) if trigger_type & bit
    )
    return timing, events, level


def _catalog_triggers(admin_engine: Engine) -> dict[tuple[str, str], tuple[Trigger, bool, str]]:
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname, t.tgname, t.tgtype, p.proname, t.tgdeferrable AND t.tginitdeferred, "
                "t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace"
            )
        ).all()
    result = {}
    for table, name, trigger_type, function, deferred, enabled in rows:
        timing, events, level = _decode(trigger_type)
        result[(table, name)] = ((timing, events, level, function), bool(deferred), enabled)
    return result


def test_every_expected_trigger_exists_and_nothing_else(admin_engine: Engine) -> None:
    """Dropping or disabling a trigger fails here; so does adding one nobody documented."""
    found = _catalog_triggers(admin_engine)

    assert {key: value[0] for key, value in found.items()} == EXPECTED_TRIGGERS
    assert {key for key, value in found.items() if value[2] != "O"} == set()  # all enabled
    deferred = {key for key, value in found.items() if value[1]}
    assert deferred == {
        ("financial_transactions", "ft_90_consistency"),
        ("pix_charges", "pix_charges_90_consistency"),
    }


def test_the_triggers_fire_in_the_order_the_model_needs(admin_engine: Engine) -> None:
    found = _catalog_triggers(admin_engine)
    for (table, timing, event), expected in EXPECTED_ORDER.items():
        actual = sorted(
            name
            for (t, name), (trigger, _deferred, _enabled) in found.items()
            if t == table and trigger[0] == timing and event in trigger[1]
        )
        assert actual == expected, (table, timing, event)


def test_every_function_is_security_invoker_with_a_fixed_search_path(admin_engine: Engine) -> None:
    """The financial functions are all SECURITY INVOKER; the closed list of SECURITY DEFINER
    functions (ADR-016) is not touched by this task."""
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT p.proname, p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner), "
                "p.provolatile, pg_get_function_result(p.oid) = 'trigger' FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
            )
        ).all()
    assert {row[0] for row in rows} == EXPECTED_FUNCTIONS
    for name, security_definer, config, owner, volatility, _is_trigger in rows:
        assert security_definer is (name in DEFINER_FUNCTIONS), name
        assert config == ["search_path=pg_catalog"], name
        assert owner == ("apm_definer" if name in DEFINER_FUNCTIONS else "apm_owner"), name
        if name.startswith(("statement_", "org_statement_")) or name in (
            "closing_entries_hash",
            "closing_breakdown",
            "verify_closing",
        ):
            assert volatility == "s", name  # STABLE: they only read


def test_no_security_definer_function_exists_anywhere(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        definers = connection.execute(
            text(
                "SELECT n.nspname || '.' || p.proname FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
            )
        ).all()
    assert sorted(row[0] for row in definers) == sorted(
        f"public.{name}" for name in DEFINER_FUNCTIONS
    )  # the closed list of ADR-016, nothing else


# --- rules --------------------------------------------------------------------------------------


@contextmanager
def refused(conn: Connection, match: str) -> Iterator[None]:
    """The block must fail with a database error that matches; the transaction stays usable."""
    with pytest.raises(DBAPIError, match=match), conn.begin_nested():
        yield


def _scalar(conn: Connection, sql: str, **params: object) -> Any:
    return conn.execute(text(sql), params).scalar_one()


# Condition 1: an author must be an ACTIVE member of the school, visible in the context.


@pytest.mark.parametrize("status", ["invited", "suspended", "revoked"])
def test_a_member_who_is_not_active_cannot_be_named(
    world: tuple[Connection, Fresh], status: str
) -> None:
    conn, f = world
    conn.execute(
        text("UPDATE memberships SET status = :s WHERE user_id = :u"), {"s": status, "u": f.staff}
    )
    with refused(conn, "invalid user reference in created_by_user_id"):
        add_transaction(
            conn, f, kind="CONTRIBUTION", direction="IN", amount=100, status="PENDING_PAYMENT",
            created_by=f.staff,
        )  # fmt: skip


def test_an_active_member_and_an_organization_wide_member_can_be_named(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    for user in (f.staff, f.treasurer, f.admin):  # the admin's membership covers every school
        add_transaction(
            conn, f, kind="CONTRIBUTION", direction="IN", amount=100, status="PENDING_PAYMENT",
            created_by=user, origin_user_id=user,
        )  # fmt: skip


def test_the_origin_user_is_guarded_like_the_author(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    with refused(conn, "invalid user reference in origin_user_id"):
        add_transaction(
            conn, f, kind="CONTRIBUTION", direction="IN", amount=100, status="PENDING_PAYMENT",
            origin_user_id=f.outsider,
        )  # fmt: skip


def test_a_member_of_another_school_of_the_same_organization_cannot_be_named(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    other_school, user = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO schools (id, organization_id, name, slug) VALUES (:s, :o, 'Other', :slug)"
        ),
        {"s": other_school, "o": f.org, "slug": f"t5-other-{uuid.uuid4().hex[:10]}"},
    )
    conn.execute(
        text("INSERT INTO users (id, email, full_name) VALUES (:u, :e, 'Other staff')"),
        {"u": user, "e": f"t5-other-{uuid.uuid4().hex[:10]}@example.test"},
    )
    conn.execute(
        text(
            "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
            "VALUES (:u, :o, :s, 'staff', 'active')"
        ),
        {"u": user, "o": f.org, "s": other_school},
    )
    with refused(conn, "invalid user reference"):
        add_transaction(
            conn, f, kind="CONTRIBUTION", direction="IN", amount=100, status="PENDING_PAYMENT",
            created_by=user,
        )  # fmt: skip


def test_the_decider_of_an_expense_is_checked_when_it_is_set(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense = add_expense(conn, f, 1000)
    with refused(conn, "invalid user reference in approved_by_user_id"):
        conn.execute(
            text("UPDATE expenses SET approved_by_user_id = :u WHERE transaction_id = :t"),
            {"u": f.outsider, "t": expense},
        )
    conn.execute(
        text("UPDATE expenses SET approved_by_user_id = :u WHERE transaction_id = :t"),
        {"u": f.treasurer, "t": expense},
    )


def test_who_paid_a_reimbursement_and_who_confirmed_a_refund_are_checked(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    _, reimbursement = add_collaborator_expense(conn, f, 1000)
    with refused(conn, "invalid user reference in paid_by_user_id"):
        conn.execute(
            text("UPDATE reimbursements SET paid_by_user_id = :u WHERE transaction_id = :t"),
            {"u": f.outsider, "t": reimbursement},
        )
    refund = add_refund(conn, f, 100)
    with refused(conn, "invalid user reference in confirmed_by_user_id"):
        conn.execute(
            text("UPDATE refunds SET confirmed_by_user_id = :u WHERE transaction_id = :t"),
            {"u": f.outsider, "t": refund},
        )


def test_nobody_decides_their_own_expense(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    expense = add_expense(conn, f, 1000)
    with refused(conn, "ck_expenses_decider_is_not_submitter"):
        conn.execute(
            text("UPDATE expenses SET approved_by_user_id = :u WHERE transaction_id = :t"),
            {"u": f.staff, "t": expense},  # the submitter
        )


def test_the_time_of_the_decision_is_set_by_the_database(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    expense = add_expense(conn, f, 1000)
    conn.execute(
        text("UPDATE expenses SET approved_by_user_id = :u WHERE transaction_id = :t"),
        {"u": f.treasurer, "t": expense},
    )
    assert _scalar(conn, "SELECT approved_at FROM expenses WHERE transaction_id = :t", t=expense)


# Condition 3: personal data of a contribution can only be erased.


def test_personal_data_can_only_be_anonymized(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx = add_cash_contribution(
        conn, f, 5000, utc(2025, 3, 5, 15), guardian="Maria", student="João", class_name="3A",
        email="maria@example.test", phone="+55 11 99999-0000",
    )  # fmt: skip
    for column in (
        "guardian_name", "student_name", "class_name", "contributor_email", "contributor_phone"
    ):  # fmt: skip
        with refused(conn, "can only be erased, never rewritten"):
            conn.execute(
                text(
                    f"UPDATE contributions SET {column} = 'Someone else' WHERE transaction_id = :t"  # noqa: S608
                ),
                {"t": tx},
            )
    conn.execute(
        text(
            "UPDATE contributions SET guardian_name = NULL, student_name = NULL, class_name = NULL, "
            "contributor_email = NULL, contributor_phone = NULL WHERE transaction_id = :t"
        ),
        {"t": tx},
    )
    with refused(conn, "can only be erased, never rewritten"):  # and cannot be written back
        conn.execute(
            text("UPDATE contributions SET guardian_name = 'Maria' WHERE transaction_id = :t"),
            {"t": tx},
        )


# The rules that look at two tables (checked when the transaction commits).


@pytest.mark.parametrize("method", ["CASH", "TRANSFER", "OTHER"])
def test_a_manual_contribution_records_who_entered_it_and_is_born_paid(
    world: tuple[Connection, Fresh], method: str
) -> None:
    conn, f = world
    tx = add_transaction(
        conn, f, kind="CONTRIBUTION", direction="IN", amount=500, status="PAID",
        occurred_at=utc(2025, 3, 5), settled_at=utc(2025, 3, 5),
    )  # fmt: skip
    conn.execute(
        text(
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method) "
            "VALUES (:t, :o, :s, :m)"
        ),
        {"t": tx, "o": f.org, "s": f.school, "m": method},
    )  # no author
    with refused(conn, "a manual contribution .* is born PAID and records who entered it"):
        check_consistency(conn)


def test_a_manual_contribution_cannot_be_pending(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    tx = add_transaction(
        conn, f, kind="CONTRIBUTION", direction="IN", amount=500, status="PENDING_PAYMENT",
        created_by=f.treasurer,
    )  # fmt: skip
    conn.execute(
        text(
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method) "
            "VALUES (:t, :o, :s, 'TRANSFER')"
        ),
        {"t": tx, "o": f.org, "s": f.school},
    )
    with refused(conn, "born PAID"):
        check_consistency(conn)


def test_a_pix_contribution_is_paid_only_with_a_charge_that_received_exactly_its_amount(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    tx, _ = add_pix_contribution(conn, f, 3000)
    set_status(conn, tx, "PAID", utc(2025, 3, 6, 15))  # PAID with no confirmation from the provider
    with refused(conn, "only PAID with a charge that received exactly its amount"):
        check_consistency(conn)


def test_a_pix_contribution_with_a_confirmed_charge_can_be_paid(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    add_pix_contribution(conn, f, 3000, paid_at=utc(2025, 3, 10, 15))
    check_consistency(conn)


def test_the_ledger_row_needs_the_detail_row_of_its_kind(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    add_transaction(
        conn, f, kind="EXPENSE", direction="OUT", amount=100, status="SUBMITTED",
        created_by=f.staff,
    )  # fmt: skip
    with refused(conn, "has no expenses row"):
        check_consistency(conn)


def test_a_paid_reimbursement_records_who_paid_it_and_the_reference(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    expense = add_expense(conn, f, 1000, paid_by="COLLABORATOR", status="APPROVED")
    reimbursement = add_reimbursement(conn, f, expense)
    set_status(conn, reimbursement, "PAID", utc(2025, 3, 6, 15))  # nothing recorded
    with refused(conn, "records who paid it and its payment reference"):
        check_consistency(conn)


def test_a_confirmed_refund_records_who_confirmed_it(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    refund = add_refund(conn, f, 100, status="AWAITING_CONFIRMATION")
    set_status(conn, refund, "CONFIRMED", utc(2025, 3, 6, 15))
    with refused(conn, "records who confirmed it"):
        check_consistency(conn)


def test_a_consistent_ledger_commits_clean(world: tuple[Connection, Fresh]) -> None:
    """The deferred check accepts everything the builders write when the data is right."""
    conn, f = world
    add_cash_contribution(conn, f, 10000, utc(2025, 3, 5, 15))
    add_cash_contribution(conn, f, 700, utc(2025, 3, 5, 16), method="TRANSFER")
    add_pix_contribution(conn, f, 3000, paid_at=utc(2025, 3, 6, 15))
    add_pix_review(conn, f, 3300, 3350)
    add_expense(conn, f, 2500, status="PAID", settled_at=utc(2025, 3, 20, 15))
    add_expense(conn, f, 900, status="DRAFT")
    add_expense(conn, f, 900, status="CORRECTION_REQUESTED")
    add_collaborator_expense(conn, f, 4000, reimbursed_at=utc(2025, 3, 25, 15))
    paid = add_expense(conn, f, 1000, status="PAID", settled_at=utc(2025, 3, 8, 15))
    add_refund(
        conn,
        f,
        300,
        parent=paid,
        parent_kind="EXPENSE",
        status="CONFIRMED",
        settled_at=utc(2025, 3, 9, 15),
    )
    add_refund(conn, f, 50)  # no parent: a wrong payment
    check_consistency(conn)


# The reference code.


def test_the_reference_code_is_sequential_per_school_and_never_chosen_by_the_caller(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    other = make_school(conn)
    first = add_cash_contribution(conn, f, 100, utc(2025, 3, 1, 15))
    add_cash_contribution(conn, other, 100, utc(2025, 3, 1, 15))
    add_cash_contribution(conn, f, 100, utc(2025, 3, 1, 15))
    # Even the admin asking for a code gets the next one, never its own number.
    chosen: int = conn.execute(
        text(
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
            "amount_cents, status, category_id, origin_type, reference_code) VALUES (:o, :s, "
            "'CONTRIBUTION', 'IN', 100, 'PENDING_PAYMENT', :c, 'GUARDIAN', 777) "
            "RETURNING reference_code"
        ),
        {"o": f.org, "s": f.school, "c": f.cat_in},
    ).scalar_one()

    codes = [
        row[0]
        for row in conn.execute(
            text(
                "SELECT reference_code FROM financial_transactions WHERE school_id = :s ORDER BY 1"
            ),
            {"s": f.school},
        )
    ]
    assert codes == [1, 2, 3] and chosen == 3
    assert (
        _scalar(conn, "SELECT reference_code FROM financial_transactions WHERE id = :t", t=first)
        == 1
    )
    assert [
        row[0]
        for row in conn.execute(
            text("SELECT reference_code FROM financial_transactions WHERE school_id = :s"),
            {"s": other.school},
        )
    ] == [1]


def test_a_multi_row_insert_gets_consecutive_codes(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    conn.execute(
        text(
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
            "amount_cents, status, category_id, origin_type) SELECT :o, :s, 'CONTRIBUTION', 'IN', "
            "100, 'PENDING_PAYMENT', :c, 'GUARDIAN' FROM generate_series(1, 5)"
        ),
        {"o": f.org, "s": f.school, "c": f.cat_in},
    )
    codes = [
        row[0]
        for row in conn.execute(
            text(
                "SELECT reference_code FROM financial_transactions WHERE school_id = :s ORDER BY 1"
            ),
            {"s": f.school},
        )
    ]
    assert codes == [1, 2, 3, 4, 5]


# The booking of a settlement.


def test_a_settlement_in_the_future_is_refused(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    ten_minutes = _scalar(conn, "SELECT now() + interval '10 minutes'")
    two_minutes = _scalar(conn, "SELECT now() + interval '2 minutes'")
    with refused(conn, "settled_at cannot be in the future"):
        add_cash_contribution(conn, f, 100, ten_minutes)
    add_cash_contribution(conn, f, 100, two_minutes)  # inside the tolerance


def test_the_flag_of_a_late_adjustment_is_never_taken_from_the_caller(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    row = conn.execute(
        text(
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
            "amount_cents, status, category_id, origin_type, settled_at, late_adjustment, "
            "created_by_user_id) VALUES (:o, :s, 'CONTRIBUTION', 'IN', 100, 'PAID', :c, 'GUARDIAN', "
            ":at, true, :u) RETURNING late_adjustment, settled_at"
        ),
        {"o": f.org, "s": f.school, "c": f.cat_in, "at": utc(2025, 3, 1, 15), "u": f.treasurer},
    ).one()
    assert row[0] is False and row[1] == utc(2025, 3, 1, 15)  # no closing: booked as given


# Pix charges and payment accounts.


def test_a_pix_charge_needs_a_pending_pix_contribution_of_the_same_amount(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    tx, charge = add_pix_contribution(conn, f, 3000)
    cash = add_cash_contribution(conn, f, 3000, utc(2025, 3, 1, 15))
    sql = text(
        "INSERT INTO pix_charges (organization_id, school_id, transaction_id, payment_account_id, "
        "provider, txid, amount_cents, expires_at) VALUES (:o, :s, :t, :account, 'SANDBOX', :x, :a, "
        "now() + interval '30 minutes')"
    )
    args = {"o": f.org, "s": f.school, "account": f.account, "x": uuid.uuid4().hex}
    conn.execute(text("UPDATE pix_charges SET status = 'EXPIRED' WHERE id = :c"), {"c": charge})
    with refused(conn, "exact amount"):
        conn.execute(sql, {**args, "t": tx, "a": 3001})
    with refused(conn, "needs a PENDING_PAYMENT Pix contribution"):
        conn.execute(sql, {**args, "t": cash, "a": 3000})
    conn.execute(sql, {**args, "t": tx, "a": 3000})
    with refused(conn, "uq_pix_charges_one_pending_per_contribution"):
        conn.execute(sql, {**args, "x": uuid.uuid4().hex, "t": tx, "a": 3000})


def test_a_pix_charge_needs_the_active_account_of_the_school(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    conn.execute(
        text("UPDATE payment_accounts SET status = 'INACTIVE' WHERE id = :a"), {"a": f.account}
    )
    with refused(conn, "needs the ACTIVE payment account"):
        add_pix_contribution(conn, f, 3000)
    conn.execute(
        text("UPDATE payment_accounts SET status = 'ACTIVE' WHERE id = :a"), {"a": f.account}
    )
    add_pix_contribution(conn, f, 3000)


def test_a_confirmed_pix_charge_is_final(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    _, charge = add_pix_contribution(conn, f, 3000, paid_at=utc(2025, 3, 6, 15))
    with refused(conn, "is final"):
        conn.execute(
            text("UPDATE pix_charges SET status = 'CANCELLED' WHERE id = :c"), {"c": charge}
        )


# School settings.


def test_a_school_is_born_with_its_settings(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    row = conn.execute(
        text(
            "SELECT timezone, min_contribution_cents, suggested_amounts_cents, allow_custom_amount, "
            "pix_expiration_minutes, required_fields, optional_fields "
            "FROM school_settings WHERE school_id = :s"
        ),
        {"s": f.school},
    ).one()
    assert tuple(row) == (
        "America/Sao_Paulo", 1000, [2000, 3000, 4000], True, 30, [],
        ["guardian_name", "contributor_email", "contributor_phone"],
    )  # fmt: skip


def test_the_time_zone_is_frozen_by_the_first_settlement(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    conn.execute(
        text("UPDATE school_settings SET timezone = 'Asia/Tokyo' WHERE school_id = :s"),
        {"s": f.school},
    )  # still free
    add_cash_contribution(conn, f, 100, utc(2025, 3, 1, 15))
    with refused(conn, "time zone cannot change"):
        conn.execute(
            text("UPDATE school_settings SET timezone = 'America/Sao_Paulo' WHERE school_id = :s"),
            {"s": f.school},
        )


def test_an_unknown_time_zone_is_refused(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    with refused(conn, "time zone"):
        conn.execute(
            text("UPDATE school_settings SET timezone = 'Mars/Olympus' WHERE school_id = :s"),
            {"s": f.school},
        )
