"""Random tokens, their stored hashes and keyed digests (tokens: 256 bits, only hashes stored)."""

import base64
import hashlib
import hmac
import re
import secrets

from pydantic import SecretStr

# secrets.token_urlsafe(32): 43 characters of the URL-safe base64 alphabet
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def looks_like_a_token(value: str) -> bool:
    return _TOKEN.fullmatch(value) is not None


def hash_token(token: str) -> bytes:
    """SHA-256 of the token: what the database keeps. Knowing it authenticates nobody."""
    return hashlib.sha256(token.encode()).digest()


def keyed_digest(secret: SecretStr, purpose: str, value: bytes | str) -> bytes:
    """HMAC-SHA256 under a key derived for one purpose, so e-mail, IP and CSRF digests never mix."""
    key = hmac.new(
        secret.get_secret_value().encode(), f"apm-digital/{purpose}".encode(), hashlib.sha256
    ).digest()
    data = value.encode() if isinstance(value, str) else value
    return hmac.new(key, data, hashlib.sha256).digest()


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
