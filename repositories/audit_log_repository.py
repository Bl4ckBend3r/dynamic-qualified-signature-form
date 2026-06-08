from __future__ import annotations

import json
from typing import Any


class AuditLogRepository:
    def append(self, entry: dict) -> None:
        raise NotImplementedError


class StorageAuditLogRepository(AuditLogRepository):
    def __init__(self, storage: Any, output_dir: str = "output") -> None:
        self.storage = storage
        self.output_dir = str(output_dir or "output").strip("/")

    def append(self, entry: dict) -> None:
        form_slug = str(entry.get("form_slug") or "").strip()
        if not form_slug:
            raise ValueError("entry.form_slug is required")

        audit_dir = f"{self.output_dir}/{form_slug}/audit"
        audit_path = f"{audit_dir}/audit_log.jsonl"
        if hasattr(self.storage, "mkdir"):
            self.storage.mkdir(audit_dir)

        existing = ""
        if hasattr(self.storage, "read_text_or_empty"):
            existing = self.storage.read_text_or_empty(audit_path)

        line = json.dumps(entry, ensure_ascii=False) + "\n"
        if hasattr(self.storage, "write_text"):
            self.storage.write_text(audit_path, existing + line, "application/jsonl; charset=utf-8")
            return

        raise NotImplementedError("Configured storage cannot write audit log entries")
