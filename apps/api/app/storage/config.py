"""Settings of the attachment store, read from the environment (not from the shared settings, which
belong to the authentication task)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.storage.base import AttachmentStore
from app.storage.local import LocalDiskStore

# The database refuses a size above this (`ck_expense_attachments_size_range`).
DATABASE_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class StorageSettings(BaseSettings):
    """`ATTACHMENTS_DIR`: where the files go (a docker volume in compose, outside the repository).
    `ATTACHMENT_MAX_BYTES`: the largest file accepted (10 MiB by default, 20 MiB at most)."""

    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    attachments_dir: str = "/attachments"
    attachment_max_bytes: int = Field(
        default=10 * 1024 * 1024, ge=1, le=DATABASE_MAX_ATTACHMENT_BYTES
    )


@lru_cache
def get_storage_settings() -> StorageSettings:
    return StorageSettings()


def get_attachment_store() -> AttachmentStore:
    """FastAPI dependency. Tests replace it with `app.dependency_overrides`."""
    return LocalDiskStore(get_storage_settings().attachments_dir)
