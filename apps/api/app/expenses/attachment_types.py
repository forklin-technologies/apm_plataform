"""The kinds of file an expense accepts. The type comes from the first bytes of the file, never
from the name or the `Content-Type` the client sent (both are free text the client controls).

To accept one more type, add an `AttachmentType` to `ALLOWED_TYPES`: the media type, the extension
(used when the file has no usable name) and a function that recognises the first bytes. Nothing
else changes: the upload, the database and the download read this table.
"""

from collections.abc import Callable
from dataclasses import dataclass

# How many bytes of the start of a file the recognisers may look at.
HEAD_BYTES = 16


@dataclass(frozen=True)
class AttachmentType:
    content_type: str
    extension: str
    matches: Callable[[bytes], bool]


ALLOWED_TYPES: tuple[AttachmentType, ...] = (
    AttachmentType("application/pdf", ".pdf", lambda head: head.startswith(b"%PDF-")),
    AttachmentType("image/png", ".png", lambda head: head.startswith(b"\x89PNG\r\n\x1a\n")),
    AttachmentType("image/jpeg", ".jpg", lambda head: head.startswith(b"\xff\xd8\xff")),
    AttachmentType(
        "image/webp",
        ".webp",
        lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP",
    ),
)


def sniff(head: bytes) -> AttachmentType | None:
    """The allowed type whose signature the file starts with, or None."""
    return next((kind for kind in ALLOWED_TYPES if kind.matches(head[:HEAD_BYTES])), None)
