from __future__ import annotations

import logging
from typing import Any, Mapping

from services.status_catalog import get_status_label, normalize_status

logger = logging.getLogger(__name__)


class SubmissionWorkflowHistoryService:
    def __init__(
        self,
        submission_repository=None,
        log: logging.Logger | None = None,
        strict_workflow_history_read: bool = False,
    ) -> None:
        self.submission_repository = submission_repository
        self.logger = log or logger
        self.strict_workflow_history_read = strict_workflow_history_read

    def list_history(self, submission: Mapping[str, Any]) -> dict:
        submission_id = str(submission.get("submission_id") or "").strip()
        events = self._repository_events(submission_id)
        if events:
            return {
                "source": "submission_workflow_events",
                "used_legacy_fallback": False,
                "events": [self._normalize_event(event) for event in events],
            }
        if self.strict_workflow_history_read:
            self.logger.error(
                "strict_workflow_events_missing area=workflow submission_id=%s reason=missing_submission_workflow_events",
                submission_id,
            )
            return {
                "source": "strict_missing_workflow_events",
                "used_legacy_fallback": False,
                "strict_mode": True,
                "not_ready": True,
                "error": "missing_workflow_events",
                "events": [],
            }
        self.logger.warning("Legacy workflow history fallback used for submission=%s.", submission_id)
        status = str(submission.get("process_status") or "").strip()
        return {
            "source": "legacy_form_submission",
            "used_legacy_fallback": True,
            "events": [
                {
                    "previous_status": "",
                    "new_status": normalize_status(status).value if status else "",
                    "new_status_label": get_status_label(status) if status else "",
                    "previous_step": "",
                    "new_step": str(submission.get("workflow_step") or ""),
                    "actor_role": "system",
                    "reason": "Legacy fallback from FormSubmission",
                    "source": "legacy_fallback",
                    "created_at": submission.get("created_at"),
                }
            ],
        }

    def _repository_events(self, submission_id: str) -> list[dict]:
        if not self.submission_repository or not hasattr(self.submission_repository, "list_workflow_events"):
            return []
        try:
            return self.submission_repository.list_workflow_events(submission_id)
        except Exception:
            self.logger.warning("Nie udalo sie odczytac eventow workflow dla %s.", submission_id, exc_info=True)
            return []

    def _normalize_event(self, event: Mapping[str, Any]) -> dict:
        new_status = str(event.get("new_status") or "")
        return {
            **dict(event),
            "new_status": normalize_status(new_status).value if new_status else "",
            "new_status_label": get_status_label(new_status) if new_status else "",
        }
