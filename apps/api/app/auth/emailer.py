"""E-mail goes through an outbox (ADR-016). The only implementation writes files, for development.

Each message is a file with permission 0600 in a directory with permission 0700 outside the
repository. An invitation message contains the accept link (and so the token): that is why the
sender is development only and the API refuses to start in production without a real one. The
password-changed notice never contains a password.
"""

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.core.config import ApiSettings


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str
    kind: str


class EmailSender(Protocol):
    def send(self, message: Message) -> None: ...


class FileOutbox:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory)

    def send(self, message: Message) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        path = self.directory / f"{stamp}-{message.kind}-{uuid.uuid4().hex[:8]}.txt"
        content = f"To: {message.to}\nSubject: {message.subject}\n\n{message.body}\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)


def build_sender(settings: ApiSettings) -> EmailSender:
    return FileOutbox(settings.outbox_dir)


def invitation_message(settings: ApiSettings, to: str, token: str, organization: str) -> Message:
    link = f"{settings.public_base_url.rstrip('/')}/accept-invitation?token={token}"
    return Message(
        to=to,
        kind="invitation",
        subject=f"Convite para {organization} no APM Digital",
        body=(
            f"Voce foi convidado(a) para {organization} no APM Digital.\n"
            f"Para aceitar e definir sua senha, abra o link (vale por 72 horas, uso unico):\n{link}"
        ),
    )


def password_changed_message(to: str) -> Message:
    return Message(
        to=to,
        kind="password-changed",
        subject="Sua senha do APM Digital foi alterada",
        body=(
            "A senha da sua conta no APM Digital foi alterada e todas as sessoes foram "
            "encerradas.\n"
            "Se nao foi voce, contate a administracao da sua escola imediatamente."
        ),
    )
