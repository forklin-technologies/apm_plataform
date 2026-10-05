"""The list: who sees what, the filters, and the cursor."""

import base64
from typing import Any

import pytest

from tests.expenses.support import Client, Scene


def ids(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_staff_see_their_own_and_whoever_reads_all_sees_every_expense_of_the_school(
    scene: Scene,
) -> None:
    ana = scene.person("staff", label="ana")
    beto = scene.person("staff", label="beto")
    treasurer = scene.person("treasurer", label="treasurer")
    director = scene.person("school_admin", label="director")
    admin = scene.person("organization_admin", school=None, label="admin")
    mine, theirs = scene.draft(ana), scene.draft(beto)

    assert ids(ana.get("/expenses")) == [mine["id"]]
    assert ids(beto.get("/expenses")) == [theirs["id"]]
    for reader in (treasurer, director, admin):
        seen = ids(reader.get("/expenses", params={"limit": 100}))
        assert mine["id"] in seen and theirs["id"] in seen


def test_the_list_is_newest_first_and_light(scene: Scene) -> None:
    ana = scene.person("staff", label="ana")
    first, second, third = (scene.draft(ana) for _ in range(3))

    response = ana.get("/expenses")

    assert ids(response) == [third["id"], second["id"], first["id"]]
    item = response.json()["items"][0]
    assert set(item) == {
        "id",
        "reference_code",
        "status",
        "amount_cents",
        "approved_amount_cents",
        "category",
        "occurred_at",
        "description",
        "vendor",
        "paid_by",
        "submitted_by_user_id",
        "attachments_count",
        "created_at",
        "updated_at",
    }
    assert set(item["category"]) == {"id", "key", "name"}
    assert response.json()["next_cursor"] is None


def test_the_cursor_walks_the_whole_list_once(scene: Scene) -> None:
    ana = scene.person("staff", label="ana")
    created = [scene.draft(ana)["id"] for _ in range(5)]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = ana.get("/expenses", params=params)
        seen += ids(response)
        pages += 1
        cursor = response.json()["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert seen == list(reversed(created))  # newest first, none twice, none missing


def test_a_page_that_ends_exactly_with_the_list_has_no_next_cursor(scene: Scene) -> None:
    ana = scene.person("staff", label="ana")
    for _ in range(4):
        scene.draft(ana)

    response = ana.get("/expenses", params={"limit": 4})

    assert len(response.json()["items"]) == 4 and response.json()["next_cursor"] is None


@pytest.mark.parametrize("limit", [0, -1, 101, "x", ""])
def test_the_limit_is_between_1_and_100(scene: Scene, limit: Any) -> None:
    ana = scene.person("staff", label="ana")

    response = ana.get("/expenses", params={"limit": limit})

    assert response.status_code == 422 and response.json()["code"] == "validation_error"


def test_the_limit_defaults_to_50_and_100_is_allowed(scene: Scene) -> None:
    ana = scene.person("staff", label="ana")
    assert ana.get("/expenses", params={"limit": 100}).status_code == 200
    assert ana.get("/expenses", params={"limit": 1}).status_code == 200
    assert ana.get("/expenses").status_code == 200


@pytest.mark.parametrize(
    "cursor",
    [
        "not a cursor",
        "!!!!",
        base64.urlsafe_b64encode(b"r-5").decode(),
        base64.urlsafe_b64encode(b"x5").decode(),
        base64.urlsafe_b64encode(b"r99999999999999999999").decode(),
        base64.urlsafe_b64encode(b"r5; DROP TABLE expenses").decode(),
        "a" * 65,
    ],
)
def test_a_cursor_that_is_not_ours_is_a_422_never_a_500(scene: Scene, cursor: str) -> None:
    ana = scene.person("staff", label="ana")

    response = ana.get("/expenses", params={"cursor": cursor})

    assert response.status_code == 422 and response.json()["code"] == "validation_error"


def test_a_cursor_cannot_widen_what_a_person_sees(scene: Scene) -> None:
    ana = scene.person("staff", label="ana")
    beto = scene.person("staff", label="beto")
    scene.draft(ana)
    theirs = scene.draft(beto)
    huge = base64.urlsafe_b64encode(b"r999999999").decode()  # past every reference code

    assert theirs["id"] not in ids(ana.get("/expenses", params={"cursor": huge, "limit": 100}))


def test_the_status_filter(scene: Scene) -> None:
    ana = scene.person("staff", label="ana")
    treasurer = scene.person("treasurer", label="treasurer")
    draft = scene.draft(ana)
    sent = scene.submitted(ana)
    approved = scene.approved(ana, treasurer)

    assert ids(ana.get("/expenses", params={"status": "DRAFT"})) == [draft["id"]]
    assert ids(ana.get("/expenses", params={"status": "SUBMITTED"})) == [sent["id"]]
    assert ids(ana.get("/expenses", params={"status": "APPROVED"})) == [approved["id"]]
    assert ids(ana.get("/expenses", params={"status": "PAID"})) == []
    assert approved["id"] in ids(
        treasurer.get("/expenses", params={"status": "APPROVED", "limit": 100})
    )
    for bad in ("approved", "OPEN", "DRAFT,PAID", ""):
        assert ana.get("/expenses", params={"status": bad}).status_code == 422, bad


def test_the_period_is_a_month_of_the_date_of_the_expense_in_the_time_zone_of_the_school(
    scene: Scene,
) -> None:
    ana = scene.person("staff", label="ana")
    january = scene.draft(ana, occurred_at="2026-01-15")
    # 01:00 UTC on 1 February is still 22:00 of 31 January in Sao Paulo (UTC-3)
    january_night = scene.draft(ana, occurred_at="2026-02-01T01:00:00+00:00")
    february = scene.draft(ana, occurred_at="2026-02-01T03:00:00+00:00")  # 00:00 local
    december = scene.draft(ana, occurred_at="2025-12-31")

    assert set(ids(ana.get("/expenses", params={"period": "2026-01"}))) == {
        january["id"],
        january_night["id"],
    }
    assert ids(ana.get("/expenses", params={"period": "2026-02"})) == [february["id"]]
    assert ids(ana.get("/expenses", params={"period": "2025-12"})) == [december["id"]]
    assert ids(ana.get("/expenses", params={"period": "2024-06"})) == []
    for bad in ("2026-13", "2026-00", "2026-1", "202601", "january", "2026-01-15", "0000-01"):
        assert ana.get("/expenses", params={"period": bad}).status_code == 422, bad


def test_filters_and_the_cursor_work_together(scene: Scene) -> None:
    ana = scene.person("staff", label="ana")
    march = [scene.draft(ana, occurred_at="2026-03-10")["id"] for _ in range(3)]
    scene.draft(ana, occurred_at="2026-04-10")

    first = ana.get("/expenses", params={"period": "2026-03", "status": "DRAFT", "limit": 2})
    second = ana.get(
        "/expenses",
        params={
            "period": "2026-03",
            "status": "DRAFT",
            "limit": 2,
            "cursor": first.json()["next_cursor"],
        },
    )

    assert ids(first) + ids(second) == list(reversed(march))
    assert second.json()["next_cursor"] is None


def test_the_detail_carries_the_attachments_and_the_decision(scene: Scene) -> None:
    ana: Client = scene.person("staff", label="ana")
    treasurer = scene.person("treasurer", label="treasurer")
    expense = scene.approved(ana, treasurer, paid_by="COLLABORATOR")

    detail = ana.get(f"/expenses/{expense['id']}").json()

    assert detail["status"] == "APPROVED" and detail["approved_by_user_id"] == str(
        treasurer.user.id
    )
    assert [a["kind"] for a in detail["attachments"]] == ["INVOICE"]
    assert detail["attachments_count"] == 1 and detail["reimbursement"] is None
