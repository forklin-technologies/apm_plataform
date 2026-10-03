"""A2/A3/A4: inviting people (who may invite whom, where) and accepting an invitation (the token is
the credential, one answer for every way it can be wrong, and it can be used once)."""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.auth.tokens import new_token
from tests.authsupport import (
    PASSWORD,
    Api,
    ApiFactory,
    TestUser,
    UserFactory,
    World,
    read_outbox,
)

INVITE = "/api/v1/invitations"
ACCEPT = "/api/v1/invitations/accept"


def new_email(world: World) -> str:
    return world.email("invitee")


def logged_in(apis: ApiFactory, user: TestUser) -> Api:
    api = apis.make()
    assert api.login(user).status_code == 200
    return api


def invite_for_token(
    apis: ApiFactory, users: UserFactory, tmp_path: Path, *, role: str = "staff", school: str = "1"
) -> tuple[str, str]:
    """An organization admin invites a new e-mail; returns (email, token read from the outbox)."""
    admin = logged_in(apis, users.make("organization_admin", school=None, label="admin"))
    email = new_email(users.world)
    school_id = users.world.school_a1 if school == "1" else users.world.school_a2
    response = admin.invite(email, role, school_id)
    assert response.status_code == 201, response.text
    message = read_outbox(tmp_path)[-1]
    assert message.kind == "invitation" and message.to == email
    return email, message.token


def invitation_row(engine: Engine, token: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT i.accepted_at, i.accepted_user_id, i.revoked_at, i.role, i.school_id, "
                "i.organization_id, i.email, i.token_hash, i.invited_by_user_id, i.expires_at, "
                "i.created_at FROM invitations i WHERE i.token_hash = :h"
            ),
            {"h": hashlib.sha256(token.encode()).digest()},
        ).one_or_none()


# --- creating an invitation --------------------------------------------------------------------


def test_an_invitation_is_created_for_the_active_tenant_and_the_token_goes_by_e_mail_only(
    apis: ApiFactory, users: UserFactory, tmp_path: Path, admin_engine: Engine
) -> None:
    admin_user = users.make("school_admin", school="1")
    admin = logged_in(apis, admin_user)
    email = new_email(users.world).upper()

    response = admin.invite(email, "staff")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "email", "role", "school_id", "expires_at"}
    assert body["email"] == email.lower() and body["role"] == "staff"
    assert body["school_id"] == str(users.world.school_a1)  # defaulted to the inviter's school
    message = read_outbox(tmp_path)[-1]
    token = message.token
    assert token not in response.text and "token" not in body
    row = invitation_row(admin_engine, token)
    assert row.organization_id == users.world.org_a and row.invited_by_user_id == admin_user.id
    assert bytes(row.token_hash) == hashlib.sha256(token.encode()).digest()
    assert row.accepted_at is None and row.revoked_at is None
    assert (row.expires_at - row.created_at).total_seconds() == pytest.approx(72 * 3600, abs=5)
    assert message.file_mode == 0o600
    assert f"/accept-invitation?token={token}" in message.body
    with admin_engine.connect() as connection:  # the token itself is stored nowhere
        leaked: Any = connection.execute(
            text("SELECT count(*) FROM invitations WHERE encode(token_hash, 'escape') = :t"),
            {"t": token},
        ).scalar_one()
    assert leaked == 0


@pytest.mark.parametrize(
    ("inviter", "role", "school", "status"),
    [
        ("organization_admin", "organization_admin", None, 201),
        ("organization_admin", "school_admin", "1", 201),
        ("organization_admin", "treasurer", "2", 201),
        ("organization_admin", "viewer", "1", 201),
        ("school_admin", "school_admin", "1", 201),
        ("school_admin", "treasurer", "1", 201),
        ("school_admin", "staff", "1", 201),
        ("school_admin", "viewer", None, 201),  # defaults to its own school
        ("school_admin", "organization_admin", "1", 403),  # never above its own level
        ("school_admin", "staff", "2", 403),  # never into another school
        ("treasurer", "staff", "1", 403),
        ("staff", "staff", "1", 403),
        ("viewer", "viewer", "1", 403),
    ],
)
def test_who_may_invite_whom(
    apis: ApiFactory,
    users: UserFactory,
    inviter: str,
    role: str,
    school: str | None,
    status: int,
) -> None:
    scope = None if inviter == "organization_admin" else "1"
    admin = logged_in(apis, users.make(inviter, school=scope))
    world = users.world
    school_id = None if school is None else (world.school_a1 if school == "1" else world.school_a2)

    response = admin.invite(new_email(world), role, school_id)

    assert response.status_code == status, response.text
    if status == 403:
        assert response.json()["code"] == "permission_denied"


def test_an_organization_admin_invitation_cannot_name_a_school(
    apis: ApiFactory, users: UserFactory
) -> None:
    admin = logged_in(apis, users.make("organization_admin", school=None))

    response = admin.invite(new_email(users.world), "organization_admin", users.world.school_a1)

    assert response.status_code == 422
    assert response.json()["errors"] == [
        {"field": "school_id", "code": "must_be_empty_for_organization_admin"}
    ]


def test_a_school_of_another_organization_does_not_exist_for_the_inviter(
    apis: ApiFactory, users: UserFactory
) -> None:
    admin_a = logged_in(apis, users.make("organization_admin", org="a", school=None))
    admin_b = logged_in(apis, users.make("organization_admin", org="b", school=None))
    world = users.world

    into_b = admin_a.invite(new_email(world), "staff", world.school_b1)
    into_a = admin_b.invite(new_email(world), "staff", world.school_a1)
    unknown = admin_a.invite(new_email(world), "staff", uuid.uuid4())

    for response in (into_b, into_a, unknown):
        assert response.status_code == 422
        assert response.json()["errors"] == [{"field": "school_id", "code": "not_found"}]


@pytest.mark.parametrize(
    "extra",
    [
        {"organization_id": str(uuid.uuid4())},
        {"invited_by_user_id": str(uuid.uuid4())},
        {"token": "x" * 43},
        {"expires_at": "2099-01-01T00:00:00Z"},
        {"accepted_at": "2020-01-01T00:00:00Z"},
    ],
)
def test_nothing_but_the_email_the_role_and_the_school_is_taken_from_the_body(
    apis: ApiFactory, users: UserFactory, extra: dict[str, str]
) -> None:
    admin = logged_in(apis, users.make("organization_admin", school=None))
    body = {
        "email": new_email(users.world),
        "role": "staff",
        "school_id": str(users.world.school_a1),
    }

    response = admin.post(INVITE, {**body, **extra})

    assert response.status_code == 422 and response.json()["code"] == "validation_error"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "nope", "role": "staff"},
        {"email": "a@b.test", "role": "root"},
        {"email": "a@b.test", "role": "ORGANIZATION_ADMIN"},
        {"email": "a@b.test", "role": ""},
        {"email": "a@b.test", "role": "staff", "school_id": "not-a-uuid"},
        {"email": "a" * 300 + "@b.test", "role": "staff"},
    ],
)
def test_a_malformed_invitation_is_a_422(
    apis: ApiFactory, users: UserFactory, body: dict[str, Any]
) -> None:
    admin = logged_in(apis, users.make("organization_admin", school=None))

    assert admin.post(INVITE, body).status_code == 422


def test_inviting_needs_a_session(api: Api) -> None:
    body = {"email": "a@b.test", "role": "staff"}

    assert api.invite("a@b.test", "staff").status_code == 401
    assert api.post(INVITE, body, csrf=False).status_code == 401


def test_a_second_pending_invitation_for_the_same_scope_is_a_conflict(
    apis: ApiFactory, users: UserFactory
) -> None:
    admin = logged_in(apis, users.make("organization_admin", school=None))
    email = new_email(users.world)
    assert admin.invite(email, "staff", users.world.school_a1).status_code == 201

    again = admin.invite(email, "viewer", users.world.school_a1)
    other_school = admin.invite(email, "staff", users.world.school_a2)

    assert again.status_code == 409 and again.json()["code"] == "invitation_pending"
    assert other_school.status_code == 201  # another scope is another invitation


def test_somebody_who_already_has_access_there_is_a_conflict(
    apis: ApiFactory, users: UserFactory
) -> None:
    admin = logged_in(apis, users.make("organization_admin", school=None))
    member = users.make("staff", school="1")

    response = admin.invite(member.email, "viewer", users.world.school_a1)
    elsewhere = admin.invite(member.email, "viewer", users.world.school_a2)

    assert response.status_code == 409 and response.json()["code"] == "already_member"
    assert elsewhere.status_code == 201


def test_an_account_of_another_organization_is_not_revealed_by_an_invitation(
    apis: ApiFactory, users: UserFactory
) -> None:
    """The conflict check only sees the inviter's own tenant, so inviting an e-mail that belongs
    to somebody in another organization answers exactly like inviting a brand new one."""
    admin_a = logged_in(apis, users.make("organization_admin", org="a", school=None))
    in_b = users.make("staff", org="b", school="1")

    response = admin_a.invite(in_b.email, "staff", users.world.school_a1)

    assert response.status_code == 201


def test_an_expired_invitation_does_not_block_a_new_one(
    apis: ApiFactory, users: UserFactory, admin_engine: Engine, tmp_path: Path
) -> None:
    admin = logged_in(apis, users.make("organization_admin", school=None))
    email = new_email(users.world)
    admin.invite(email, "staff", users.world.school_a1)
    first = read_outbox(tmp_path)[-1].token
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE invitations SET created_at = now() - interval '5 days', "
                "expires_at = now() - interval '2 days' WHERE token_hash = :h"
            ),
            {"h": hashlib.sha256(first.encode()).digest()},
        )

    again = admin.invite(email, "staff", users.world.school_a1)

    assert again.status_code == 201
    assert invitation_row(admin_engine, first).revoked_at is not None


# --- accepting an invitation ------------------------------------------------------------------


def test_a_new_person_accepts_gets_an_account_and_can_log_in(
    apis: ApiFactory, users: UserFactory, tmp_path: Path, admin_engine: Engine
) -> None:
    email, token = invite_for_token(apis, users, tmp_path, role="treasurer", school="2")
    newcomer = apis.make()

    response = newcomer.post(
        ACCEPT, {"token": token, "full_name": "  Maria Nova ", "password": PASSWORD}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["membership"]["role"] == "treasurer"
    assert body["membership"]["school"]["id"] == str(users.world.school_a2)
    assert "set-cookie" not in response.headers  # accepting does not log in
    with admin_engine.connect() as connection:
        user = connection.execute(
            text(
                "SELECT id, email, full_name, is_active, password_hash FROM users WHERE email = :e"
            ),
            {"e": email},
        ).one()
    assert str(user.id) == body["user_id"] and user.full_name == "Maria Nova" and user.is_active
    assert user.password_hash.startswith("$argon2id$") and PASSWORD not in user.password_hash
    row = invitation_row(admin_engine, token)
    assert row.accepted_at is not None and row.accepted_user_id == user.id
    login = newcomer.login(email, PASSWORD)
    assert login.status_code == 200
    assert login.json()["active_membership"]["role"] == "treasurer"


def test_an_invitation_can_be_used_once(
    apis: ApiFactory, users: UserFactory, tmp_path: Path
) -> None:
    _email, token = invite_for_token(apis, users, tmp_path)
    payload = {"token": token, "full_name": "First", "password": PASSWORD}
    assert apis.make().post(ACCEPT, payload).status_code == 201

    again = apis.make().post(ACCEPT, {**payload, "full_name": "Second"})

    assert again.status_code == 400 and again.json()["code"] == "invitation_invalid"


def test_two_people_racing_for_the_same_invitation_get_one_account(
    apis: ApiFactory, users: UserFactory, tmp_path: Path, admin_engine: Engine
) -> None:
    email, token = invite_for_token(apis, users, tmp_path)
    clients = [apis.make() for _ in range(4)]
    payload = {"token": token, "full_name": "Racer", "password": PASSWORD}

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda c: c.post(ACCEPT, payload).status_code, clients))

    assert sorted(statuses) == [201, 400, 400, 400]
    with admin_engine.connect() as connection:
        accounts: Any = connection.execute(
            text("SELECT count(*) FROM users WHERE email = :e"), {"e": email}
        ).scalar_one()
        memberships: Any = connection.execute(
            text(
                "SELECT count(*) FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE u.email = :e"
            ),
            {"e": email},
        ).scalar_one()
    assert accounts == 1 and memberships == 1


def test_every_way_an_invitation_can_be_wrong_gives_the_same_answer(
    apis: ApiFactory, users: UserFactory, tmp_path: Path, admin_engine: Engine
) -> None:
    _e1, expired = invite_for_token(apis, users, tmp_path)
    _e2, revoked = invite_for_token(apis, users, tmp_path)
    _e3, used = invite_for_token(apis, users, tmp_path)
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE invitations SET created_at = now() - interval '5 days', "
                "expires_at = now() - interval '2 days' WHERE token_hash = :h"
            ),
            {"h": hashlib.sha256(expired.encode()).digest()},
        )
        connection.execute(
            text("UPDATE invitations SET revoked_at = now() WHERE token_hash = :h"),
            {"h": hashlib.sha256(revoked.encode()).digest()},
        )
    assert (
        apis.make()
        .post(ACCEPT, {"token": used, "full_name": "U", "password": PASSWORD})
        .status_code
        == 201
    )

    candidates = {
        "expired": expired,
        "revoked": revoked,
        "used": used,
        "unknown": new_token(),
        "too-short": "abc",
        "empty": "",
        "weird": "../../etc/passwd",
        "sql": "' OR '1'='1",
        "long": "A" * 200,
        "unicode": "é" * 43,
    }

    answers = {
        label: apis.make().post(ACCEPT, {"token": value, "full_name": "X", "password": PASSWORD})
        for label, value in candidates.items()
    }

    reference = answers["unknown"]
    for label, response in answers.items():
        assert response.status_code == 400, label
        assert response.headers["content-type"] == "application/problem+json", label
        assert {k: v for k, v in response.json().items() if k != "request_id"} == {
            k: v for k, v in reference.json().items() if k != "request_id"
        }, label


def test_a_weak_password_is_refused_without_using_up_the_invitation(
    apis: ApiFactory, users: UserFactory, tmp_path: Path
) -> None:
    _email, token = invite_for_token(apis, users, tmp_path)
    api = apis.make()

    weak = api.post(ACCEPT, {"token": token, "full_name": "A", "password": "short"})
    good = api.post(ACCEPT, {"token": token, "full_name": "A", "password": PASSWORD})

    assert weak.status_code == 422 and weak.json()["code"] == "weak_password"
    assert "short" not in weak.text.replace("too_short", "")
    assert good.status_code == 201


@pytest.mark.parametrize(
    "payload",
    [
        {"full_name": "No Password"},
        {"password": PASSWORD},
        {"full_name": "   ", "password": PASSWORD},
    ],
    ids=["no-password", "no-name", "blank-name"],
)
def test_a_new_person_must_give_a_name_and_a_password(
    apis: ApiFactory,
    users: UserFactory,
    tmp_path: Path,
    admin_engine: Engine,
    payload: dict[str, str],
) -> None:
    email, token = invite_for_token(apis, users, tmp_path)

    response = apis.make().post(ACCEPT, {"token": token, **payload})

    assert response.status_code == 400 and response.json()["code"] == "invitation_invalid"
    with admin_engine.connect() as connection:
        accounts: Any = connection.execute(
            text("SELECT count(*) FROM users WHERE email = :e"), {"e": email}
        ).scalar_one()
    assert accounts == 0 and invitation_row(admin_engine, token).accepted_at is None


def test_somebody_with_an_account_must_log_in_before_accepting(
    apis: ApiFactory, users: UserFactory, tmp_path: Path, admin_engine: Engine
) -> None:
    """The token alone never lets anybody act as an existing account."""
    admin = logged_in(apis, users.make("organization_admin", school=None, label="admin"))
    existing = users.make("staff", school="1", label="existing")
    assert admin.invite(existing.email, "viewer", users.world.school_a2).status_code == 201
    token = read_outbox(tmp_path)[-1].token

    anonymous = apis.make().post(ACCEPT, {"token": token})
    someone_else = logged_in(apis, users.make("staff", label="stranger"))
    wrong_account = someone_else.post(ACCEPT, {"token": token})
    owner = logged_in(apis, existing)
    accepted = owner.post(ACCEPT, {"token": token})

    for response in (anonymous, wrong_account):
        assert response.status_code == 409
        assert response.json()["code"] == "account_exists_login_required"
    assert invitation_row(admin_engine, token).accepted_at is not None
    assert accepted.status_code == 201
    assert accepted.json()["membership"]["role"] == "viewer"
    assert {m["role"] for m in owner.get("/api/v1/auth/me").json()["memberships"]} == {
        "staff",
        "viewer",
    }


def test_an_existing_account_cannot_have_its_password_replaced_through_an_invitation(
    apis: ApiFactory, users: UserFactory, tmp_path: Path, admin_engine: Engine
) -> None:
    admin = logged_in(apis, users.make("organization_admin", school=None, label="admin"))
    existing = users.make("staff", school="1", label="existing")
    admin.invite(existing.email, "viewer", users.world.school_a2)
    token = read_outbox(tmp_path)[-1].token
    with admin_engine.connect() as connection:
        before: Any = connection.execute(
            text("SELECT password_hash FROM users WHERE id = :i"), {"i": existing.id}
        ).scalar_one()

    apis.make().post(
        ACCEPT, {"token": token, "full_name": "Mallory", "password": "attacker password 1"}
    )
    logged_in(apis, existing).post(
        ACCEPT, {"token": token, "full_name": "Mallory", "password": "attacker password 1"}
    )

    with admin_engine.connect() as connection:
        after = connection.execute(
            text("SELECT password_hash, full_name FROM users WHERE id = :i"), {"i": existing.id}
        ).one()
    assert after.password_hash == before and after.full_name == "Test existing"


def test_an_inactive_account_cannot_accept(
    apis: ApiFactory, users: UserFactory, tmp_path: Path
) -> None:
    admin = logged_in(apis, users.make("organization_admin", school=None, label="admin"))
    dormant = users.make("staff", school="1", active=False, label="dormant")
    admin.invite(dormant.email, "viewer", users.world.school_a2)
    token = read_outbox(tmp_path)[-1].token

    response = apis.make().post(ACCEPT, {"token": token})

    assert response.status_code == 400 and response.json()["code"] == "invitation_invalid"


def test_guessing_tokens_is_slowed_down_per_address_and_blocks_nobody_else(
    apis: ApiFactory, users: UserFactory, tmp_path: Path
) -> None:
    _email, token = invite_for_token(apis, users, tmp_path)
    guesser = apis.make()
    for _ in range(5):
        assert guesser.post(ACCEPT, {"token": "z" * 43}).status_code == 400

    blocked = guesser.post(ACCEPT, {"token": "z" * 43})
    right_but_blocked = guesser.post(
        ACCEPT, {"token": token, "full_name": "A", "password": PASSWORD}
    )
    innocent = apis.make().post(ACCEPT, {"token": token, "full_name": "A", "password": PASSWORD})

    for response in (blocked, right_but_blocked):
        assert response.status_code == 429 and response.json()["code"] == "rate_limited"
        assert int(response.headers["retry-after"]) > 0
    assert innocent.status_code == 201  # another address is not affected by the guesser's block
