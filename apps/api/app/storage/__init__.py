"""Where the bytes of an attachment live, behind an interface (TASK-007).

The database keeps only the metadata (`expense_attachments`: key, name, type, size, sha256). The
bytes go through `AttachmentStore`; the only implementation today is a directory on local disk.
A different backend (object storage, later) is a new class with the same four methods.
"""

from app.storage.base import (
    AttachmentStore,
    StorageConflictError,
    StorageError,
    StorageKeyError,
    StorageNotFoundError,
)
from app.storage.config import StorageSettings, get_attachment_store, get_storage_settings
from app.storage.local import LocalDiskStore

__all__ = [
    "AttachmentStore",
    "LocalDiskStore",
    "StorageConflictError",
    "StorageError",
    "StorageKeyError",
    "StorageNotFoundError",
    "StorageSettings",
    "get_attachment_store",
    "get_storage_settings",
]
