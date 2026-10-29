from __future__ import annotations

import json
from typing import Any
from uuid import UUID


class AuditLogRepository:
    def append(self, entry: dict) -> None:
        raise NotImplementedError


class StorageAuditLogRepository(AuditLogRepository):
    def __init__(self, storage: Any, output_dir: str = "output") -> None:
        self.storage = storage
        self.output_dir = str(output_dir or "output").strip("/")

    def append(self, entry: dict) -> None:
        form_slug = str(entry.get("form_slug") or "").strip()
        if (
            not form_slug
            or form_slug in {".", ".."}
            or "/" in form_slug
            or "\\" in form_slug
            or any(ord(char) < 32 or ord(char) == 127 for char in form_slug)
        ):
            raise ValueError("entry.form_slug must be a safe path segment")
        try:
            event_id = str(UUID(str(entry.get("event_id") or "")))
        except ValueError as exc:
            raise ValueError("entry.event_id must be a UUID") from exc

        audit_dir = f"{self.output_dir}/{form_slug}/audit"
        events_dir = f"{audit_dir}/events"
        self.storage.mkdir(f"{self.output_dir}/{form_slug}")
        self.storage.mkdir(audit_dir)
        self.storage.mkdir(events_dir)
        audit_path = f"{events_dir}/{event_id}.jsonl"
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        self.storage.write_text(audit_path, line, "application/jsonl; charset=utf-8")
