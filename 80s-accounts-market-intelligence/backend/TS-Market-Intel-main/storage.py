"""
storage.py — Output sink abstraction.

Writes JSON results + usage reports to either the local filesystem or
Azure Blob Storage. Selected at runtime by get_sink() based on env vars.

Resolution order:
  1. AZURE_STORAGE_CONNECTION_STRING  → BlobSink (key-based auth)
  2. AZURE_STORAGE_ACCOUNT_URL        → BlobSink (DefaultAzureCredential / MI)
  3. neither                          → LocalSink (OUTPUT_DIR or ./output)
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any


class Sink(ABC):
    """Interface for persisting pipeline output."""

    supports_log_files: bool = False

    @abstractmethod
    def read(self, name: str) -> Any | None:
        """Return the JSON-decoded contents of `name`, or None if missing/unreadable."""

    @abstractmethod
    def write(self, name: str, data: Any) -> None:
        """Atomically write JSON-encoded `data` under `name`."""

    def log_path(self, name: str) -> str | None:
        """Return a local filesystem path for a log file, or None to disable file logging."""
        return None


class LocalSink(Sink):
    supports_log_files = True

    def __init__(self, base_dir: str = "./output"):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _path(self, name: str) -> str:
        return os.path.join(self.base_dir, name)

    def read(self, name: str) -> Any | None:
        path = self._path(name)
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def write(self, name: str, data: Any) -> None:
        path = self._path(name)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

    def log_path(self, name: str) -> str:
        path = self._path(name)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        return path


class BlobSink(Sink):
    supports_log_files = False

    def __init__(self, service_client, container: str):
        """Prefer the `from_account_url` / `from_connection_string` factories."""
        self.service = service_client
        self.container_client = self.service.get_container_client(container)
        try:
            self.container_client.create_container()
        except Exception:
            pass  # container already exists

    @classmethod
    def from_account_url(cls, account_url: str, container: str) -> "BlobSink":
        """Auth via DefaultAzureCredential — Managed Identity in Azure, az login locally."""
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient(
            account_url=account_url,
            credential=DefaultAzureCredential(),
        )
        return cls(service, container)

    @classmethod
    def from_connection_string(cls, conn_str: str, container: str) -> "BlobSink":
        """Auth via storage account key embedded in the connection string."""
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient.from_connection_string(conn_str)
        return cls(service, container)

    def read(self, name: str) -> Any | None:
        blob = self.container_client.get_blob_client(name)
        try:
            raw = blob.download_blob().readall()
            return json.loads(raw)
        except Exception:
            return None

    def write(self, name: str, data: Any) -> None:
        blob = self.container_client.get_blob_client(name)
        blob.upload_blob(json.dumps(data, indent=2), overwrite=True)


def get_sink() -> Sink:
    """Select the sink based on env vars.

    Priority: AZURE_STORAGE_CONNECTION_STRING > AZURE_STORAGE_ACCOUNT_URL > local.
    """
    container = os.getenv("AZURE_STORAGE_CONTAINER")

    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if conn_str:
        if not container:
            raise RuntimeError(
                "AZURE_STORAGE_CONTAINER must be set when AZURE_STORAGE_CONNECTION_STRING is set"
            )
        return BlobSink.from_connection_string(conn_str, container)

    url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
    if url:
        if not container:
            raise RuntimeError(
                "AZURE_STORAGE_CONTAINER must be set when AZURE_STORAGE_ACCOUNT_URL is set"
            )
        return BlobSink.from_account_url(url, container)

    return LocalSink(os.getenv("OUTPUT_DIR", "./output"))
