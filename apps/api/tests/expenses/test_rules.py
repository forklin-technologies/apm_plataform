"""The rules of the service on top of the ledger: the author never decides on their own expense,
the edit window, partial approval, the reimbursement, what a person may cancel."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.expenses.support import Client, Scene

# --- the author never decides ---------------------------------------------------------------------


@pytest.mark.parametrize("role", ["school_admin", "organization_admin"])
def test_the_author_never_approves_rejects_or_asks_a_correction_of_their_own_expense(
    scene: Scene, role: str
) -> None:
    school = None if role == "organization_admin" else "1"
    author = scene.person(role, school=school, label="author")
    other = scene.person("school_admin", label="other")
    expense = scene.submitted(author)

    for action, body in (
        ("approve", {}),
        ("approve", {"approved_amount_cents": 100}),
        ("reject", {"reason": "nao quero"}),
        ("request-correction", {"reason": "corrija"}),
    ):
        response = author.post(f"/expenses/{expense['id']}/{action}", body)
        assert response.status_code == 403, (action, response.text)
        assert response.json()["code"] == "self_approval_forbidden"
        assert response.headers["content-type"].startswith("application/problem+json")

    assert scene.row(expense["id"]).status == "SUBMITTED"
    assert scene.row(expense["id"]).approved_by_user_id is None
    assert other.post(f"/expenses/{expense['id']}/approve", {}).status_code == 200
    decided = scene.row(expense["id"])
    assert decided.approved_by_user_id == other.user.id != decided.submitted_by_user_id


def test_the_person_reimbursed_does_not_register_their_own_reimbursement(scene: Scene) -> None:
    author = scene.person("school_admin", label="author")
    other = scene.person("school_admin", label="other")
    expense = scene.approved(author, other, paid_by="COLLABORATOR")

    own = author.post(f"/expenses/{expense['id']}/reimburse", {"payment_reference": "TED-1"})

    assert own.status_code == 403 and own.json()["code"] == "self_reimbursement_forbidden"
    assert scene.row(expense["id"]).status == "APPROVED"
    assert (
        other.post(
            f"/expenses/{expense['id']}/reimburse", {"payment_reference": "TED-1"}
        ).status_code
        == 200
    )


def test_the_author_does_not_register_the_payment_of_their_own_expense(scene: Scene) -> None:
    author = scene.person("school_admin", label="author")
    other = scene.person("school_admin", label="other")
    expense = scene.approved(author, other, paid_by="APM")

    own = author.post(f"/expenses/{expense['id']}/pay")

    assert own.status_code == 403 and own.json()["code"] == "self_payment_forbidden"
    assert own.headers["content-type"].startswith("application/problem+json")
    assert scene.row(expense["id"]).status == "APPROVED"
    assert scene.row(expense["id"]).settled_at is None
    assert other.post(f"/expenses/{expense['id']}/pay").status_code == 200


# --- the edit window ------------------------------------------------------------------------------


def test_an_expense_is_edited_by_its_author_while_it_is_a_draft_or_awaits_a_correction(
    scene: Scene,
) -> None:
    author = scene.person("staff", label="author")
    director = scene.person("school_admin", label="director")
    expense = scene.draft(author)
    path = f"/expenses/{expense['id']}"

    edited = author.patch(
        path,
        {
            "amount_cents": 999,
            "description": "Tinta guache",
            "vendor": None,
            "payment_method": "PIX",
        },
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["amount_cents"], body["description"], body["vendor"]) == (
        999,
        "Tinta guache",
        None,
    )
    assert body["payment_method"] == "PIX" and body["purchase_reason"] == expense["purchase_reason"]

    other_category = scene.category(author.school, "services")
    assert (
        author.patch(path, {"category_id": other_category}).json()["category"]["key"] == "services"
    )
    assert (
        author.patch(path, {"occurred_at": "2026-08-01"})
        .json()["occurred_at"]
        .startswith("2026-08-01T15:00")
    )

    # not by someone who is not the author, even an administrator who can see it
    denied = director.patch(path, {"description": "mudei"})
    assert denied.status_code == 403 and denied.json()["code"] == "author_only"
    # not the payer, not the author, not a school, not an empty or null edit, not a bad category
    for bad in (
        {"paid_by": "COLLABORATOR"},
        {"submitted_by_user_id": str(director.user.id)},
        {"school_id": str(author.school)},
        {},
        {"description": None},
        {"amount_cents": None},
        {"amount_cents": 0},
        {"category_id": scene.category(author.school, "bank_fees")},
    ):
        assert author.patch(path, bad).status_code == 422, bad

    assert author.upload(expense["id"]).status_code == 201
    assert author.post(f"{path}/submit").status_code == 200
    frozen = author.patch(path, {"amount_cents": 1})
    assert frozen.status_code == 409 and frozen.json()["code"] == "invalid_state"
    assert scene.row(expense["id"]).amount_cents == 999


def test_an_approved_or_final_expense_cannot_be_edited(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    approved = scene.approved(author, treasurer)
    cancelled = scene.draft(author)
    assert author.post(f"/expenses/{cancelled['id']}/cancel").status_code == 200

    for expense in (approved, cancelled):
        response = author.patch(f"/expenses/{expense['id']}", {"amount_cents": 1})
        assert response.status_code == 409 and response.json()["code"] == "invalid_state"


# --- sending --------------------------------------------------------------------------------------


def test_an_expense_is_sent_only_with_an_attachment_a_reason_and_a_way_it_was_paid(
    scene: Scene,
) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author, purchase_reason=None, payment_method=None)
    path = f"/expenses/{expense['id']}"

    refused = author.post(f"{path}/submit")

    assert refused.status_code == 422 and refused.json()["code"] == "expense_incomplete"
    assert {error["field"] for error in refused.json()["errors"]} == {
        "purchase_reason",
        "payment_method",
        "attachments",
    }
    assert author.upload(expense["id"]).status_code == 201
    assert author.patch(
        path, {"purchase_reason": "Aula de artes", "payment_method": "CASH"}
    ).is_success
    assert author.post(f"{path}/submit").json()["status"] == "SUBMITTED"


def test_only_the_author_sends_an_expense(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    director = scene.person("school_admin", label="director")
    expense = scene.draft(author)
    assert author.upload(expense["id"]).status_code == 201

    response = director.post(f"/expenses/{expense['id']}/submit")

    assert response.status_code == 403 and response.json()["code"] == "author_only"
    assert scene.row(expense["id"]).status == "DRAFT"


# --- partial approval and the reimbursement -------------------------------------------------------


def test_less_than_requested_is_approved_only_for_a_collaborator_and_never_more(
    scene: Scene,
) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    apm = scene.submitted(author, paid_by="APM", amount_cents=1_000)
    collaborator = scene.submitted(author, paid_by="COLLABORATOR", amount_cents=1_000)

    less = treasurer.post(f"/expenses/{apm['id']}/approve", {"approved_amount_cents": 999})
    assert less.status_code == 422 and less.json()["code"] == "partial_approval_not_allowed"
    for bad in (1_001, 0, -1, 10.5):
        refused = treasurer.post(
            f"/expenses/{collaborator['id']}/approve", {"approved_amount_cents": bad}
        )
        assert refused.status_code == 422, bad
    assert scene.row(apm["id"]).status == scene.row(collaborator["id"]).status == "SUBMITTED"

    assert treasurer.post(
        f"/expenses/{apm['id']}/approve", {"approved_amount_cents": 1_000}
    ).is_success
    half = treasurer.post(
        f"/expenses/{collaborator['id']}/approve", {"approved_amount_cents": 400}
    ).json()
    assert (half["amount_cents"], half["approved_amount_cents"]) == (1_000, 400)
    paid = treasurer.post(
        f"/expenses/{collaborator['id']}/reimburse", {"payment_reference": "PIX-1"}
    ).json()
    assert paid["reimbursement"]["amount_cents"] == 400  # exactly what was approved
    assert paid["amount_cents"] == 1_000  # the request stays as it was


def test_an_omitted_approved_amount_is_the_requested_one(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    expense = scene.submitted(author, paid_by="COLLABORATOR", amount_cents=7_777)

    approved = treasurer.post(f"/expenses/{expense['id']}/approve").json()  # no body at all

    assert approved["approved_amount_cents"] == 7_777


def test_who_gets_paid_how(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    apm = scene.approved(author, treasurer, paid_by="APM")
    collaborator = scene.approved(author, treasurer, paid_by="COLLABORATOR")
    ref = {"payment_reference": "TED-9"}

    not_applicable = treasurer.post(f"/expenses/{apm['id']}/reimburse", ref)
    assert not_applicable.status_code == 409
    assert not_applicable.json()["code"] == "reimbursement_not_applicable"
    by_reimbursement = treasurer.post(f"/expenses/{collaborator['id']}/pay")
    assert by_reimbursement.status_code == 409
    assert by_reimbursement.json()["code"] == "payment_by_reimbursement"

    assert treasurer.post(f"/expenses/{collaborator['id']}/reimburse", ref).status_code == 200
    assert treasurer.post(f"/expenses/{apm['id']}/pay").status_code == 200
    for response in (
        treasurer.post(f"/expenses/{collaborator['id']}/reimburse", ref),
        treasurer.post(f"/expenses/{apm['id']}/pay"),
    ):
        assert response.status_code == 409 and response.json()["code"] == "invalid_state"


@pytest.mark.parametrize("reference", ["", "   ", "x" * 201])
def test_a_reimbursement_needs_a_reference_of_the_payment(scene: Scene, reference: str) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    expense = scene.approved(author, treasurer, paid_by="COLLABORATOR")

    response = treasurer.post(
        f"/expenses/{expense['id']}/reimburse", {"payment_reference": reference}
    )

    assert response.status_code == 422
    assert scene.row(expense["id"]).status == "APPROVED"


def test_a_reason_has_at_least_three_characters(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    expense = scene.submitted(author)
    path = f"/expenses/{expense['id']}"

    for action in ("reject", "request-correction"):
        for reason in ("", "ab", "  a ", None):
            assert treasurer.post(f"{path}/{action}", {"reason": reason}).status_code == 422
        assert treasurer.post(f"{path}/{action}", {}).status_code == 422
    assert treasurer.post(f"{path}/approve", {"reason": "ab"}).status_code == 422
    assert scene.row(expense["id"]).status == "SUBMITTED"
    assert treasurer.post(f"{path}/reject", {"reason": "abc"}).status_code == 200


# --- states ---------------------------------------------------------------------------------------


def test_an_action_out_of_its_state_is_a_409_with_a_stable_code(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    draft = scene.draft(author)
    approved = scene.approved(author, treasurer, paid_by="COLLABORATOR")
    ref = {"payment_reference": "TED-1"}

    cases: list[tuple[Client, str, str, Any]] = [
        (treasurer, draft["id"], "approve", {}),
        (treasurer, draft["id"], "reject", {"reason": "abc"}),
        (treasurer, draft["id"], "request-correction", {"reason": "abc"}),
        (treasurer, draft["id"], "reimburse", ref),
        (treasurer, draft["id"], "pay", None),
        (author, approved["id"], "submit", None),
        (treasurer, approved["id"], "approve", {}),
        (treasurer, approved["id"], "reject", {"reason": "abc"}),
    ]
    for client, expense_id, action, body in cases:
        response = client.post(f"/expenses/{expense_id}/{action}", body)
        assert response.status_code == 409, (action, response.text)
        assert response.json()["code"] == "invalid_state"
    assert scene.row(draft["id"]).status == "DRAFT"
    assert scene.row(approved["id"]).status == "APPROVED"


def test_what_a_person_may_cancel(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    director = scene.person("school_admin", label="director")
    treasurer = scene.person("treasurer", label="treasurer")

    # an author cancels their draft; nobody else does (even who can see it)
    draft = scene.draft(author)
    denied = director.post(f"/expenses/{draft['id']}/cancel")
    assert denied.status_code == 403 and denied.json()["code"] == "author_only"
    assert author.post(f"/expenses/{draft['id']}/cancel").json()["status"] == "CANCELLED"
    # a cancelled expense is final
    assert author.post(f"/expenses/{draft['id']}/cancel").status_code == 409
    assert author.upload(draft["id"]).status_code == 409

    # a sent expense is not cancelled: it is decided (or sent back)
    sent = scene.submitted(author)
    assert author.post(f"/expenses/{sent['id']}/cancel").status_code == 409
    assert treasurer.post(f"/expenses/{sent['id']}/cancel").status_code == 409

    # an approved one is cancelled by an approver, not by a person who only writes expenses
    approved = scene.approved(author, treasurer)
    own = author.post(f"/expenses/{approved['id']}/cancel")
    assert own.status_code == 403 and own.json()["code"] == "permission_denied"
    assert treasurer.post(f"/expenses/{approved['id']}/cancel").json()["status"] == "CANCELLED"

    # one waiting for a correction goes back to its author, who may cancel it
    again = scene.submitted(author)
    assert treasurer.post(
        f"/expenses/{again['id']}/request-correction", {"reason": "abc"}
    ).is_success
    assert author.post(f"/expenses/{again['id']}/cancel").json()["status"] == "CANCELLED"


def test_a_paid_expense_is_final(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    expense = scene.approved(author, treasurer, paid_by="APM")
    assert treasurer.post(f"/expenses/{expense['id']}/pay").status_code == 200

    for response in (
        treasurer.post(f"/expenses/{expense['id']}/cancel"),
        author.patch(f"/expenses/{expense['id']}", {"amount_cents": 1}),
        author.upload(expense["id"]),
    ):
        assert response.status_code == 409, response.text


def test_two_approvers_at_the_same_moment_decide_once(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    first = scene.person("treasurer", label="first")
    second = scene.person("school_admin", label="second")
    expense = scene.submitted(author, paid_by="COLLABORATOR", amount_cents=2_000)
    path = f"/expenses/{expense['id']}"

    with ThreadPoolExecutor(2) as pool:
        answers = list(
            pool.map(
                lambda who: who[0].post(
                    f"{path}/{who[1]}",
                    {"approved_amount_cents": 1_500} if who[1] == "approve" else {"reason": "abc"},
                ),
                [(first, "approve"), (second, "reject")],
            )
        )

    assert sorted(answer.status_code for answer in answers) == [200, 409]
    final = scene.row(expense["id"])
    assert final.status in ("APPROVED", "REJECTED")
    assert (final.status == "APPROVED") == (final.approved_amount_cents == 1_500)
