"""QA finding N3: a stored value that is not an Argon2 hash (damaged, or from another scheme) costs
one real Argon2 verification, exactly like a wrong password, so the time does not tell it apart.
The test does not read a clock: it spies on the hasher and asserts the work was done."""

import pytest
from argon2 import extract_parameters
from sqlalchemy import Engine, text

from app.auth import passwords
from tests.authsupport import ApiFactory, SpyHasher, UserFactory

UNPARSABLE = [
    "not-a-hash",
    "",
    "$argon2id$garbage",
    "$argon2id$v=19$m=19456,t=2,p=1$onlytwo",
    "$2b$12$abcdefghijklmnopqrstuuABCDEFGHIJKLMNOPQRSTUVWXYZ0123456",  # bcrypt, not Argon2
    "x" * 300,
    # Well-shaped for Argon2's parser but not verifiable: they used to fail in microseconds
    # (review of 2026-10-05), which told a damaged row apart from a wrong password.
    "$argon2id$v=19$m=19456,t=2,p=1$aaaa$bbbb",  # salt too short
    "$argon2id$v=19$m=19456,t=2,p=0$c29tZXNhbHRzb21l$YWJjZGVmZ2g",  # no lanes
    "$argon2id$v=19$m=999999999,t=2,p=1$c29tZXNhbHRzb21l$YWJjZGVmZ2hpamts",  # about a terabyte
    "$argon2id$v=19$m=19456,t=0,p=1$c29tZXNhbHRzb21l$YWJjZGVmZ2hpamts",  # no passes
    "$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHRzb21l$***not-base64***",
]


@pytest.mark.parametrize("stored", UNPARSABLE, ids=lambda s: s[:12] or "empty")
def test_a_stored_value_that_does_not_parse_is_checked_against_the_dummy_hash(
    monkeypatch: pytest.MonkeyPatch, stored: str
) -> None:
    spy = SpyHasher(passwords._hasher)  # noqa: SLF001
    monkeypatch.setattr(passwords, "_hasher", spy)

    matched = passwords.check_password(stored, "whatever password")

    assert matched is False
    # Exactly one verification, of a hash Argon2 can parse (so the real work happened).
    assert spy.verified == [passwords._DUMMY_HASH]  # noqa: SLF001
    extract_parameters(spy.verified[0])


def test_a_valid_stored_hash_is_still_the_one_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = passwords.hash_password("a long enough password")
    spy = SpyHasher(passwords._hasher)  # noqa: SLF001
    monkeypatch.setattr(passwords, "_hasher", spy)

    assert passwords.check_password(stored, "a long enough password") is True
    assert passwords.check_password(stored, "another long password") is False
    assert spy.verified == [stored, stored]


def test_logging_in_to_an_account_whose_stored_hash_is_garbage_does_the_same_work(
    apis: ApiFactory,
    users: UserFactory,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = users.make("staff")
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE users SET password_hash = 'not-an-argon2-hash' WHERE id = :id"),
            {"id": user.id},
        )
    spy = SpyHasher(passwords._hasher)  # noqa: SLF001
    monkeypatch.setattr(passwords, "_hasher", spy)

    response = apis.make().login(user)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
    assert spy.verified == [passwords._DUMMY_HASH]  # noqa: SLF001
