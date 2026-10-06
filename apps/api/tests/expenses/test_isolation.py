"""One school never sees or touches another's expenses, and the answer does not say whether the
thing exists: it is the same 404 (same body, apart from the request id) for a school outside the
session, an expense of another school, one of another author, and one that never was."""

import uuid
from typing import Any

from httpx2 import Response

from tests.expenses.support import Client, Scene, pdf

NOTHING = "00000000-0000-4000-8000-0000000000aa"


def shape(response: Response) -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = response.json()
    body.pop("request_id", None)
    return response.status_code, body


def assert_the_same_404(responses: list[Response]) -> None:
    first = shape(responses[0])
    assert first[0] == 404 and first[1]["code"] == "not_found"
    for response in responses[1:]:
        assert shape(response) == first


def every_way_to_touch(client: Client, expense_id: str, attachment_id: str, **kw: Any) -> list[Any]:
    upload: dict[str, Any] = {"files": {"file": ("a.pdf", pdf(), "application/pdf")}}
    return [
        client.get(f"/expenses/{expense_id}", **kw),
        client.patch(f"/expenses/{expense_id}", {"description": "x"}, **kw),
        client.post(f"/expenses/{expense_id}/submit", **kw),
        client.post(f"/expenses/{expense_id}/cancel", **kw),
        client.request("POST", f"/expenses/{expense_id}/attachments", **{**upload, **kw}),
        client.get(f"/expenses/{expense_id}/attachments/{attachment_id}", **kw),
    ]


def test_a_school_outside_the_session_is_the_same_404_as_one_that_does_not_exist(
    scene: Scene,
) -> None:
    teacher_1 = scene.person("staff", school="1", label="t1")
    teacher_2 = scene.person("staff", school="2", label="t2")
    foreign = scene.draft(teacher_2)

    valid = {
        "amount_cents": 100,
        "occurred_at": "2026-09-10",
        "category_id": scene.category(teacher_1.school),
        "description": "Cola",
        "paid_by": "APM",
    }
    for school in (teacher_2.school, scene.world.school_b1, uuid.uuid4(), NOTHING):
        assert_the_same_404(
            [
                teacher_1.get("/expenses", school=school),
                teacher_1.get("/expense-categories", school=school),
                teacher_1.post("/expenses", valid, school=school),
                teacher_1.get(f"/expenses/{foreign['id']}", school=school),
            ]
        )
    assert teacher_2.get("/expenses").json()["items"][0]["id"] == foreign["id"]  # nothing was made


def test_an_expense_of_another_school_is_not_there_for_the_school_in_the_path(
    scene: Scene,
) -> None:
    teacher_1 = scene.person("staff", school="1", label="t1")
    teacher_2 = scene.person("staff", school="2", label="t2")
    foreign = scene.draft(teacher_2)
    assert teacher_2.upload(foreign["id"]).status_code == 201
    attachment = teacher_2.get(f"/expenses/{foreign['id']}").json()["attachments"][0]["id"]

    mine = every_way_to_touch(teacher_1, foreign["id"], attachment)
    nothing = every_way_to_touch(teacher_1, NOTHING, NOTHING)

    assert_the_same_404(mine + nothing)
    assert teacher_2.get(f"/expenses/{foreign['id']}").json()["status"] == "DRAFT"
    assert teacher_1.get("/expenses").json()["items"] == []


def test_another_organization_cannot_see_the_school_nor_its_expenses(scene: Scene) -> None:
    teacher = scene.person("staff", school="1", label="t1")
    expense = scene.draft(teacher)
    treasurer_b = scene.person("treasurer", org="b", school="1", label="tb")
    admin_b = scene.person("organization_admin", org="b", school=None, label="ab")

    for outsider in (treasurer_b, admin_b):
        answers = [
            outsider.get("/expenses", school=teacher.school),
            outsider.get(f"/expenses/{expense['id']}", school=teacher.school),
            outsider.get("/expenses", school=uuid.uuid4()),
            outsider.get(f"/expenses/{NOTHING}", school=teacher.school),
        ]
        assert_the_same_404(answers)
        assert_the_same_404(
            [
                outsider.post(f"/expenses/{expense['id']}/approve", {}, school=teacher.school),
                outsider.post(f"/expenses/{NOTHING}/approve", {}, school=teacher.school),
            ]
        )
    assert scene.row(expense["id"]).status == "DRAFT"


def test_an_organization_wide_admin_reaches_every_school_of_the_organization_and_no_other(
    scene: Scene,
) -> None:
    teacher_1 = scene.person("staff", school="1", label="t1")
    teacher_2 = scene.person("staff", school="2", label="t2")
    admin = scene.person("organization_admin", school=None, label="admin")
    one, two = scene.draft(teacher_1), scene.draft(teacher_2)

    assert admin.get(f"/expenses/{one['id']}", school=teacher_1.school).status_code == 200
    assert admin.get(f"/expenses/{two['id']}", school=teacher_2.school).status_code == 200
    # the school in the path is the school of the expense: A1's expense is not under A2
    assert_the_same_404(
        [
            admin.get(f"/expenses/{one['id']}", school=teacher_2.school),
            admin.get(f"/expenses/{NOTHING}", school=teacher_2.school),
        ]
    )
    listed = {
        item["id"] for item in admin.get("/expenses", school=teacher_2.school).json()["items"]
    }
    assert two["id"] in listed and one["id"] not in listed
    assert admin.get("/expenses", school=scene.world.school_b1).status_code == 404


def test_a_colleague_cannot_see_an_expense_that_is_not_theirs(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    colleague = scene.person("staff", label="colleague")
    expense = scene.draft(author)
    assert author.upload(expense["id"]).status_code == 201
    attachment = author.get(f"/expenses/{expense['id']}").json()["attachments"][0]["id"]

    answers = every_way_to_touch(colleague, expense["id"], attachment)
    answers += every_way_to_touch(colleague, NOTHING, NOTHING)

    assert_the_same_404(answers)
    assert colleague.get("/expenses").json()["items"] == []
    assert scene.row(expense["id"]).status == "DRAFT"


def test_the_tenant_is_not_taken_from_headers_either(scene: Scene) -> None:
    teacher_1 = scene.person("staff", school="1", label="t1")
    teacher_2 = scene.person("staff", school="2", label="t2")
    foreign = scene.draft(teacher_2)
    forged = {
        "X-Organization-Id": str(scene.world.org_a),
        "X-School-Id": str(teacher_2.school),
        "X-Tenant-Id": str(teacher_2.school),
    }

    response = teacher_1.get(f"/expenses/{foreign['id']}", headers=forged)

    assert response.status_code == 404
    assert teacher_1.get("/expenses", headers=forged).json()["items"] == []
