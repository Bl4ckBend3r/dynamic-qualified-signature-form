from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class AuditLogService:
    def __init__(self, log_path: str | Path = "data/audit_log.jsonl", repository=None) -> None:
        self.log_path = Path(log_path)
        self.repository = repository

    def log_event(
        self,
        event_type: str,
        submission_id: str,
        form_slug: str,
        old_value=None,
        new_value=None,
        actor: str = "system",
        metadata: dict | None = None,
    ) -> dict:
        entry = {
            "event_id": str(uuid4()),
            "submission_id": submission_id,
            "form_slug": form_slug,
            "event_type": event_type,
            "old_value": old_value,
            "new_value": new_value,
            "actor": actor,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        if self.repository:
            try:
                self.repository.append(entry)
                return entry
            except Exception:
                pass

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
