"""The actor of the audit comes from the context of the request (never from a column the service
writes), and what the database refuses reaches the client as a problem with a stable code, with no
text of the database in it."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.errors import ProblemError
from app.expenses.db_errors import database_rules, problem_for
from tests.expenses.support import Scene, pdf, refusal

# --- the audit ------------------------------------------------------------------------------------


def test_every_change_is_recorded_with_the_user_and_the_request_that_made_it(
    scene: Scene,
) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")

    created = author.post(
        "/expenses",
        {
            "amount_cents": 4_200,
            "occurred_at": "2026-09-10",
            "category_id": scene.category(author.school),
            "description": "Descricao secreta da despesa",
            "vendor": "Fornecedor Reservado",
            "purchase_reason": "Motivo particular",
            "payment_method": "CARD",
            "paid_by": "APM",
        },
    )
    expense_id = created.json()["id"]
    created_request = created.headers["x-request-id"]
    assert author.upload(expense_id).status_code == 201
    assert author.post(f"/expenses/{expense_id}/submit").status_code == 200
    approved = treasurer.post(f"/expenses/{expense_id}/approve", {"reason": "Parecer reservado"})
    approved_request = approved.headers["x-request-id"]

    rows = scene.audit(expense_id)

    by_action: dict[str, list[Any]] = {}
    for row in rows:
        by_action.setdefault(row.action, []).append(row)
    for action in (
        "financial_transactions.insert",
        "expenses.insert",
        "expense.created",
        "expense.submitted",
        "financial_transactions.update",
        "expenses.update",
        "expense.approved",
    ):
        assert action in by_action, action
    # who and which request: the creation is the author's, the decision is the approver's
    for action in ("financial_transactions.insert", "expenses.insert", "expense.created"):
        assert {(r.actor_user_id, r.actor_type, r.request_id) for r in by_action[action]} == {
            (author.user.id, "USER", created_request)
        }
    approval = list(by_action["expense.approved"])
    assert [(r.actor_user_id, r.actor_type, r.request_id) for r in approval] == [
        (treasurer.user.id, "USER", approved_request)
    ]
    decided = [r for r in by_action["expenses.update"] if r.request_id == approved_request]
    assert decided and all(r.actor_user_id == treasurer.user.id for r in decided)
    # nothing of the free text is in the log
    with scene.engine.connect() as connection:
        dumped = " ".join(
            connection.execute(
                text(
                    "SELECT coalesce(before_data::text, '') || coalesce(after_data::text, '') "
                    "FROM audit_logs WHERE entity_id = :id"
                ),
                {"id": expense_id},
            ).scalars()
        )
    for secret in ("Descricao secreta", "Fornecedor Reservado", "Motivo particular", "Parecer"):
        assert secret not in dumped


def test_the_event_of_a_decision_holds_ids_amounts_and_states_only(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    expense = scene.submitted(author, paid_by="COLLABORATOR", amount_cents=3_000)
    assert treasurer.post(
        f"/expenses/{expense['id']}/approve", {"approved_amount_cents": 2_000}
    ).is_success

    with scene.engine.connect() as connection:
        event = connection.execute(
            text(
                "SELECT after_data, entity_reference FROM audit_logs "
                "WHERE entity_id = :id AND action = 'expense.approved'"
            ),
            {"id": expense["id"]},
        ).one()

    assert event.after_data == {
        "status": "APPROVED",
        "amount_cents": 3_000,
        "approved_amount_cents": 2_000,
    }
    assert event.entity_reference == expense["reference_code"]


def test_a_reimbursement_is_recorded_for_the_person_who_paid_without_the_reference(
    scene: Scene,
) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    expense = scene.approved(author, treasurer, paid_by="COLLABORATOR")
    paid = treasurer.post(
        f"/expenses/{expense['id']}/reimburse", {"payment_reference": "TED-AUDITADO-123"}
    )
    reimbursement_id = paid.json()["reimbursement"]["id"]

    rows = scene.audit(reimbursement_id)

    assert rows and {r.actor_user_id for r in rows} == {treasurer.user.id}
    assert {r.request_id for r in rows} == {paid.headers["x-request-id"]}
    with scene.engine.connect() as connection:
        dumped = " ".join(
            connection.execute(
                text("SELECT coalesce(after_data::text, '') FROM audit_logs WHERE entity_id = :id"),
                {"id": reimbursement_id},
            ).scalars()
        )
    assert "TED-AUDITADO-123" not in dumped and "payment_reference_present" in dumped


# --- what the database refuses --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sqlstate", "constraint", "status", "code"),
    [
        ("23505", None, 409, "conflict"),
        (
            "23505",
            "uq_financial_transactions_one_active_reimbursement",
            409,
            "reimbursement_exists",
        ),
        (
            "23505",
            "uq_expense_attachments_school_id_transaction_id_sha256",
            409,
            "attachment_duplicate",
        ),
        ("23514", None, 422, "rule_violation"),
        ("23514", "ck_expenses_decider_is_not_submitter", 403, "self_approval_forbidden"),
        ("23001", None, 409, "state_conflict"),
        ("23503", None, 422, "reference_invalid"),
        ("22003", None, 422, "value_out_of_range"),
        ("40001", None, 409, "retry"),
        ("40P01", None, 409, "retry"),
    ],
)
def test_a_refusal_of_the_database_becomes_a_problem_with_a_stable_code(
    sqlstate: str, constraint: str | None, status: int, code: str
) -> None:
    problem = problem_for(refusal(sqlstate, constraint))

    assert problem is not None
    assert (problem.status, problem.code) == (status, code)
    assert "secret" not in f"{problem.title} {problem.detail}"


@pytest.mark.parametrize("sqlstate", ["08006", "57P01", "42501", "XX000", ""])
def test_what_is_not_a_known_refusal_is_not_hidden_as_one(sqlstate: str) -> None:
    assert problem_for(refusal(sqlstate)) is None


def test_the_guard_rolls_back_and_translates_but_lets_the_rest_through() -> None:
    class Spy:
        rolled_back = 0

        def rollback(self) -> None:
            self.rolled_back += 1

    spy = Spy()
    with pytest.raises(ProblemError) as caught, database_rules(spy):  # type: ignore[arg-type]
        raise refusal("23514")
    assert caught.value.code == "rule_violation" and spy.rolled_back == 1
    assert caught.value.__cause__ is None  # nothing of the database error is chained

    with pytest.raises(DBAPIError), database_rules(spy):  # type: ignore[arg-type]
        raise refusal("08006")
    assert spy.rolled_back == 2
    with pytest.raises(ValueError), database_rules(spy):  # type: ignore[arg-type]
        raise ValueError("not a database matter")


def test_a_real_refusal_of_the_database_reaches_the_client_without_its_text(
    scene: Scene,
) -> None:
    """The same file twice: the service does not look first, the unique index says no."""
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    content = pdf()
    assert author.upload(expense["id"], content).status_code == 201

    response = author.upload(expense["id"], content)

    assert response.status_code == 409 and response.json()["code"] == "attachment_duplicate"
    assert set(response.json()) == {"type", "title", "status", "code", "request_id"}
    for leaked in ("duplicate key", "violates", "expense_attachments", "uq_", "DETAIL"):
        assert leaked not in response.text


def test_a_person_who_left_the_school_cannot_be_reimbursed_and_the_answer_is_a_problem(
    scene: Scene,
) -> None:
    """The database checks that the person to reimburse is still a member: a refusal the service did
    not foresee comes back as 422 `reference_invalid`, with no text of the database."""
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    expense = scene.approved(author, treasurer, paid_by="COLLABORATOR")
    scene.users.set_membership_status(author.user.membership_ids[0], "suspended")

    response = treasurer.post(
        f"/expenses/{expense['id']}/reimburse", {"payment_reference": "TED-1"}
    )

    assert response.status_code == 422 and response.json()["code"] == "reference_invalid"
    assert "invalid user reference" not in response.text
    assert scene.row(expense["id"]).status == "APPROVED"  # nothing was half done
    with scene.engine.connect() as connection:
        children: int = connection.execute(
            text("SELECT count(*) FROM financial_transactions WHERE parent_transaction_id = :id"),
            {"id": expense["id"]},
        ).scalar_one()
    assert children == 0
