"""Passwords: Argon2id, a length policy, and a verification that always costs the same.

Parameters are the OWASP minimum for Argon2id (19 MiB of memory, 2 iterations, 1 lane). Nothing in
this module logs, formats or raises with a password or a hash in the message.
"""

import base64
import binascii
import secrets

from argon2 import PasswordHasher, Type, extract_parameters
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


# What a stored hash must look like to be worth verifying. A value that Argon2 can PARSE but not
# verify (a short salt, p=0, a memory cost of a terabyte) fails in microseconds, or tries a huge
# allocation, so it is treated like any other damaged value: the dummy hash is verified instead.
_MAX_MEMORY_KIB = 1 << 20  # 1 GiB
_MAX_TIME_COST = 10
_MAX_PARALLELISM = 16
_MIN_SALT_BYTES = 8
_MIN_DIGEST_BYTES = 4


def _decoded_length(segment: str) -> int:
    try:
        return len(base64.b64decode(segment + "=" * (-len(segment) % 4), validate=True))
    except (binascii.Error, ValueError):
        return 0


def _is_argon2_hash(value: str) -> bool:
    try:
        parameters = extract_parameters(value)
    except InvalidHashError:
        return False
    if not (
        1 <= parameters.time_cost <= _MAX_TIME_COST
        and 1 <= parameters.parallelism <= _MAX_PARALLELISM
        and 8 * parameters.parallelism <= parameters.memory_cost <= _MAX_MEMORY_KIB
    ):
        return False
    segments = value.split("$")  # "", "argon2id", "v=19", "m=...,t=...,p=...", salt, digest
    return (
        len(segments) == 6
        and _decoded_length(segments[4]) >= _MIN_SALT_BYTES
        and _decoded_length(segments[5]) >= _MIN_DIGEST_BYTES
    )


def check_password(stored_hash: str | None, password: str) -> bool:
    """True only for a stored hash that matches. ALWAYS performs exactly one verification, of a
    hash Argon2 can parse: when there is none (no password set) or the stored value is not an
    Argon2 hash (damaged, or from another scheme), the dummy hash is verified instead, so the work
    done and the time taken are the same as for a wrong password."""
    usable = stored_hash if stored_hash is not None and _is_argon2_hash(stored_hash) else None
    matched = verify_hash(usable or _DUMMY_HASH, password)
    return matched and usable is not None


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
