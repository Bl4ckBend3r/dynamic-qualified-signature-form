from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


class AuditLogService:
    def __init__(self, repository) -> None:
        if repository is None:
            raise ValueError("Audit log repository is required.")
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
        event_id: str = "",
    ) -> dict:
        entry = {
            "event_id": str(event_id or uuid4()),
            "submission_id": submission_id,
            "form_slug": form_slug,
            "event_type": event_type,
            "old_value": old_value,
            "new_value": new_value,
            "actor": actor,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        self.repository.append(entry)
        return entry
