"""The public contribution flow end to end: the page of a school, the contribution and its Pix
charge, the polling, the payment (the sandbox), a different amount, the receipt and the limits."""

import uuid

import pytest
from sqlalchemy import Engine, text

from app.public.service import FIELD_NAMES
from tests.authsupport import ApiFactory
from tests.public.conftest import PublicSchool
from tests.public.support import charge_url, contribute, row, txid_of


def _pay(api, txid: str, amount: int | None = None):  # type: ignore[no-untyped-def]
    body = None if amount is None else {"amount_cents": amount}
    return api.post(f"/api/v1/dev/sandbox/pix/{txid}/pay", body, csrf=False)


# --- the page of a school ---


def test_the_page_of_a_school_has_what_the_form_needs_and_no_internal_id(
    apis: ApiFactory, school: PublicSchool
) -> None:
    api = apis.make()
    response = api.get(f"/api/v1/public/schools/{school.slug}")
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == school.slug and body["name"] == school.name
    assert body["apm_name"] == f"APM da {school.name}"
    assert body["suggested_amounts_cents"] == [2000, 3000, 4000]
    assert body["allow_custom_amount"] is True
    assert body["min_amount_cents"] == 1000 and body["max_amount_cents"] == 500000
    assert body["identification"] == {
        "guardian_name": "OPTIONAL",
        "student_name": "HIDDEN",
        "class_name": "HIDDEN",
        "contributor_email": "OPTIONAL",
        "contributor_phone": "OPTIONAL",
    }
    text_ = response.text
    for internal in (str(school.fresh.school), str(school.fresh.org)):
        assert internal not in text_  # the ids of the school and of its network stay on the server
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("slug", ["no-such-school", "UPPER", "a" * 100, "bad_slug!", "t5-sch-"])
def test_an_unknown_or_malformed_slug_is_the_same_404(apis: ApiFactory, slug: str) -> None:
    response = apis.make().get(f"/api/v1/public/schools/{slug}")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_a_school_without_an_active_payment_account_has_no_public_page(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            text("UPDATE payment_accounts SET status = 'INACTIVE' WHERE id = :a"),
            {"a": school.fresh.account},
        )
    assert apis.make().get(f"/api/v1/public/schools/{school.slug}").status_code == 404


# --- starting a contribution ---------------------------------------------------------------------


def test_a_contribution_is_created_with_its_charge_and_nothing_secret_is_stored(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    key = uuid.uuid4()
    response = contribute(
        api, school.slug, 3000, key=key, guardian_name=" Maria Exemplo ", student_name="Davi"
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["token"]) == 43
    assert body["status"] == "PENDING_PAYMENT" and body["amount_cents"] == 3000
    assert body["charge"]["status"] == "PENDING" and body["charge"]["amount_cents"] == 3000
    assert body["charge"]["emv_payload"].startswith("PIX-SANDBOX:")
    assert response.headers["cache-control"] == "private, no-store"

    stored = row(
        admin_engine,
        "SELECT f.status, f.origin_name, c.method, c.guardian_name, c.student_name, "
        "c.receipt_token_hash, c.idempotency_key, "
        "c.receipt_expires_at > now() + interval '29 days' "
        "FROM financial_transactions f JOIN contributions c ON c.transaction_id = f.id "
        "WHERE c.school_id = :s",
        s=school.fresh.school,
    )
    assert stored[0] == "PENDING_PAYMENT" and stored[1] is None  # the name lives only in the detail
    assert stored[2] == "PIX"
    assert stored[3] == "Maria Exemplo"  # trimmed
    assert stored[4] is None  # the school does not ask for the student: it is not stored
    assert stored[5] != body["token"] and len(stored[5]) == 64  # only the hash
    assert stored[6] == key and stored[7] is True


def test_the_same_idempotency_key_answers_with_the_same_contribution(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    key = uuid.uuid4()
    first = contribute(api, school.slug, 3000, key=key).json()
    again = contribute(api, school.slug, 3000, key=key).json()
    assert again["token"] == first["token"]
    assert again["charge"]["emv_payload"] == first["charge"]["emv_payload"]
    count = row(
        admin_engine,
        "SELECT count(*) FROM contributions WHERE school_id = :s",
        s=school.fresh.school,
    )
    assert count[0] == 1
    other = contribute(api, school.slug, 3000, key=uuid.uuid4()).json()
    assert other["token"] != first["token"]


def test_the_same_key_with_another_amount_is_a_conflict(
    apis: ApiFactory, school: PublicSchool
) -> None:
    api = apis.make()
    key = uuid.uuid4()
    assert contribute(api, school.slug, 3000, key=key).status_code == 201
    response = contribute(api, school.slug, 4000, key=key)
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_reused"


@pytest.mark.parametrize(
    ("amount", "field", "code"),
    [(999, "amount_cents", "out_of_range"), (500001, "amount_cents", "out_of_range")],
)
def test_the_amount_is_revalidated_against_the_school(
    apis: ApiFactory, school: PublicSchool, amount: int, field: str, code: str
) -> None:
    response = contribute(apis.make(), school.slug, amount)
    assert response.status_code == 422
    assert {"field": field, "code": code} in response.json()["errors"]


def test_only_the_suggested_amounts_when_the_school_does_not_allow_another(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            text("UPDATE school_settings SET allow_custom_amount = false WHERE school_id = :s"),
            {"s": school.fresh.school},
        )
    api = apis.make()
    assert contribute(api, school.slug, 3000).status_code == 201
    response = contribute(api, school.slug, 3500)
    assert response.status_code == 422
    assert {"field": "amount_cents", "code": "not_suggested"} in response.json()["errors"]


def test_a_required_field_is_required_and_an_unasked_one_is_ignored(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE school_settings SET required_fields = '{guardian_name,student_name}', "
                "optional_fields = '{}' WHERE school_id = :s"
            ),
            {"s": school.fresh.school},
        )
    api = apis.make()
    response = contribute(api, school.slug, 3000, guardian_name="Maria")
    assert response.status_code == 422
    assert {"field": "student_name", "code": "required"} in response.json()["errors"]
    ok = contribute(
        api, school.slug, 3000, guardian_name="Maria", student_name="Davi",
        contributor_email="maria@example.test",
    )  # fmt: skip
    assert ok.status_code == 201
    stored = row(
        admin_engine,
        "SELECT student_name, contributor_email FROM contributions WHERE school_id = :s",
        s=school.fresh.school,
    )
    assert stored[0] == "Davi" and stored[1] is None  # the e-mail is not asked: never stored


@pytest.mark.parametrize(
    "fields",
    [
        {"contributor_email": "not-an-email"},
        {"contributor_phone": "call me maybe"},
        {"guardian_name": "x" * 121},
        {"unknown_field": "x"},
    ],
)
def test_malformed_fields_are_refused(
    apis: ApiFactory, school: PublicSchool, fields: dict[str, str]
) -> None:
    assert contribute(apis.make(), school.slug, 3000, **fields).status_code == 422


def test_the_idempotency_key_is_required(apis: ApiFactory, school: PublicSchool) -> None:
    for key in ("", "not-a-uuid"):
        response = contribute(apis.make(), school.slug, 3000, key=key)
        assert response.status_code == 422, key
    assert set(FIELD_NAMES) >= {"guardian_name"}


def test_a_post_from_another_site_is_refused(apis: ApiFactory, school: PublicSchool) -> None:
    api = apis.make()
    response = api.post(
        f"/api/v1/public/schools/{school.slug}/contributions",
        {"amount_cents": 3000},
        origin="https://evil.example",
        csrf=False,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_too_many_new_contributions_from_one_address_are_limited(
    apis: ApiFactory, school: PublicSchool
) -> None:
    api = apis.make()
    statuses = [contribute(api, school.slug, 3000).status_code for _ in range(31)]
    assert statuses[:30] == [201] * 30
    assert statuses[30] == 429


# --- the token ---


def test_the_token_opens_the_state_and_nothing_else_does(
    apis: ApiFactory, school: PublicSchool, other_school: PublicSchool
) -> None:
    api = apis.make()
    created = contribute(api, school.slug).json()
    token = created["token"]
    opened = api.get(charge_url(school.slug, token))
    assert opened.status_code == 200
    assert opened.json()["status"] == "PENDING_PAYMENT"
    assert opened.json()["charge"]["status"] == "PENDING"
    assert opened.headers["cache-control"] == "private, no-store"
    # The same answer for an unknown token, a malformed one and a token of another school.
    answers = [
        api.get(charge_url(school.slug, "A" * 43)),
        api.get(charge_url(school.slug, "short")),
        api.get(charge_url(other_school.slug, token)),
    ]
    assert [a.status_code for a in answers] == [404, 404, 404]
    assert {a.json()["code"] for a in answers} == {"not_found"}
    assert len({a.text.replace(a.json()["request_id"], "") for a in answers}) == 1


def test_guessing_tokens_is_limited_by_address(apis: ApiFactory, school: PublicSchool) -> None:
    api = apis.make()
    statuses = [api.get(charge_url(school.slug, "B" * 43)).status_code for _ in range(7)]
    assert statuses[:5] == [404] * 5
    assert statuses[5] == 429
    assert statuses[6] == 429


# --- the payment ---


def test_a_paid_contribution_confirms_by_the_provider_and_has_a_receipt(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    created = contribute(api, school.slug, 3000)
    token, txid = created.json()["token"], txid_of(created)
    waiting = api.get(charge_url(school.slug, token, "receipt"))
    assert waiting.status_code == 409 and waiting.json()["code"] == "payment_not_confirmed"

    paid = _pay(api, txid)
    assert paid.status_code == 200 and paid.json() == {"status": "PAID"}

    state = api.get(charge_url(school.slug, token)).json()
    assert state["status"] == "PAID" and state["charge"]["status"] == "PAID"
    receipt = api.get(charge_url(school.slug, token, "receipt"))
    assert receipt.status_code == 200, receipt.text
    body = receipt.json()
    assert body["status"] == "PAID" and body["amount_cents"] == 3000
    assert body["school_name"] == school.name and body["method"] == "PIX"
    assert len(body["reference_code"]) == 10 and body["reference_code"].startswith("APM-")
    assert "guardian" not in receipt.text  # the receipt does not echo personal data

    settled = row(
        admin_engine,
        "SELECT f.status, f.settled_at IS NOT NULL, p.status, p.received_amount_cents, "
        "p.end_to_end_id ~ '^E[A-Za-z0-9]{31}$' FROM financial_transactions f "
        "JOIN pix_charges p ON p.transaction_id = f.id WHERE f.school_id = :s",
        s=school.fresh.school,
    )
    assert tuple(settled) == ("PAID", True, "PAID", 3000, True)
    audited = row(
        admin_engine,
        "SELECT count(*) FROM audit_logs WHERE school_id = :s AND actor_type = 'SYSTEM' "
        "AND entity_type = 'financial_transactions'",
        s=school.fresh.school,
    )
    assert audited[0] >= 1  # the confirmation is audited, with the actor SYSTEM


def test_a_different_amount_goes_to_review_and_is_not_a_normal_contribution(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    created = contribute(api, school.slug, 3000)
    token = created.json()["token"]
    assert _pay(api, txid_of(created), 30000).json() == {"status": "REVIEW_REQUIRED"}
    state = api.get(charge_url(school.slug, token)).json()
    assert state["status"] == "REVIEW_REQUIRED"
    assert state["charge"]["status"] == "REVIEW_REQUIRED"
    assert api.get(charge_url(school.slug, token, "receipt")).status_code == 409
    stored = row(
        admin_engine,
        "SELECT f.amount_cents, f.settled_at, p.received_amount_cents, p.divergence_reason "
        "FROM financial_transactions f JOIN pix_charges p ON p.transaction_id = f.id "
        "WHERE f.school_id = :s",
        s=school.fresh.school,
    )
    assert stored[0] == 3000 and stored[1] is None  # not settled, not in the cash
    assert stored[2] == 30000 and stored[3]


def test_the_dev_pay_route_knows_only_real_charges(apis: ApiFactory, school: PublicSchool) -> None:
    api = apis.make()
    assert _pay(api, "z" * 32).status_code == 404


# --- a new charge ---


def _expire(admin_engine: Engine, school_id: uuid.UUID) -> None:
    with admin_engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(
            text(
                "UPDATE pix_charges SET created_at = now() - interval '2 hours', "
                "expires_at = now() - interval '1 hour' WHERE school_id = :s"
            ),
            {"s": school_id},
        )


def test_an_expired_charge_is_replaced_once_and_only_once(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    created = contribute(api, school.slug, 3000)
    token = created.json()["token"]
    # While it is pending there is no second charge.
    same = api.post(charge_url(school.slug, token, "charges"), None, csrf=False)
    assert same.status_code == 200
    assert same.json()["charge"]["emv_payload"] == created.json()["charge"]["emv_payload"]

    _expire(admin_engine, school.fresh.school)
    assert api.get(charge_url(school.slug, token)).json()["charge"]["status"] == "EXPIRED"
    renewed = api.post(charge_url(school.slug, token, "charges"), None, csrf=False)
    assert renewed.status_code == 200
    assert renewed.json()["charge"]["status"] == "PENDING"
    assert renewed.json()["charge"]["emv_payload"] != created.json()["charge"]["emv_payload"]
    again = api.post(charge_url(school.slug, token, "charges"), None, csrf=False)
    assert again.json()["charge"]["emv_payload"] == renewed.json()["charge"]["emv_payload"]
    count = row(
        admin_engine,
        "SELECT count(*) FROM pix_charges WHERE school_id = :s",
        s=school.fresh.school,
    )
    assert count[0] == 2

    # The new charge can be paid.
    assert _pay(api, txid_of(renewed)).json() == {"status": "PAID"}


def test_a_paid_contribution_does_not_get_a_new_charge(
    apis: ApiFactory, school: PublicSchool
) -> None:
    api = apis.make()
    created = contribute(api, school.slug)
    _pay(api, txid_of(created))
    response = api.post(
        charge_url(school.slug, created.json()["token"], "charges"), None, csrf=False
    )
    assert response.status_code == 409
    assert response.json()["code"] == "contribution_closed"
