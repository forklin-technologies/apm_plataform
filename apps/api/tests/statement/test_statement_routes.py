"""The statement, the summary and the pending list over HTTP, against a ledger worked out by hand
(see the header of conftest.py): the API repeats what the database computed."""

from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from tests.authsupport import Api
from tests.statement.conftest import MARCH, OPENING, RUNNING, Login, Scene, statement_path


def march(api: Api, scene: Scene, **params: Any) -> list[dict[str, Any]]:
    response = api.get(statement_path(scene.fresh.school), params={"period": MARCH, **params})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def test_the_statement_of_march_repeats_the_columns_of_the_database(
    scene: Scene, login: Login
) -> None:
    api = login(scene.users["treasurer"])

    response = api.get(statement_path(scene.fresh.school), params={"period": MARCH})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["next_cursor"] is None
    items = body["items"]
    assert [item["signed_amount_cents"] for item in items] == [5000, 3000, -2000, -4000, -350, 500]
    assert [item["amount_cents"] for item in items] == [5000, 3000, 2000, 4000, 350, 500]
    assert [item["running_balance_cents"] for item in items] == RUNNING
    assert {item["opening_balance_cents"] for item in items} == {OPENING}
    assert [item["kind"] for item in items] == [
        "CONTRIBUTION", "CONTRIBUTION", "EXPENSE", "REIMBURSEMENT", "EXPENSE", "REFUND",
    ]  # fmt: skip
    assert [item["display_type"] for item in items] == [
        "INCOME", "INCOME", "EXPENSE", "EXPENSE", "EXPENSE", "REFUND",
    ]  # fmt: skip
    assert [item["direction"] for item in items] == ["IN", "IN", "OUT", "OUT", "OUT", "IN"]
    assert items[0]["origin_label"] == "Maria Exemplo"
    assert items[1]["origin_label"] == "João Teste"
    assert items[1]["report_group"] == "OTHER_INCOME"
    assert items[3]["status"] == "PAID"
    assert items[3]["status_label"] == "REIMBURSED"
    assert items[3]["beneficiary_label"] == "T5 staff"
    assert items[3]["description"] == "Tinta"
    assert items[4]["report_group"] == "BANK_FEES"
    assert items[5]["status"] == "CONFIRMED"
    assert items[0]["local_date"] == "2025-03-03"
    assert items[0]["settled_at"].startswith("2025-03-03T12:00:00")
    assert all(item["late_adjustment"] is False for item in items)
    assert [item["reference_code"] for item in items] == sorted(
        item["reference_code"] for item in items
    )
    assert items[0]["transaction_id"] == str(scene.entries["maria"])


@pytest.mark.parametrize(
    ("params", "amounts", "running"),
    [
        ({"kind": "CONTRIBUTION"}, [5000, 3000], [15000, 18000]),
        ({"kind": "REIMBURSEMENT"}, [-4000], [12000]),
        ({"kind": "REFUND"}, [500], [12150]),
        ({"display_type": "INCOME"}, [5000, 3000], [15000, 18000]),
        ({"display_type": "EXPENSE"}, [-2000, -4000, -350], [16000, 12000, 11650]),
        ({"display_type": "REFUND"}, [500], [12150]),
        ({"status": "REIMBURSED"}, [-4000], [12000]),
        ({"status": "CONFIRMED"}, [500], [12150]),
        ({"status": "PAID"}, [5000, 3000, -2000, -4000, -350], [15000, 18000, 16000, 12000, 11650]),
        ({"status": "REJECTED"}, [], []),
        ({"kind": "EXPENSE", "status": "PAID"}, [-2000, -350], [16000, 11650]),
    ],
    ids=lambda value: str(value) if isinstance(value, dict) else "",
)
def test_a_filter_chooses_rows_but_the_balances_stay_those_of_the_account(
    scene: Scene, login: Login, params: dict[str, str], amounts: list[int], running: list[int]
) -> None:
    items = march(login(scene.users["treasurer"]), scene, **params)

    assert [item["signed_amount_cents"] for item in items] == amounts
    assert [item["running_balance_cents"] for item in items] == running
    assert {item["opening_balance_cents"] for item in items} <= {OPENING}


def test_the_category_and_person_filters(scene: Scene, login: Login) -> None:
    api = login(scene.users["treasurer"])

    by_category = march(api, scene, category=str(scene.fresh.cat_other))
    by_person = march(api, scene, person=str(scene.fresh.staff))
    nobody = march(api, scene, person=str(uuid4()))

    assert [item["signed_amount_cents"] for item in by_category] == [3000]
    assert [item["running_balance_cents"] for item in by_category] == [18000]
    # The person is the origin or the beneficiary: the APM expense the staff member spent and the
    # reimbursement paid to them.
    assert [item["signed_amount_cents"] for item in by_person] == [-2000, -4000]
    assert [item["running_balance_cents"] for item in by_person] == [16000, 12000]
    assert nobody == []


@pytest.mark.parametrize(
    ("period", "amounts", "opening"),
    [
        ("2025-02", [10000], 0),
        ("2025-04", [700], 12150),
        ("2025-05", [], None),
        ("2024-12", [], None),
    ],
)
def test_the_period_is_a_calendar_month_and_the_opening_carries_over(
    scene: Scene, login: Login, period: str, amounts: list[int], opening: int | None
) -> None:
    api = login(scene.users["treasurer"])

    items = march(api, scene, period=period)

    assert [item["signed_amount_cents"] for item in items] == amounts
    if opening is not None:
        assert items[0]["opening_balance_cents"] == opening


def test_the_cursor_walks_the_statement_without_repeating_or_skipping(
    scene: Scene, login: Login
) -> None:
    api = login(scene.users["treasurer"])
    seen: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0

    while True:
        params: dict[str, Any] = {"period": MARCH, "limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = api.get(statement_path(scene.fresh.school), params=params).json()
        assert len(body["items"]) <= 2
        seen.extend(body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert [item["signed_amount_cents"] for item in seen] == [5000, 3000, -2000, -4000, -350, 500]
    assert [item["running_balance_cents"] for item in seen] == RUNNING
    full = march(api, scene)
    assert [item["transaction_id"] for item in seen] == [item["transaction_id"] for item in full]


def test_a_page_that_is_exactly_full_has_no_next_cursor(scene: Scene, login: Login) -> None:
    api = login(scene.users["treasurer"])

    body = api.get(statement_path(scene.fresh.school), params={"period": MARCH, "limit": 6}).json()

    assert len(body["items"]) == 6
    assert body["next_cursor"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": "many"},
        {"period": "2025-13"},
        {"period": "2025-3"},
        {"period": "March"},
        {"kind": "INCOME"},
        {"display_type": "CONTRIBUTION"},
        {"status": "paid; drop table"},
        {"category": "not-a-uuid"},
        {"person": "not-a-uuid"},
        {"cursor": "garbage"},
        {"cursor": "e30"},  # {} : valid base64 and JSON, but not a position
        {"cursor": "eyJyZWZlcmVuY2VfY29kZSI6MSwic2V0dGxlZF9hdCI6IjIwMjUtMDMtMDNUMTI6MDA6MDAifQ"},
    ],
    ids=str,
)
def test_bad_input_is_a_422_with_a_fixed_code(
    scene: Scene, login: Login, params: dict[str, Any]
) -> None:
    response = login(scene.users["treasurer"]).get(
        statement_path(scene.fresh.school), params=params
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "validation_error"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_the_summary_repeats_the_columns_of_statement_summary_with_both_balances(
    scene: Scene, login: Login
) -> None:
    response = login(scene.users["treasurer"]).get(
        statement_path(scene.fresh.school, "/summary"), params={"period": MARCH}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "period": "2025-03",
        "period_start": "2025-03-01",
        "period_end": "2025-03-31",
        "timezone": "America/Sao_Paulo",
        "opening_balance_cents": 10000,
        "contributions_in_cents": 5000,
        "other_in_cents": 3000,
        "refunds_in_cents": 500,
        "total_in_cents": 8500,
        "expenses_out_cents": 2350,
        "reimbursements_out_cents": 4000,
        "total_out_cents": 6350,
        "closing_balance_cents": 12150,
        "pending_reimbursements_cents": 1500,
        "balance_after_pending_cents": 10650,
        "entries_count": 6,
    }


def test_the_summary_of_an_empty_month_is_one_row_of_zeros(scene: Scene, login: Login) -> None:
    body = (
        login(scene.users["treasurer"])
        .get(statement_path(scene.fresh.school, "/summary"), params={"period": "2024-01"})
        .json()
    )

    assert body["entries_count"] == 0
    assert body["opening_balance_cents"] == body["closing_balance_cents"] == 0
    assert body["total_in_cents"] == body["total_out_cents"] == 0


def test_without_a_period_the_month_is_the_current_one_in_the_time_zone_of_the_school(
    scene: Scene, login: Login
) -> None:
    api = login(scene.users["treasurer"])
    now = datetime.now(ZoneInfo("America/Sao_Paulo"))

    body = api.get(statement_path(scene.fresh.school, "/summary")).json()

    assert body["period"] == f"{now.year:04d}-{now.month:02d}"
    assert api.get(statement_path(scene.fresh.school)).status_code == 200


def test_pending_lists_what_is_outside_the_balance(scene: Scene, login: Login) -> None:
    api = login(scene.users["treasurer"])

    body = api.get(statement_path(scene.fresh.school, "/pending")).json()

    assert body["next_cursor"] is None
    [row] = body["items"]
    assert row["transaction_id"] == str(scene.entries["pending_reimbursement"])
    assert (row["kind"], row["direction"], row["status"], row["section"]) == (
        "REIMBURSEMENT", "OUT", "PENDING", "PAYABLE",
    )  # fmt: skip
    assert row["amount_cents"] == 1500
    other_section = api.get(
        statement_path(scene.fresh.school, "/pending"), params={"section": "REVIEW"}
    ).json()
    assert other_section["items"] == []
    assert (
        api.get(
            statement_path(scene.fresh.school, "/pending"), params={"section": "NOPE"}
        ).status_code
        == 422
    )


# --- who may ask, and for which school ------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "statement", "summary", "pending"),
    [
        ("admin", 200, 200, 200),  # organization_admin
        ("school_admin", 200, 200, 200),
        ("treasurer", 200, 200, 200),
        ("viewer", 403, 200, 403),  # aggregate reports only
        ("staff", 403, 403, 403),
    ],
)
def test_each_role_gets_what_its_permissions_allow(
    scene: Scene, login: Login, role: str, statement: int, summary: int, pending: int
) -> None:
    api = login(scene.users[role])
    school = scene.fresh.school

    assert api.get(statement_path(school), params={"period": MARCH}).status_code == statement
    assert (
        api.get(statement_path(school, "/summary"), params={"period": MARCH}).status_code == summary
    )
    assert api.get(statement_path(school, "/pending")).status_code == pending


def test_the_summary_the_viewer_sees_is_the_same_aggregate(scene: Scene, login: Login) -> None:
    viewer = login(scene.users["viewer"]).get(
        statement_path(scene.fresh.school, "/summary"), params={"period": MARCH}
    )
    treasurer = login(scene.users["treasurer"]).get(
        statement_path(scene.fresh.school, "/summary"), params={"period": MARCH}
    )

    assert viewer.json() == treasurer.json()


def test_a_school_outside_the_session_is_a_404_identical_to_one_that_does_not_exist(
    scene: Scene, login: Login
) -> None:
    api = login(scene.users["treasurer"])
    unknown = uuid4()
    foreign = scene.other.school  # a school of ANOTHER organization
    sibling = scene.sibling_school  # same organization, but not the school of this membership

    answers = {
        label: api.get(statement_path(school, tail), params={"period": MARCH})
        for label, school in (("unknown", unknown), ("foreign", foreign), ("sibling", sibling))
        for tail in ("", "/summary", "/pending")
    }

    for label, response in answers.items():
        assert response.status_code == 404, label
        body = response.json()
        body.pop("request_id")
        assert body == {
            "type": "urn:apm-digital:problem:not_found",
            "title": "Not found",
            "status": 404,
            "code": "not_found",
        }, label


def test_an_organization_wide_admin_reaches_every_school_of_the_organization_and_no_other(
    scene: Scene, login: Login
) -> None:
    api = login(scene.users["admin"])

    own = api.get(statement_path(scene.fresh.school), params={"period": MARCH})
    sibling = api.get(statement_path(scene.sibling_school), params={"period": MARCH})
    foreign = api.get(statement_path(scene.other.school), params={"period": MARCH})

    assert own.status_code == 200 and len(own.json()["items"]) == 6
    assert sibling.status_code == 200 and sibling.json()["items"] == []
    assert foreign.status_code == 404


def test_what_another_organization_holds_never_shows_in_the_answers(
    scene: Scene, login: Login
) -> None:
    api = login(scene.users["treasurer"])

    text = api.get(statement_path(scene.fresh.school), params={"period": MARCH}).text
    summary = api.get(statement_path(scene.fresh.school, "/summary"), params={"period": MARCH}).text

    assert "Pessoa Alheia" not in text
    assert "99900" not in text + summary


def test_the_path_is_not_the_way_to_choose_a_tenant(scene: Scene, login: Login) -> None:
    api = login(scene.users["treasurer"])
    forged = {"X-Organization-Id": str(scene.other.org), "X-School-Id": str(scene.other.school)}

    response = api.get(statement_path(scene.other.school), params={"period": MARCH}, headers=forged)

    assert response.status_code == 404


def test_a_malformed_school_id_is_a_validation_error_not_a_server_error(
    scene: Scene, login: Login
) -> None:
    response = login(scene.users["treasurer"]).get("/api/v1/schools/not-a-uuid/statement")

    assert response.status_code == 422
