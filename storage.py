"""
storage.py — Output sink abstraction.

Writes JSON results + usage reports to either the local filesystem or
Azure Blob Storage. Selected at runtime by get_sink() based on env vars.

Local  (default):  LocalSink(OUTPUT_DIR or ./output)
Azure Blob:        BlobSink(AZURE_STORAGE_ACCOUNT_URL, AZURE_STORAGE_CONTAINER)
                   Auth via DefaultAzureCredential — Managed Identity in Azure,
                   `az login` credentials or service-principal env vars locally.
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

    def __init__(self, account_url: str, container: str):
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        self.service = BlobServiceClient(
            account_url=account_url,
            credential=DefaultAzureCredential(),
        )
        self.container_client = self.service.get_container_client(container)
        try:
            self.container_client.create_container()
        except Exception:
            pass  # container already exists

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
    """Select the sink based on env vars. Azure if AZURE_STORAGE_ACCOUNT_URL is set, else local."""
    url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
    if url:
        container = os.getenv("AZURE_STORAGE_CONTAINER")
        if not container:
            raise RuntimeError(
                "AZURE_STORAGE_CONTAINER must be set when AZURE_STORAGE_ACCOUNT_URL is set"
            )
        return BlobSink(url, container)
    return LocalSink(os.getenv("OUTPUT_DIR", "./output"))
