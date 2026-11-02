from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5


AUDIT_PENDING_KEY = "_audit_pending"


class AuditPendingError(RuntimeError):
    pass


def pending_audit_state(submission: Any) -> dict:
    data = submission.get("data_json") if isinstance(submission, Mapping) else getattr(submission, "data_json", None)
    data = data or {}
    if not isinstance(data, Mapping):
        return {}
    marker = data.get(AUDIT_PENDING_KEY) or {}
    return dict(marker) if isinstance(marker, Mapping) else {}


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

    def require_no_pending(self, submission: Any) -> None:
        if pending_audit_state(submission):
            raise AuditPendingError(
                "Poprzednia operacja oczekuje na zapis obowiązkowego audytu."
            )

    def stage_pending(
        self,
        submission: Any,
        *,
        event_type: str,
        submission_id: str,
        form_slug: str,
        old_value=None,
        new_value=None,
        actor: str = "system",
        metadata: dict | None = None,
        required_permission: str = "",
        continuation: dict | None = None,
        operation_id: str = "",
        operation_key: str = "",
    ) -> dict:
        return self.stage_pending_events(
            submission,
            events=[{
                "event_type": event_type,
                "old_value": old_value,
                "new_value": new_value,
                "actor": actor,
                "metadata": metadata or {},
            }],
            submission_id=submission_id,
            form_slug=form_slug,
            required_permission=required_permission,
            continuation=continuation,
            operation_id=operation_id,
            operation_key=operation_key,
        )

    def stage_pending_events(
        self,
        submission: Any,
        *,
        events: list[Mapping[str, Any]],
        submission_id: str,
        form_slug: str,
        required_permission: str = "",
        continuation: dict | None = None,
        operation_id: str = "",
        operation_key: str = "",
    ) -> dict:
        self.require_no_pending(submission)
        normalized_events = [dict(item) for item in events if isinstance(item, Mapping)]
        if not normalized_events or any(not str(item.get("event_type") or "").strip() for item in normalized_events):
            raise AuditPendingError("Lista obowiązkowych zdarzeń audytu jest niekompletna.")
        operation_id = str(operation_id or "").strip()
        if not operation_id:
            stable_key = str(operation_key or "").strip()
            if not stable_key:
                raise AuditPendingError("Brak trwałego klucza operacji dla obowiązkowego audytu.")
            event_types = ",".join(str(item["event_type"]) for item in normalized_events)
            operation_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"required-audit-operation:{submission_id}:{event_types}:{stable_key}",
                )
            )
        created_at = datetime.now(timezone.utc).isoformat()
        entries = []
        for index, event in enumerate(normalized_events):
            event_type = str(event.get("event_type") or "").strip()
            entries.append({
                "event_id": str(
                    uuid5(
                        NAMESPACE_URL,
                        f"required-audit:{operation_id}:{index}:{event_type}",
                    )
                ),
                "submission_id": submission_id,
                "form_slug": form_slug,
                "event_type": event_type,
                "old_value": event.get("old_value"),
                "new_value": event.get("new_value"),
                "actor": str(event.get("actor") or "system"),
                "created_at": str(event.get("created_at") or created_at),
                "metadata": dict(event.get("metadata") or {}),
            })
        marker = {
            "operation_id": operation_id,
            "event": entries[0],
            "events": entries,
            "required_permission": str(required_permission or ""),
            "continuation": dict(continuation or {}),
        }
        data = submission.get("data_json") if isinstance(submission, Mapping) else getattr(submission, "data_json", None)
        updated = {**dict(data or {}), AUDIT_PENDING_KEY: marker}
        if isinstance(submission, dict):
            submission["data_json"] = updated
        else:
            submission.data_json = updated
        return marker

    def write_pending(self, marker: Mapping[str, Any]) -> dict:
        raw_entries = marker.get("events")
        entries = (
            [dict(item) for item in raw_entries if isinstance(item, Mapping)]
            if isinstance(raw_entries, list)
            else [dict(marker.get("event") or {})]
        )
        if not entries or any(not item.get("event_id") or not item.get("event_type") for item in entries):
            raise AuditPendingError("Marker audytu jest niekompletny.")
        written = {}
        for entry in entries:
            self.repository.append(entry)
            written = entry
        return written

    def clear_pending(self, submission: Any, operation_id: str) -> None:
        marker = pending_audit_state(submission)
        if not marker or str(marker.get("operation_id") or "") != str(operation_id or ""):
            raise AuditPendingError("Marker audytu zmienił się podczas operacji.")
        data = submission.get("data_json") if isinstance(submission, Mapping) else getattr(submission, "data_json", None)
        updated = dict(data or {})
        updated.pop(AUDIT_PENDING_KEY, None)
        if isinstance(submission, dict):
            submission["data_json"] = updated
        else:
            submission.data_json = updated

    def mark_audit_complete(self, submission: Any, operation_id: str) -> dict:
        marker = pending_audit_state(submission)
        if not marker or str(marker.get("operation_id") or "") != str(operation_id or ""):
            raise AuditPendingError("Marker audytu zmienił się podczas operacji.")
        marker["audit_complete"] = True
        data = submission.get("data_json") if isinstance(submission, Mapping) else getattr(submission, "data_json", None)
        updated = {**dict(data or {}), AUDIT_PENDING_KEY: marker}
        if isinstance(submission, dict):
            submission["data_json"] = updated
        else:
            submission.data_json = updated
        return marker
