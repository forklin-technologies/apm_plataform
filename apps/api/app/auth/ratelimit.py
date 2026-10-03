"""Progressive blocking without Redis: the counters are rows in `login_attempts`.

Only HMACs are stored (of the e-mail or token, and of the IP), never the values. Limits, per kind
of attempt (login, password change, invitation acceptance):

  per (subject, IP)  5 failures free in a 15 minute window, then a block of 30 s that doubles
                     with each further failure up to 15 minutes, counted from the last failure
  per IP             20 failures in 15 minutes block that IP for the rest of the window
  per subject        30 failures in an hour (from any IP) block that subject for the rest of it
A success ends the (subject, IP) streak. Blocking is decided BEFORE any password is hashed, and from
the submitted value only, so it is identical whether the account exists or not.

The IP is the one the framework reports (`request.client`): uvicorn replaces it with the forwarded
one only for peers listed in FORWARDED_ALLOW_IPS. Without a proxy in front, X-Forwarded-For is
ignored; if that setting is wrong, every client behind the proxy shares ONE IP and one block.
"""

import ipaddress
import math
import secrets
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.tokens import keyed_digest

PAIR_FREE_FAILURES = 5
PAIR_WINDOW_SECONDS = 15 * 60
PAIR_BACKOFF_BASE_SECONDS = 30
PAIR_BACKOFF_CAP_SECONDS = 15 * 60
IP_FAILURES = 20
IP_WINDOW_SECONDS = 15 * 60
SUBJECT_FAILURES = 30
SUBJECT_WINDOW_SECONDS = 60 * 60
# Chance (in percent) that recording an attempt also purges the old ones.
PURGE_PERCENT = 5

_PAIR_AGES = text(
    "SELECT EXTRACT(EPOCH FROM (now() - attempted_at))::float8 FROM login_attempts "
    "WHERE kind = :kind AND subject_hmac = :subject AND ip_hmac = :ip AND NOT succeeded "
    "AND attempted_at > now() - make_interval(secs => :window) "
    "AND attempted_at > COALESCE((SELECT max(attempted_at) FROM login_attempts "
    "  WHERE kind = :kind AND subject_hmac = :subject AND ip_hmac = :ip AND succeeded), "
    "  '-infinity') "
    "ORDER BY attempted_at DESC LIMIT 100"
)
_IP_AGES = text(
    "SELECT EXTRACT(EPOCH FROM (now() - attempted_at))::float8 FROM login_attempts "
    "WHERE kind = :kind AND ip_hmac = :ip AND NOT succeeded "
    "AND attempted_at > now() - make_interval(secs => :window) "
    "ORDER BY attempted_at DESC LIMIT :limit"
)
_SUBJECT_AGES = text(
    "SELECT EXTRACT(EPOCH FROM (now() - attempted_at))::float8 FROM login_attempts "
    "WHERE kind = :kind AND subject_hmac = :subject AND NOT succeeded "
    "AND attempted_at > now() - make_interval(secs => :window) "
    "ORDER BY attempted_at DESC LIMIT :limit"
)


@dataclass(frozen=True)
class AttemptKeys:
    subject: bytes
    ip: bytes


def client_ip(host: str | None) -> str:
    """The client address as a normalised string, or a fixed word when there is none."""
    try:
        return str(ipaddress.ip_address(host or ""))
    except ValueError:
        return "unknown"


def attempt_keys(secret: SecretStr, subject: str, ip: str) -> AttemptKeys:
    return AttemptKeys(
        subject=keyed_digest(secret, "attempt-subject", subject),
        ip=keyed_digest(secret, "attempt-ip", ip),
    )


def _retry_after(ages: list[float], limit: int, window: int) -> int:
    """Seconds until the `limit`-th most recent failure leaves the window (0 = not blocked)."""
    if len(ages) < limit:
        return 0
    return max(1, math.ceil(window - ages[limit - 1]))


def blocked_for(db: Session, kind: str, keys: AttemptKeys) -> int:
    """How many seconds this attempt must wait (0 = go ahead).

    Takes a transaction-scoped advisory lock on the (subject, IP) pair, so two simultaneous
    attempts cannot both read the same count; it is released when the caller commits.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": keys.subject.hex() + keys.ip.hex()},
    )
    base: dict[str, Any] = {"kind": kind, "subject": keys.subject, "ip": keys.ip}

    pair = [
        float(row[0])
        for row in db.execute(_PAIR_AGES, {**base, "window": PAIR_WINDOW_SECONDS}).all()
    ]
    wait_pair = 0
    if len(pair) >= PAIR_FREE_FAILURES:
        delay = min(
            PAIR_BACKOFF_BASE_SECONDS * 2 ** (len(pair) - PAIR_FREE_FAILURES),
            PAIR_BACKOFF_CAP_SECONDS,
        )
        wait_pair = max(0, math.ceil(delay - pair[0]))

    ip_ages = [
        float(row[0])
        for row in db.execute(
            _IP_AGES, {**base, "window": IP_WINDOW_SECONDS, "limit": IP_FAILURES}
        ).all()
    ]
    subject_ages = [
        float(row[0])
        for row in db.execute(
            _SUBJECT_AGES, {**base, "window": SUBJECT_WINDOW_SECONDS, "limit": SUBJECT_FAILURES}
        ).all()
    ]
    return max(
        wait_pair,
        _retry_after(ip_ages, IP_FAILURES, IP_WINDOW_SECONDS),
        _retry_after(subject_ages, SUBJECT_FAILURES, SUBJECT_WINDOW_SECONDS),
    )


def record_attempt(db: Session, kind: str, keys: AttemptKeys, *, succeeded: bool) -> None:
    db.execute(
        text(
            "INSERT INTO login_attempts (kind, subject_hmac, ip_hmac, succeeded) "
            "VALUES (:kind, :subject, :ip, :succeeded)"
        ),
        {"kind": kind, "subject": keys.subject, "ip": keys.ip, "succeeded": succeeded},
    )
    if secrets.randbelow(100) < PURGE_PERCENT:
        purge_old_attempts(db)


def purge_old_attempts(db: Session) -> None:
    """Drop the attempts the policy lets the application remove (older than a day). The longest
    window that matters is an hour, so nothing here changes a decision."""
    db.execute(text("DELETE FROM login_attempts WHERE attempted_at < now() - interval '24 hours'"))
