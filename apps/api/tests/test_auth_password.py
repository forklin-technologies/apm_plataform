"""A2/A4: changing the password. It ends every session, starts a new one, tells the person by
e-mail, never takes a weak password and can't be used to guess the current one."""

from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from sqlalchemy import Engine, text

from tests.authsupport import OTHER_PASSWORD, PASSWORD, Api, ApiFactory, UserFactory, read_outbox
from tests.test_auth_login import cookie_attributes, session_row

CHANGE_PATH = "/api/v1/auth/password"
ME = "/api/v1/auth/me"


def change(api: Api, current: str = PASSWORD, new: str = OTHER_PASSWORD, **kwargs: Any) -> Any:
    return api.post(CHANGE_PATH, {"current_password": current, "new_password": new}, **kwargs)


def stored_hash(engine: Engine, user_id: Any) -> str:
    with engine.connect() as connection:
        value: Any = connection.execute(
            text("SELECT password_hash FROM users WHERE id = :id"), {"id": user_id}
        ).scalar_one()
    return str(value)


def test_changing_the_password_ends_every_session_and_starts_a_new_one(
    apis: ApiFactory, users: UserFactory, admin_engine: Engine
) -> None:
    user = users.make("staff")
    laptop, phone = apis.make(), apis.make()
    laptop.login(user)
    phone.login(user)
    old_laptop, old_phone, old_csrf = laptop.session_token, phone.session_token, laptop.csrf
    assert old_laptop and old_phone
    before = stored_hash(admin_engine, user.id)

    response = change(laptop)

    assert response.status_code == 204 and response.content == b""
    assert set(cookie_attributes(response)) == {"apm_session", "apm_csrf"}  # a new session
    assert laptop.session_token not in (None, old_laptop) and laptop.csrf != old_csrf
    assert laptop.get(ME).status_code == 200  # the one that changed it stays logged in
    assert phone.get(ME).status_code == 401  # every other one is gone
    for token in (old_laptop, old_phone):
        row = session_row(admin_engine, token)
        assert row.revoked_at is not None and row.revoked_reason == "password_changed"
    after = stored_hash(admin_engine, user.id)
    assert after != before and after.startswith("$argon2id$")
    assert PasswordHasher().verify(after, OTHER_PASSWORD)


def test_the_old_password_stops_working_and_the_new_one_works(
    apis: ApiFactory, users: UserFactory
) -> None:
    user = users.make("staff")
    api = apis.make()
    api.login(user)
    change(api)
    api.forget_cookies()

    assert apis.make().login(user, PASSWORD).status_code == 401
    assert apis.make().login(user, OTHER_PASSWORD).status_code == 200


def test_the_person_is_told_by_e_mail_and_the_notice_holds_no_password(
    api: Api, users: UserFactory, tmp_path: Path
) -> None:
    user = users.make("staff")
    api.login(user)

    change(api)

    messages = [m for m in read_outbox(tmp_path) if m.kind == "password-changed"]
    assert len(messages) == 1
    message = messages[0]
    assert message.to == user.email
    assert PASSWORD not in message.body + message.subject
    assert OTHER_PASSWORD not in message.body + message.subject
    assert "argon2" not in message.body.lower()
    assert message.file_mode == 0o600
    assert tmp_path.stat().st_mode & 0o777 == 0o700 or tmp_path.stat().st_mode & 0o077 == 0


def test_a_failing_outbox_does_not_undo_the_change(
    apis: ApiFactory, users: UserFactory, tmp_path: Path, admin_engine: Engine
) -> None:
    broken = tmp_path / "not-a-directory"
    broken.write_text("a file where the outbox should be")
    api = apis.make(outbox_dir=str(broken))
    user = users.make("staff")
    api.login(user)
    before = stored_hash(admin_engine, user.id)

    response = change(api)

    assert response.status_code == 204
    assert stored_hash(admin_engine, user.id) != before


def test_a_wrong_current_password_changes_nothing(
    api: Api, users: UserFactory, admin_engine: Engine, tmp_path: Path
) -> None:
    user = users.make("staff")
    api.login(user)
    token = api.session_token
    before = stored_hash(admin_engine, user.id)

    response = change(api, current="definitely not it")

    assert response.status_code == 403
    assert response.json()["code"] == "current_password_incorrect"
    assert stored_hash(admin_engine, user.id) == before
    assert api.session_token == token and api.get(ME).status_code == 200  # still logged in
    assert read_outbox(tmp_path) == []  # and nobody was notified of a change that did not happen


@pytest.mark.parametrize(
    ("new", "code"),
    [
        ("short", "too_short"),
        ("elevenchars", "too_short"),
        (" " * 14, "blank"),
        ("x" * 129, "too_long"),
        (PASSWORD, "same_as_current"),
    ],
)
def test_a_weak_new_password_is_refused_with_a_fixed_code(
    api: Api, users: UserFactory, admin_engine: Engine, new: str, code: str
) -> None:
    user = users.make("staff")
    api.login(user)
    before = stored_hash(admin_engine, user.id)

    response = change(api, new=new)

    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "weak_password"
    assert problem["errors"] == [{"field": "new_password", "code": code}]
    assert new.strip() == "" or new not in response.text.replace("too_short", "")
    assert stored_hash(admin_engine, user.id) == before


def test_twelve_characters_is_enough(api: Api, users: UserFactory) -> None:
    api.login(users.make("staff"))

    assert change(api, new="twelve chars").status_code == 204


@pytest.mark.parametrize("body", [{}, {"current_password": PASSWORD}, {"new_password": "x" * 20}])
def test_a_malformed_change_is_a_422(api: Api, users: UserFactory, body: dict[str, str]) -> None:
    api.login(users.make("staff"))

    assert api.post(CHANGE_PATH, body).status_code == 422


def test_changing_the_password_needs_a_session_the_csrf_token_and_the_origin(
    api: Api, users: UserFactory
) -> None:
    assert change(api, csrf=False).status_code == 401
    api.login(users.make("staff"))

    assert change(api, csrf=False).status_code == 403
    assert change(api, origin="https://evil.example").status_code == 403
    assert change(api, origin=False).status_code == 403


def test_the_current_password_cannot_be_guessed_by_a_hijacked_session(
    api: Api, users: UserFactory
) -> None:
    """Five wrong tries are free; then the attempts wait, and even the RIGHT password waits."""
    api.login(users.make("staff"))
    for _ in range(5):
        assert change(api, current="guess guess guess").status_code == 403

    blocked = change(api, current="guess guess guess")
    right_but_blocked = change(api)

    for response in (blocked, right_but_blocked):
        assert response.status_code == 429
        assert response.json()["code"] == "rate_limited"
        assert int(response.headers["retry-after"]) > 0
