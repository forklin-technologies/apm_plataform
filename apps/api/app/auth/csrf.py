"""CSRF: a double-submit token bound to the session.

The token is HMAC(AUTH_SECRET, session id), carried in a cookie the page can read and sent back in
the X-CSRF-Token header. A state-changing request must present BOTH, equal to each other AND equal
to the value derived from the session in use: a cookie planted by an attacker (on a sibling
subdomain, say) does not pass, because forging it needs the secret.
"""

import hmac

from fastapi import Request
from pydantic import SecretStr

from app.auth.tokens import b64url, keyed_digest

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def csrf_token(secret: SecretStr, session_id: bytes) -> str:
    return b64url(keyed_digest(secret, "csrf", session_id))


def csrf_is_valid(request: Request, secret: SecretStr, session_id: bytes, cookie_name: str) -> bool:
    header = request.headers.get(CSRF_HEADER)
    cookie = request.cookies.get(cookie_name)
    if not header or not cookie:
        return False
    expected = csrf_token(secret, session_id)
    return hmac.compare_digest(header, cookie) and hmac.compare_digest(header, expected)
