# ruff: noqa: E501  (SQL text)
"""The monthly closing over HTTP: the database computes the snapshot, the API only asks for it."""

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, text

from app.closing import queries
from tests.authsupport import Api
from tests.statement.conftest import MARCH, Login, Scene


def closings(school: uuid.UUID, tail: str = "") -> str:
    return f"/api/v1/schools/{school}/closings{tail}"


def close(api: Api, scene: Scene, period: str = MARCH, **body: Any) -> Any:
    return api.post(closings(scene.fresh.school), {"period": period, **body})


FIGURES_OF_MARCH = {
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
    "closing_after_pending_cents": 10650,
    "entries_count": 6,
}


def test_closing_march_writes_the_snapshot_the_database_computed(
    scene: Scene, login: Login, admin_engine: Engine
) -> None:
    api = login(scene.users["treasurer"])

    response = close(api, scene, bank_balance_reported_cents=12000)

    assert response.status_code == 201, response.text
    body = response.json()
    assert {key: body[key] for key in FIGURES_OF_MARCH} == FIGURES_OF_MARCH
    assert body["school_id"] == str(scene.fresh.school)
    assert body["bank_balance_reported_cents"] == 12000
    assert body["bank_difference_cents"] == -150  # the bank holds LESS than the platform
    assert body["closed_by_user_id"] == str(scene.users["treasurer"].id)
    assert body["report_ref"] is None
    assert body["reopened_at"] is None and body["reopen_reason"] is None
    assert len(body["entries_hash"]) == 64
    assert body["breakdown"] == {
        "CONTRIBUTIONS": {
            "in": 5000, "out": 0, "count": 1,
            "categories": {"parent_contribution": {"in": 5000, "out": 0, "count": 1}},
        },
        "OTHER_INCOME": {
            "in": 3000, "out": 0, "count": 1,
            "categories": {"donation": {"in": 3000, "out": 0, "count": 1}},
        },
        "EXPENSES_REIMBURSEMENTS": {
            "in": 0, "out": 6000, "count": 2,
            "categories": {
                "school_supplies": {"in": 0, "out": 2000, "count": 1},
                "teacher_reimbursement": {"in": 0, "out": 4000, "count": 1},
            },
        },
        "BANK_FEES": {
            "in": 0, "out": 350, "count": 1,
            "categories": {"bank_fees": {"in": 0, "out": 350, "count": 1}},
        },
        "REFUNDS": {
            "in": 500, "out": 0, "count": 1,
            "categories": {"refund": {"in": 500, "out": 0, "count": 1}},
        },
    }  # fmt: skip
    with admin_engine.connect() as conn:  # the hash is the one the database function computes
        recomputed: str = conn.execute(
            text("SELECT closing_entries_hash(:s, '2025-03-01', '2025-03-31')"),
            {"s": scene.fresh.school},
        ).scalar_one()
    assert body["entries_hash"] == recomputed


def test_without_a_bank_balance_there_is_no_difference(scene: Scene, login: Login) -> None:
    body = close(login(scene.users["treasurer"]), scene).json()

    assert body["bank_balance_reported_cents"] is None
    assert body["bank_difference_cents"] is None


def test_a_difference_to_the_bank_does_not_stop_the_closing(scene: Scene, login: Login) -> None:
    response = close(login(scene.users["treasurer"]), scene, bank_balance_reported_cents=99999)

    assert response.status_code == 201
    assert response.json()["bank_difference_cents"] == 99999 - 12150


def test_a_month_that_has_not_ended_cannot_be_closed(scene: Scene, login: Login) -> None:
    api = login(scene.users["treasurer"])
    now = datetime.now(ZoneInfo("America/Sao_Paulo"))

    this_month = close(api, scene, f"{now.year:04d}-{now.month:02d}")
    future = close(api, scene, "2099-12")

    for response in (this_month, future):
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "period_not_ended"


def test_months_are_closed_in_order(scene: Scene, login: Login) -> None:
    api = login(scene.users["treasurer"])
    assert close(api, scene).status_code == 201

    skipped = close(api, scene, "2025-05")
    again = close(api, scene, MARCH)
    april = close(api, scene, "2025-04")

    for response in (skipped, again):
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == "closing_out_of_sequence"
        assert body["detail"] == "The next month to close is 2025-04"
    assert april.status_code == 201
    assert april.json()["opening_balance_cents"] == 12150  # the closing of March carries over
    assert april.json()["closing_balance_cents"] == 12850


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"period": "2025-3"},
        {"period": "2025-13"},
        {"period": "2025-03-01"},
        {"period": "March 2025"},
        {"period": "2025-03", "school_id": str(uuid.uuid4())},
        {"period": "2025-03", "closed_by_user_id": str(uuid.uuid4())},
        {"period": "2025-03", "closing_balance_cents": 1},
        {"period": "2025-03", "bank_balance_reported_cents": 10**12 + 1},
        {"period": "2025-03", "bank_balance_reported_cents": "a lot"},
        {"period": "2025-03", "bank_balance_reported_cents": 1.5},
    ],
    ids=str,
)
def test_a_bad_body_is_a_422_and_nothing_is_closed(scene: Scene, login: Login, body: Any) -> None:
    api = login(scene.users["treasurer"])

    response = api.post(closings(scene.fresh.school), body)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    assert api.get(closings(scene.fresh.school)).json()["items"] == []


def test_the_bank_balance_at_the_limit_is_accepted(scene: Scene, login: Login) -> None:
    response = close(login(scene.users["treasurer"]), scene, bank_balance_reported_cents=10**12)

    assert response.status_code == 201
    assert response.json()["bank_difference_cents"] == 10**12 - 12150


def test_the_refusals_never_carry_the_text_of_postgres(scene: Scene, login: Login) -> None:
    api = login(scene.users["treasurer"])
    close(api, scene)
    bodies = [
        close(api, scene, "2099-12").text,
        close(api, scene, "2025-06").text,
        api.post(
            closings(scene.fresh.school),
            {"period": "2025-04", "bank_balance_reported_cents": 10**13},
        ).text,
    ]

    for body in bodies:
        lowered = body.lower()
        for leak in (
            "sequential",
            "violat",
            "constraint",
            "monthly_closings",
            "check_",
            "sqlstate",
            "trigger",
        ):
            assert leak not in lowered, (leak, body)


@pytest.mark.parametrize(
    ("role", "status"),
    [("admin", 201), ("school_admin", 201), ("treasurer", 201), ("viewer", 403), ("staff", 403)],
)
def test_who_may_close_a_month(scene: Scene, login: Login, role: str, status: int) -> None:
    response = close(login(scene.users[role]), scene)

    assert response.status_code == status, response.text


def test_closing_needs_the_csrf_token_and_the_origin_of_the_site(
    scene: Scene, login: Login
) -> None:
    api = login(scene.users["treasurer"])
    path = closings(scene.fresh.school)

    no_csrf = api.post(path, {"period": MARCH}, csrf=False)
    forged_csrf = api.post(path, {"period": MARCH}, csrf="forged")
    foreign = api.post(path, {"period": MARCH}, origin="https://evil.example")

    assert (no_csrf.status_code, no_csrf.json()["code"]) == (403, "csrf_failed")
    assert (forged_csrf.status_code, forged_csrf.json()["code"]) == (403, "csrf_failed")
    assert (foreign.status_code, foreign.json()["code"]) == (403, "origin_not_allowed")
    assert api.get(path).json()["items"] == []


# --- reading ----------------------------------------------------------------------------------------


def test_list_and_get_repeat_the_closing(scene: Scene, login: Login) -> None:
    treasurer = login(scene.users["treasurer"])
    created = close(treasurer, scene, bank_balance_reported_cents=12150).json()
    school = scene.fresh.school

    listed = treasurer.get(closings(school)).json()
    one = treasurer.get(closings(school, f"/{created['id']}")).json()

    assert listed["next_cursor"] is None
    [row] = listed["items"]
    assert "breakdown" not in row  # the list is light: the breakdown is in the detail
    assert row["entries_hash"] == created["entries_hash"] == one["entries_hash"]
    assert one == created
    assert one["bank_difference_cents"] == 0


@pytest.mark.parametrize(
    ("role", "status"),
    [("admin", 200), ("school_admin", 200), ("treasurer", 200), ("viewer", 200), ("staff", 403)],
)
def test_who_may_read_the_closings(scene: Scene, login: Login, role: str, status: int) -> None:
    created = close(login(scene.users["treasurer"]), scene).json()
    api = login(scene.users[role])

    assert api.get(closings(scene.fresh.school)).status_code == status
    assert api.get(closings(scene.fresh.school, f"/{created['id']}")).status_code == status


def test_the_viewer_reads_the_figures_but_not_who_closed_the_month(
    scene: Scene, login: Login
) -> None:
    created = close(login(scene.users["treasurer"]), scene).json()

    seen = (
        login(scene.users["viewer"]).get(closings(scene.fresh.school, f"/{created['id']}")).json()
    )

    assert seen["closing_balance_cents"] == 12150
    assert seen["breakdown"] == created["breakdown"]
    assert seen["closed_by_user_id"] is None
    assert seen["reopened_by_user_id"] is None and seen["reopen_reason"] is None


def test_the_list_is_newest_first_and_paginated_by_cursor(scene: Scene, login: Login) -> None:
    api = login(scene.users["treasurer"])
    march = close(api, scene).json()
    april = close(api, scene, "2025-04").json()
    may = close(api, scene, "2025-05").json()
    school = scene.fresh.school

    everything = api.get(closings(school)).json()["items"]
    first = api.get(closings(school), params={"limit": 2}).json()
    second = api.get(closings(school), params={"limit": 2, "cursor": first["next_cursor"]}).json()

    assert [row["id"] for row in everything] == [may["id"], april["id"], march["id"]]
    assert [row["period"] for row in everything] == ["2025-05", "2025-04", "2025-03"]
    assert [row["id"] for row in first["items"]] == [may["id"], april["id"]]
    assert [row["id"] for row in second["items"]] == [march["id"]]
    assert second["next_cursor"] is None
    assert api.get(closings(school), params={"cursor": "garbage"}).status_code == 422
    assert api.get(closings(school), params={"limit": 0}).status_code == 422


def test_an_unknown_closing_is_a_404(scene: Scene, login: Login) -> None:
    api = login(scene.users["treasurer"])
    path = closings(scene.fresh.school, f"/{uuid.uuid4()}")

    assert api.get(path).status_code == 404
    assert api.post(f"{path}/verify").status_code == 404
    assert api.get(closings(scene.fresh.school, "/not-a-uuid")).status_code == 422


# --- verifying ---------------------------------------------------------------------------------------


def test_verify_says_true_for_an_intact_closing(scene: Scene, login: Login) -> None:
    api = login(scene.users["treasurer"])
    created = close(api, scene).json()

    response = api.post(closings(scene.fresh.school, f"/{created['id']}/verify"))

    assert response.status_code == 200, response.text
    assert response.json() == {
        "closing_id": created["id"],
        "verified": True,
        "entries_hash": created["entries_hash"],
    }


def test_verify_says_false_when_the_ledger_no_longer_matches_the_snapshot(
    scene: Scene, login: Login, admin_engine: Engine
) -> None:
    api = login(scene.users["treasurer"])
    created = close(api, scene).json()
    with admin_engine.begin() as conn:  # only someone who can switch the triggers off could do this
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(
            text("UPDATE financial_transactions SET amount_cents = amount_cents + 1 WHERE id = :t"),
            {"t": scene.entries["maria"]},
        )

    body = api.post(closings(scene.fresh.school, f"/{created['id']}/verify")).json()

    assert body["verified"] is False


@pytest.mark.parametrize(
    ("role", "status"),
    [("admin", 200), ("school_admin", 200), ("treasurer", 200), ("viewer", 403), ("staff", 403)],
)
def test_who_may_verify(scene: Scene, login: Login, role: str, status: int) -> None:
    created = close(login(scene.users["treasurer"]), scene).json()

    response = login(scene.users[role]).post(
        closings(scene.fresh.school, f"/{created['id']}/verify")
    )

    assert response.status_code == status, response.text


# --- reopening ---------------------------------------------------------------------------------------

REASON = "the bank statement of March was corrected"


def reopen(api: Api, scene: Scene, closing_id: str, reason: Any = REASON) -> Any:
    return api.post(closings(scene.fresh.school, f"/{closing_id}/reopen"), {"reason": reason})


def test_an_organization_admin_reopens_the_latest_closing_and_the_month_can_be_closed_again(
    scene: Scene, login: Login
) -> None:
    treasurer, admin = login(scene.users["treasurer"]), login(scene.users["admin"])
    created = close(treasurer, scene).json()

    response = reopen(admin, scene, created["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reopened_at"] is not None
    assert body["reopen_reason"] == REASON
    assert body["reopened_by_user_id"] == str(scene.users["admin"].id)
    assert body["closing_balance_cents"] == created["closing_balance_cents"]  # the snapshot stays
    verify = treasurer.post(closings(scene.fresh.school, f"/{created['id']}/verify"))
    assert (verify.status_code, verify.json()["code"]) == (409, "closing_reopened")
    again = close(treasurer, scene)
    assert again.status_code == 201
    assert again.json()["id"] != created["id"]
    history = treasurer.get(closings(scene.fresh.school)).json()["items"]
    assert [row["id"] for row in history] == [again.json()["id"], created["id"]]
    active = treasurer.get(closings(scene.fresh.school), params={"include_reopened": False}).json()
    assert [row["id"] for row in active["items"]] == [again.json()["id"]]


@pytest.mark.parametrize("role", ["school_admin", "treasurer", "viewer", "staff"])
def test_only_an_organization_admin_may_reopen(scene: Scene, login: Login, role: str) -> None:
    created = close(login(scene.users["treasurer"]), scene).json()

    response = reopen(login(scene.users[role]), scene, created["id"])

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "permission_denied"


@pytest.mark.parametrize(
    "reason",
    [None, "", "short", "         x         ", "nine char", "x" * 501, 12345678901, {"a": "b"}],
    ids=str,
)
def test_the_reason_needs_at_least_ten_characters(scene: Scene, login: Login, reason: Any) -> None:
    created = close(login(scene.users["treasurer"]), scene).json()
    admin = login(scene.users["admin"])
    path = closings(scene.fresh.school, f"/{created['id']}/reopen")

    response = admin.post(path, {} if reason is None else {"reason": reason})

    assert response.status_code == 422, response.text
    assert (
        admin.get(closings(scene.fresh.school, f"/{created['id']}")).json()["reopened_at"] is None
    )


def test_the_reason_is_trimmed_and_the_body_takes_nothing_else(scene: Scene, login: Login) -> None:
    created = close(login(scene.users["treasurer"]), scene).json()
    admin = login(scene.users["admin"])
    path = closings(scene.fresh.school, f"/{created['id']}/reopen")

    extra = admin.post(path, {"reason": REASON, "reopened_by_user_id": str(uuid.uuid4())})
    done = admin.post(path, {"reason": f"   {REASON}   "})

    assert extra.status_code == 422
    assert done.status_code == 200 and done.json()["reopen_reason"] == REASON


def test_only_the_latest_closing_can_be_reopened_and_only_once(scene: Scene, login: Login) -> None:
    treasurer, admin = login(scene.users["treasurer"]), login(scene.users["admin"])
    march = close(treasurer, scene).json()
    april = close(treasurer, scene, "2025-04").json()

    not_latest = reopen(admin, scene, march["id"])
    first = reopen(admin, scene, april["id"])
    second = reopen(admin, scene, april["id"])
    now_latest = reopen(admin, scene, march["id"])

    assert (not_latest.status_code, not_latest.json()["code"]) == (409, "closing_not_latest")
    assert first.status_code == 200
    assert (second.status_code, second.json()["code"]) == (409, "closing_already_reopened")
    assert now_latest.status_code == 200  # with April out of the way, March is the latest


def test_reopening_an_unknown_closing_is_a_404(scene: Scene, login: Login) -> None:
    response = reopen(login(scene.users["admin"]), scene, str(uuid.uuid4()))

    assert response.status_code == 404


# --- isolation, the audit --------------------------------------------------------------------------


def test_the_closings_of_another_organization_are_neither_readable_nor_writable(
    scene: Scene, login: Login, admin_engine: Engine
) -> None:
    theirs = close_other(login, scene)
    mine = login(scene.users["treasurer"])
    unknown = uuid.uuid4()

    answers = [
        mine.get(closings(scene.fresh.school, f"/{theirs}")),  # their closing under my school
        mine.get(closings(scene.fresh.school, f"/{unknown}")),  # a closing that does not exist
        mine.get(closings(scene.other.school, f"/{theirs}")),  # their school
        mine.get(closings(scene.other.school)),
        mine.post(closings(scene.other.school), {"period": MARCH}),
        mine.post(closings(scene.fresh.school, f"/{theirs}/verify")),
        reopen(login(scene.users["admin"]), scene, str(theirs)),
        reopen_theirs(login(scene.users["admin"]), scene, theirs),
    ]

    for response in answers:
        assert response.status_code == 404, response.text
        body = response.json()
        body.pop("request_id")
        assert body["code"] == "not_found"
    with admin_engine.connect() as conn:
        count: int = conn.execute(
            text("SELECT count(*) FROM monthly_closings WHERE school_id = :s"),
            {"s": scene.other.school},
        ).scalar_one()
    assert count == 1  # theirs, untouched


def close_other(login: Login, scene: Scene) -> uuid.UUID:
    api = login(scene.other_users["treasurer"])
    response = api.post(closings(scene.other.school), {"period": MARCH})
    assert response.status_code == 201, response.text
    assert response.json()["closing_balance_cents"] == 99900
    return uuid.UUID(response.json()["id"])


def reopen_theirs(api: Api, scene: Scene, closing: uuid.UUID) -> Any:
    return api.post(closings(scene.other.school, f"/{closing}/reopen"), {"reason": REASON})


def test_a_closing_of_the_sibling_school_is_not_reachable_from_a_school_membership(
    scene: Scene, login: Login
) -> None:
    admin = login(scene.users["admin"])  # organization-wide
    on_sibling = admin.post(closings(scene.sibling_school), {"period": MARCH})
    assert on_sibling.status_code == 201, on_sibling.text
    assert on_sibling.json()["school_id"] == str(scene.sibling_school)
    assert on_sibling.json()["closing_balance_cents"] == 0

    treasurer = login(scene.users["treasurer"])  # the school of the scene only

    assert treasurer.get(closings(scene.sibling_school)).status_code == 404
    assert (
        treasurer.get(closings(scene.fresh.school, f"/{on_sibling.json()['id']}")).status_code
        == 404
    )


def test_closing_and_reopening_are_audited_with_the_person_who_did_it(
    scene: Scene, login: Login, admin_engine: Engine
) -> None:
    created = close(login(scene.users["treasurer"]), scene).json()
    reopen(login(scene.users["admin"]), scene, created["id"])

    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT action, actor_user_id, actor_type, after_data FROM audit_logs "
                "WHERE entity_type = 'monthly_closings' AND entity_id = :c ORDER BY occurred_at, id"
            ),
            {"c": created["id"]},
        ).all()

    assert [row.action for row in rows] == ["monthly_closings.insert", "monthly_closings.update"]
    assert [row.actor_user_id for row in rows] == [
        scene.users["treasurer"].id,
        scene.users["admin"].id,
    ]
    assert {row.actor_type for row in rows} == {"USER"}
    assert rows[1].after_data["reopen_reason_present"] is True
    assert REASON not in str(rows[1].after_data)  # free text never reaches the audit log


# --- the extra read of the sequence refusal --------------------------------------------------------


def test_the_next_period_is_looked_up_only_for_the_sequence_refusal(
    scene: Scene, login: Login, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[uuid.UUID] = []
    real = queries.next_period_to_close

    def spy(db: Any, school_id: uuid.UUID) -> Any:
        calls.append(school_id)
        return real(db, school_id)

    monkeypatch.setattr("app.closing.routes.queries.next_period_to_close", spy)
    api = login(scene.users["treasurer"])

    not_ended = close(api, scene, "2099-12")  # refused for another reason
    ok = close(api, scene)
    skipped = close(api, scene, "2025-05")  # the sequence refusal

    assert not_ended.json()["code"] == "period_not_ended" and ok.status_code == 201
    assert skipped.json()["code"] == "closing_out_of_sequence"
    assert skipped.json()["detail"] == "The next month to close is 2025-04"
    assert calls == [scene.fresh.school]  # once, for the sequence refusal only


def test_a_failing_extra_read_never_masks_the_refusal(
    scene: Scene, login: Login, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(db: Any, school_id: uuid.UUID) -> Any:
        db.execute(text("SELECT * FROM a_table_that_does_not_exist"))

    monkeypatch.setattr("app.closing.routes.queries.next_period_to_close", broken)
    api = login(scene.users["treasurer"])
    assert close(api, scene).status_code == 201

    response = close(api, scene, "2025-05")

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "closing_out_of_sequence"
    assert "detail" not in body  # the month could not be looked up; the refusal still is the answer
    assert "a_table_that_does_not_exist" not in response.text
