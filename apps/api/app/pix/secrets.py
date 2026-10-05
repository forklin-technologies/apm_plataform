"""Where the credentials of a payment provider live: never in the database, only a reference.

`payment_accounts.secret_ref` holds `env:NAME` or `vault:path` and only the platform writes it
(ADR-018). The API resolves the reference here, in memory, when a real provider needs the
credential; the value is never logged, returned or stored. The sandbox provider needs none.
"""

import os
import re
from typing import Protocol

_ENV_REF = re.compile(r"env:([A-Z][A-Z0-9_]{0,99})")


class SecretNotFound(LookupError):
    """The reference is well formed but nothing answers to it. The message never carries a value."""


class SecretStore(Protocol):
    def resolve(self, secret_ref: str) -> str: ...


class EnvSecretStore:
    """`env:NAME` is the variable NAME of the API process. `vault:` is not available yet."""

    def resolve(self, secret_ref: str) -> str:
        found = _ENV_REF.fullmatch(secret_ref)
        if found is None:
            raise SecretNotFound("unsupported secret reference")
        value = os.environ.get(found.group(1))
        if not value:
            raise SecretNotFound("the secret of this reference is not set")
        return value
