"""The webhook of the Pix provider: authenticated by a secret in a header, idempotent by event id,
and never believed without asking the provider again."""

import hashlib
import uuid

from httpx2 import Response
from sqlalchemy import Engine, text

from app.pix.provider import sandbox_provider
from tests.authsupport import Api, ApiFactory
from tests.public.conftest import WEBHOOK_SECRET, PublicSchool
from tests.public.support import contribute, row, txid_of

URL = "/api/v1/webhooks/pix/sandbox"


def notify(
    api: Api, txid: str, *, secret: str | None = WEBHOOK_SECRET, event: str | None = None
) -> Response:
    headers = {} if secret is None else {"X-Webhook-Secret": secret}
    return api.post(
        URL,
        {"event_id": event or uuid.uuid4().hex, "txid": txid},
        origin=False,  # a server calls it: there is no page and no Origin
        csrf=False,
        headers=headers,
    )


def _charge(admin_engine: Engine, school_id: uuid.UUID) -> tuple[str, str]:
    found = row(
        admin_engine,
        "SELECT p.status, f.status FROM pix_charges p JOIN financial_transactions f "
        "ON f.id = p.transaction_id WHERE p.school_id = :s",
        s=school_id,
    )
    return found[0], found[1]


def test_a_confirmed_payment_settles_the_contribution(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    txid = txid_of(contribute(api, school.slug, 3000))
    sandbox_provider().simulate_payment(txid)
    response = notify(api, txid)
    assert response.status_code == 200 and response.json() == {"status": "PAID"}
    assert _charge(admin_engine, school.fresh.school) == ("PAID", "PAID")
    event = row(
        admin_engine,
        "SELECT signature_valid, processed_at IS NOT NULL, attempts, processing_error "
        "FROM webhook_events WHERE school_id = :s",
        s=school.fresh.school,
    )
    assert tuple(event) == (True, True, 1, None)


def test_the_same_event_delivered_twice_is_one_payment(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    txid = txid_of(contribute(api, school.slug, 3000))
    sandbox_provider().simulate_payment(txid)
    event = uuid.uuid4().hex
    assert notify(api, txid, event=event).json() == {"status": "PAID"}
    assert notify(api, txid, event=event).json() == {"status": "DUPLICATE"}
    assert notify(api, txid, event=event).json() == {"status": "DUPLICATE"}
    counts = row(
        admin_engine,
        "SELECT (SELECT count(*) FROM webhook_events WHERE school_id = :s), "
        "(SELECT count(*) FROM financial_transactions WHERE school_id = :s AND status = 'PAID')",
        s=school.fresh.school,
    )
    assert tuple(counts) == (1, 1)  # one event, one settled contribution


def test_another_event_for_a_charge_that_is_no_longer_pending_changes_nothing(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    txid = txid_of(contribute(api, school.slug, 3000))
    sandbox_provider().simulate_payment(txid)
    assert notify(api, txid).json() == {"status": "PAID"}
    assert notify(api, txid).json() == {"status": "IGNORED"}
    assert _charge(admin_engine, school.fresh.school) == ("PAID", "PAID")


def test_a_notification_the_provider_does_not_confirm_changes_nothing(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    txid = txid_of(contribute(api, school.slug, 3000))  # nobody paid at the provider
    assert notify(api, txid).json() == {"status": "NOT_CONFIRMED"}
    assert _charge(admin_engine, school.fresh.school) == ("PENDING", "PENDING_PAYMENT")
    note = row(
        admin_engine,
        "SELECT processing_error FROM webhook_events WHERE school_id = :s",
        s=school.fresh.school,
    )
    assert note[0]


def test_a_different_amount_goes_to_review(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    txid = txid_of(contribute(api, school.slug, 3000))
    sandbox_provider().simulate_payment(txid, 99900)
    assert notify(api, txid).json() == {"status": "REVIEW_REQUIRED"}
    assert _charge(admin_engine, school.fresh.school) == ("REVIEW_REQUIRED", "REVIEW_REQUIRED")


def test_an_unknown_txid_is_ignored(apis: ApiFactory, school: PublicSchool) -> None:
    assert notify(apis.make(), "q" * 32).json() == {"status": "IGNORED"}


def test_without_the_secret_or_with_a_wrong_one_nothing_happens(
    apis: ApiFactory, school: PublicSchool, admin_engine: Engine
) -> None:
    api = apis.make()
    txid = txid_of(contribute(api, school.slug, 3000))
    sandbox_provider().simulate_payment(txid)
    for secret in (None, "", "wrong-secret", "x" * 300):
        response = notify(api, txid, secret=secret)
        assert response.status_code == 401, secret
        assert response.json()["code"] == "webhook_unauthorized"
    assert _charge(admin_engine, school.fresh.school) == ("PENDING", "PENDING_PAYMENT")
    events = row(admin_engine, "SELECT count(*) FROM webhook_events WHERE school_id = :s",
                 s=school.fresh.school)  # fmt: skip
    assert events[0] == 0  # nothing was recorded for an unauthenticated call


def test_the_secret_of_another_school_cannot_confirm_this_school(
    apis: ApiFactory, school: PublicSchool, other_school: PublicSchool, admin_engine: Engine
) -> None:
    other_secret = "the-secret-of-the-other-school-0123456789"  # noqa: S105  (fake, tests only)
    with admin_engine.begin() as conn:
        conn.execute(
            text("UPDATE payment_accounts SET webhook_secret_hash = :h WHERE id = :a"),
            {
                "h": hashlib.sha256(other_secret.encode()).hexdigest(),
                "a": other_school.fresh.account,
            },
        )
    api = apis.make()
    txid = txid_of(contribute(api, school.slug, 3000))
    sandbox_provider().simulate_payment(txid)
    # The secret of the other school resolves to the OTHER school, where this charge does not exist.
    assert notify(api, txid, secret=other_secret).json() == {"status": "IGNORED"}
    assert _charge(admin_engine, school.fresh.school) == ("PENDING", "PENDING_PAYMENT")


def test_a_malformed_body_is_refused_after_authentication(
    apis: ApiFactory, school: PublicSchool
) -> None:
    api = apis.make()
    headers = {"X-Webhook-Secret": WEBHOOK_SECRET}
    for body in ({}, {"event_id": "", "txid": "a" * 32}, {"event_id": "e", "txid": "short"}):
        assert api.post(URL, body, origin=False, csrf=False, headers=headers).status_code == 422


def test_guessing_secrets_is_limited_by_address(apis: ApiFactory, school: PublicSchool) -> None:
    api = apis.make()
    statuses = [notify(api, "t" * 32, secret=f"guess-{n}").status_code for n in range(7)]
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
