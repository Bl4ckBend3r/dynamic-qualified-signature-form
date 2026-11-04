from __future__ import annotations

from typing import Any


class StorageRepository:
    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def read_file(self, path: str) -> bytes:
        return self.storage.get_file_bytes(path)

    def write_file(self, path: str, content: bytes) -> bool:
        if hasattr(self.storage, "put_file_bytes"):
            self.storage.put_file_bytes(path, content)
            return True
        if hasattr(self.storage, "write_file"):
            return bool(self.storage.write_file(path, content))
        raise NotImplementedError("Configured storage cannot write arbitrary files")

    def file_exists(self, path: str) -> bool:
        return bool(self.storage.exists(path))

    def list_files(self, path: str) -> list[str]:
        if hasattr(self.storage, "list_files"):
            return list(self.storage.list_files(path))
        raise NotImplementedError("Configured storage cannot list arbitrary directories")
