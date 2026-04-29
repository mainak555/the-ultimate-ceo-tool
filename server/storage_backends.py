"""Storage backend abstraction for chat attachments."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class StorageStrategy(ABC):
    """Provider-neutral object storage contract."""

    @abstractmethod
    def upload_bytes(self, *, key: str, data: bytes, content_type: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def download_bytes(self, *, key: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def delete_prefix(self, *, prefix: str) -> int:
        raise NotImplementedError


class AzureBlobStorageStrategy(StorageStrategy):
    """Azure Blob Storage implementation for attachment payload bytes."""

    def __init__(self) -> None:
        container_sas_url = (os.getenv("AZURE_STORAGE_CONTAINER_SAS_URL") or "").strip()
        if not container_sas_url:
            raise ValueError("AZURE_STORAGE_CONTAINER_SAS_URL is required for Azure Blob storage.")

        try:
            from azure.storage.blob import ContainerClient
        except Exception as exc:  # noqa: BLE001
            raise ValueError("azure-storage-blob package is required for Azure attachment storage.") from exc

        self._container = ContainerClient.from_container_url(container_sas_url)

    def upload_bytes(self, *, key: str, data: bytes, content_type: str) -> None:
        blob = self._container.get_blob_client(key)
        blob.upload_blob(
            data,
            overwrite=True,
            content_type=(content_type or "application/octet-stream"),
        )

    def download_bytes(self, *, key: str) -> bytes:
        blob = self._container.get_blob_client(key)
        return blob.download_blob().readall()

    def delete_prefix(self, *, prefix: str) -> int:
        deleted = 0
        for blob in self._container.list_blobs(name_starts_with=prefix):
            self._container.delete_blob(blob.name)
            deleted += 1
        return deleted


def build_storage_strategy() -> StorageStrategy:
    """Factory for selecting the active attachment storage provider."""
    provider = (os.getenv("ATTACHMENT_STORAGE_PROVIDER") or "azure").strip().lower()
    if provider == "azure":
        return AzureBlobStorageStrategy()
    raise ValueError(f"Unsupported ATTACHMENT_STORAGE_PROVIDER '{provider}'.")
