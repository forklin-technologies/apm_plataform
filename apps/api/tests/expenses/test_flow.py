"""The whole life of an expense, as people live it: written by staff, sent, approved by someone
else, paid (the APM) or reimbursed (a collaborator)."""

from typing import Any

from sqlalchemy import Engine, text

from tests.expenses.support import Scene


def test_a_collaborators_expense_is_written_sent_approved_for_less_and_reimbursed(
    scene: Scene,
) -> None:
    teacher = scene.person("staff", label="teacher")
    treasurer = scene.person("treasurer", label="treasurer")

    expense = scene.draft(teacher, paid_by="COLLABORATOR", amount_cents=20_000)
    assert expense["status"] == "DRAFT"
    assert expense["amount_cents"] == 20_000
    assert expense["approved_amount_cents"] is None
    assert expense["submitted_by_user_id"] == str(teacher.user.id)
    assert expense["category"]["key"] == "school_supplies"
    assert expense["attachments"] == [] and expense["reimbursement"] is None
    assert expense["reference_code"] > 0
    expense_id = expense["id"]

    assert teacher.upload(expense_id).status_code == 201
    sent = teacher.post(f"/expenses/{expense_id}/submit").json()
    assert sent["status"] == "SUBMITTED" and sent["attachments_count"] == 1

    approved = treasurer.post(
        f"/expenses/{expense_id}/approve",
        {"approved_amount_cents": 17_500, "reason": "sem o frete"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "APPROVED"
    assert body["approved_amount_cents"] == 17_500 and body["amount_cents"] == 20_000
    assert body["approved_by_user_id"] == str(treasurer.user.id) and body["approved_at"]

    paid = treasurer.post(f"/expenses/{expense_id}/reimburse", {"payment_reference": "TED-0001"})
    assert paid.status_code == 200, paid.text
    done = paid.json()
    assert done["status"] == "PAID" and done["settled_at"]
    reimbursement = done["reimbursement"]
    assert reimbursement["status"] == "PAID"
    assert reimbursement["amount_cents"] == 17_500
    assert reimbursement["beneficiary_user_id"] == str(teacher.user.id)
    assert reimbursement["paid_by_user_id"] == str(treasurer.user.id)
    assert reimbursement["payment_reference"] == "TED-0001" and reimbursement["settled_at"]


def test_an_expense_the_apm_paid_is_approved_in_full_and_then_registered_as_paid(
    scene: Scene,
) -> None:
    teacher = scene.person("staff", label="teacher")
    treasurer = scene.person("treasurer", label="treasurer")
    expense = scene.approved(teacher, treasurer, paid_by="APM", amount_cents=9_900)
    assert expense["approved_amount_cents"] == 9_900

    paid = treasurer.post(f"/expenses/{expense['id']}/pay")

    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "PAID" and paid.json()["settled_at"]
    assert paid.json()["reimbursement"] is None


def test_the_cash_statement_counts_only_what_was_paid(scene: Scene, admin_engine: Engine) -> None:
    """An expense is outside the balance until it is PAID, and an expense a collaborator paid moves
    the cash only through its reimbursement (never twice)."""
    teacher = scene.person("staff", label="teacher")
    treasurer = scene.person("treasurer", label="treasurer")
    school = teacher.school

    def out_cents() -> int:
        with admin_engine.connect() as connection:
            total: int = connection.execute(
                text(
                    "SELECT coalesce(sum(amount_cents), 0) FROM financial_transactions "
                    "WHERE school_id = :s AND settled_at IS NOT NULL AND direction = 'OUT' AND "
                    "(kind = 'REIMBURSEMENT' OR (kind = 'EXPENSE' AND id IN (SELECT transaction_id "
                    "FROM expenses WHERE school_id = :s AND paid_by = 'APM')))"
                ),
                {"s": school},
            ).scalar_one()
        return total

    before = out_cents()
    apm = scene.approved(teacher, treasurer, paid_by="APM", amount_cents=1_000)
    collaborator = scene.approved(teacher, treasurer, paid_by="COLLABORATOR", amount_cents=2_000)
    assert out_cents() == before  # approved, not paid: not in the cash

    assert treasurer.post(f"/expenses/{apm['id']}/pay").status_code == 200
    assert out_cents() == before + 1_000
    reimbursed = treasurer.post(
        f"/expenses/{collaborator['id']}/reimburse", {"payment_reference": "PIX-77"}
    )
    assert reimbursed.status_code == 200
    assert out_cents() == before + 1_000 + 2_000


def test_a_rejected_expense_is_final_and_a_correction_goes_back_to_the_author(
    scene: Scene,
) -> None:
    teacher = scene.person("staff", label="teacher")
    director = scene.person("school_admin", label="director")

    first = scene.submitted(teacher)
    rejected = director.post(f"/expenses/{first['id']}/reject", {"reason": "nao e despesa da APM"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    assert rejected.json()["decision_reason"] == "nao e despesa da APM"
    assert teacher.patch(f"/expenses/{first['id']}", {"description": "outra"}).status_code == 409

    second = scene.submitted(teacher)
    asked = director.post(
        f"/expenses/{second['id']}/request-correction", {"reason": "falta a nota fiscal"}
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["status"] == "CORRECTION_REQUESTED"
    assert asked.json()["correction_reason"] == "falta a nota fiscal"
    edited = teacher.patch(f"/expenses/{second['id']}", {"amount_cents": 11_000})
    assert edited.status_code == 200 and edited.json()["amount_cents"] == 11_000
    again = teacher.post(f"/expenses/{second['id']}/submit")
    assert again.status_code == 200 and again.json()["status"] == "SUBMITTED"


def test_the_categories_for_the_form_are_the_money_out_ones_that_need_approval(
    scene: Scene,
) -> None:
    teacher = scene.person("staff", label="teacher")

    response = teacher.get("/expense-categories")

    assert response.status_code == 200
    keys = {item["key"] for item in response.json()["items"]}
    assert {"school_supplies", "services", "other_authorized"} <= keys
    assert not keys & {"bank_fees", "parent_contribution", "donation", "refund"}
    first: dict[str, Any] = response.json()["items"][0]
    assert set(first) == {"id", "key", "name"}


def test_creating_an_expense_validates_what_the_database_will_not(scene: Scene) -> None:
    teacher = scene.person("staff", label="teacher")
    good: dict[str, Any] = {
        "amount_cents": 500,
        "occurred_at": "2026-09-10",
        "category_id": scene.category(teacher.school),
        "description": "Cola",
        "paid_by": "APM",
    }

    def fails(**changes: Any) -> dict[str, Any]:
        response = teacher.post("/expenses", {**good, **changes})
        assert response.status_code == 422, (changes, response.text)
        assert response.headers["content-type"].startswith("application/problem+json")
        problem: dict[str, Any] = response.json()
        assert problem["code"] == "validation_error"
        return problem

    assert teacher.post("/expenses", good).status_code == 201
    for changes in (
        {"amount_cents": 0},
        {"amount_cents": -5},
        {"amount_cents": 10.5},
        {"amount_cents": "500"},
        {"amount_cents": 10**12 + 1},
        {"description": "   "},
        {"paid_by": "SCHOOL"},
        {"payment_method": "BITCOIN"},
        {"purchase_reason": "ab"},
        {"occurred_at": "not a date"},
        {"occurred_at": "2026-09-10T10:00:00"},  # an instant needs its UTC offset
        {"school_id": str(teacher.school)},  # the tenant never comes from the body
        {"organization_id": str(teacher.world.org_a)},
        {"submitted_by_user_id": str(teacher.user.id)},
    ):
        fails(**changes)

    assert fails(occurred_at="2999-01-01")["errors"] == [
        {"field": "occurred_at", "code": "in_the_future"}
    ]
    # a category that is not one of the form's: money in, bank fees, another school's, unknown
    for category in (
        scene.category(teacher.school, "parent_contribution"),
        scene.category(teacher.school, "bank_fees"),
        scene.category(teacher.world.school_a2),
        "00000000-0000-0000-0000-000000000000",
    ):
        assert fails(category_id=category)["errors"] == [
            {"field": "category_id", "code": "not_available"}
        ]


def test_a_day_means_noon_in_the_time_zone_of_the_school_and_an_instant_is_kept(
    scene: Scene,
) -> None:
    teacher = scene.person("staff", label="teacher")

    by_day = scene.draft(teacher, occurred_at="2026-03-05")
    by_instant = scene.draft(teacher, occurred_at="2026-03-05T20:30:00+00:00")

    assert by_day["occurred_at"].startswith("2026-03-05T15:00:00")  # 12:00 in Sao Paulo (UTC-3)
    assert by_instant["occurred_at"].startswith("2026-03-05T20:30:00")
