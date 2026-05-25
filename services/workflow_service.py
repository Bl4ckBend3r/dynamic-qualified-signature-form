from __future__ import annotations

from datetime import datetime, timezone

from statuses import (
    COMPLETED,
    CORRECTED,
    REVIEW_ACCEPTED,
    REVIEW_REJECTED,
    WAITING_FOR_CORRECTION,
    WAITING_FOR_SIGNATURE,
    normalize_status,
)


class WorkflowService:
    def __init__(self, submission_repository=None, audit_log_service=None) -> None:
        self.submission_repository = submission_repository
        self.audit_log_service = audit_log_service

    def get_current_step(self, submission: dict, form_config: dict) -> str:
        explicit = str(submission.get("workflow_step") or "").strip()
        if explicit:
            return explicit
        workflow = form_config.get("workflow") or {}
        return workflow.get("initial_step") or "submission"

    def transition_to(
        self,
        submission_id: str,
        target_step: str,
        actor: str = "system",
        metadata: dict | None = None,
    ) -> bool:
        if not self.submission_repository:
            return False
        submission = self.submission_repository.get_by_id(submission_id)
        if not submission:
            return False
        old_step = submission.get("workflow_step")
        updates = {
            "workflow_step": target_step,
            "process_status": self._status_for_step(target_step),
        }
        updated = self.submission_repository.update(submission_id, updates)
        if updated and self.audit_log_service:
            self.audit_log_service.log_event(
                "WORKFLOW_STATUS_CHANGED",
                submission_id,
                submission.get("form_slug", ""),
                old_value=old_step,
                new_value=target_step,
                actor=actor,
                metadata=metadata or {},
            )
        return updated

    def can_execute_step(self, submission: dict, form_config: dict, step_id: str) -> bool:
        current = self.get_current_step(submission, form_config)
        if current == step_id:
            return True
        available = self.get_available_actions(submission, form_config)
        return any(action.get("target_step") == step_id for action in available)

    def get_available_actions(self, submission: dict, form_config: dict) -> list[dict]:
        current_step = self._find_step(form_config, self.get_current_step(submission, form_config))
        if not current_step:
            return []
        if current_step.get("type") == "manual_decision":
            return [
                {"id": decision, "label": decision, "target_step": target}
                for decision, target in (current_step.get("decisions") or {}).items()
            ]
        next_step = current_step.get("next")
        return [{"id": "next", "label": "Next", "target_step": next_step}] if next_step else []

    def resolve_next_step(
        self,
        form_config: dict,
        current_step: str,
        decision: str | None = None,
    ) -> str | None:
        step = self._find_step(form_config, current_step)
        if not step:
            return None
        if decision is not None:
            return (step.get("decisions") or {}).get(decision)
        return step.get("next")

    def request_correction(
        self,
        submission_id: str,
        message: str,
        fields: list[str],
        actor: str = "officer",
    ) -> bool:
        if not self.submission_repository:
            return False
        updates = {
            "correction_required": "Tak",
            "correction_message": message,
            "correction_fields": ",".join(fields),
            "correction_requested_at": datetime.now(timezone.utc).isoformat(),
            "process_status": WAITING_FOR_CORRECTION,
            "workflow_step": "waiting_for_correction",
        }
        updated = self.submission_repository.update(submission_id, updates)
        if updated and self.audit_log_service:
            submission = self.submission_repository.get_by_id(submission_id) or {}
            self.audit_log_service.log_event(
                "OFFICER_DECISION_CHANGED",
                submission_id,
                submission.get("form_slug", ""),
                new_value=WAITING_FOR_CORRECTION,
                actor=actor,
                metadata={"message": message, "fields": fields},
            )
        return updated

    def submit_correction(
        self,
        submission_id: str,
        corrected_data: dict,
        actor: str = "participant",
    ) -> bool:
        if not self.submission_repository:
            return False
        updates = {
            **corrected_data,
            "correction_completed_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.submission_repository.update(submission_id, updates)

    def mark_corrected(self, submission_id: str, actor: str = "system") -> bool:
        if not self.submission_repository:
            return False
        return self.submission_repository.update(
            submission_id,
            {
                "correction_required": "Nie",
                "process_status": CORRECTED,
                "workflow_step": "officer_review",
            },
        )

    def _find_step(self, form_config: dict, step_id: str) -> dict | None:
        for step in (form_config.get("workflow") or {}).get("steps", []):
            if step.get("id") == step_id:
                return step
        return None

    def _status_for_step(self, step_id: str) -> str:
        if step_id == "completed":
            return COMPLETED
        if step_id == "end_rejected":
            return REVIEW_REJECTED
        if "signature" in step_id:
            return WAITING_FOR_SIGNATURE
        if step_id in {"declaration", "agreement"}:
            return REVIEW_ACCEPTED
        return normalize_status(step_id.upper())
