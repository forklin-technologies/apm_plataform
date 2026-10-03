"""Passwords: Argon2id, a length policy, and a verification that always costs the same.

Parameters are the OWASP minimum for Argon2id (19 MiB of memory, 2 iterations, 1 lane). Nothing in
this module logs, formats or raises with a password or a hash in the message.
"""

import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12
MAX_LENGTH = 128  # an upper bound so a huge body cannot be used to burn CPU

_hasher = PasswordHasher(
    time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16, type=Type.ID
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_hash(stored_hash: str, password: str) -> bool:
    """The ONE place a password is checked against a hash (tests spy on it)."""
    try:
        return bool(_hasher.verify(stored_hash, password))
    except (VerificationError, InvalidHashError):
        return False


# A hash of the same cost as a real one, made once with a random value nobody knows. Used when
# there is nothing to compare with (unknown e-mail, inactive user, no password set), so the time
# taken does not tell those cases from a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def check_password(stored_hash: str | None, password: str) -> bool:
    """True only for a stored hash that matches. ALWAYS performs exactly one verification."""
    matched = verify_hash(stored_hash or _DUMMY_HASH, password)
    return matched and stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    try:
        return bool(_hasher.check_needs_rehash(stored_hash))
    except InvalidHashError:
        return False


def password_problem(password: str) -> str | None:
    """A fixed code for a password that is not acceptable, or None."""
    if len(password) < MIN_LENGTH:
        return "too_short"
    if len(password) > MAX_LENGTH:
        return "too_long"
    if not password.strip():
        return "blank"
    return None
