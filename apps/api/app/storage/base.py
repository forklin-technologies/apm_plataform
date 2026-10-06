"""The interface every attachment store implements."""

from collections.abc import Iterable, Iterator
from typing import Protocol

CHUNK_SIZE = 64 * 1024


class StorageError(Exception):
    """Base class. Messages are fixed text: a key or a path never appears in one."""


class StorageKeyError(StorageError):
    """The key is not a safe relative key (empty, absolute, `..`, odd characters, too long)."""


class StorageNotFoundError(StorageError):
    """Nothing is stored under the key."""


class StorageConflictError(StorageError):
    """Something is already stored under the key (a key is written once, never replaced)."""


class AttachmentStore(Protocol):
    """Keys are server-made relative paths such as `<organization>/<school>/<expense>/<random>`.

    Nothing a client sent (a file name, say) ever becomes part of a key.
    """

    def put(self, key: str, chunks: Iterable[bytes]) -> int:
        """Write the bytes under `key`, never replacing an existing one; return the size written.

        When `chunks` raises, or the write fails, nothing is left behind under the key.
        """
        ...

    def open(self, key: str) -> Iterator[bytes]:
        """The bytes, in chunks. Raises `StorageNotFoundError` when called, not mid-stream."""
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None:
        """Remove the bytes; removing what is not there is not an error."""
        ...
