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
from services.status_catalog import can_transition, get_status_label, normalize_status as catalog_normalize_status


def workflow_status_label(status_id: str, form_config: dict | None = None) -> str:
    status_id = str(status_id or "").strip()
    if not status_id:
        return "Nieznany status: brak"
    workflow = (form_config or {}).get("workflow") or {}
    labels = {}
    raw_statuses = workflow.get("statuses") or []
    if isinstance(raw_statuses, dict):
        raw_statuses = [{"id": key, **(value if isinstance(value, dict) else {"label": value})} for key, value in raw_statuses.items()]
    if isinstance(raw_statuses, list):
        for status in raw_statuses:
            if isinstance(status, dict) and status.get("id"):
                labels[str(status["id"])] = str(status.get("label") or status.get("name") or status["id"])
    for step in workflow.get("steps") or []:
        if isinstance(step, dict) and step.get("id"):
            labels.setdefault(str(step["id"]), str(step.get("label") or step.get("name") or step["id"]))
    if status_id in labels:
        return labels[status_id]
    return get_status_label(status_id)


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
        if updated:
            self._record_workflow_event(
                submission_id,
                previous_status=submission.get("process_status"),
                new_status=updates["process_status"],
                previous_step=old_step,
                new_step=target_step,
                actor=actor,
                reason=str((metadata or {}).get("reason") or ""),
                source="workflow_transition_to",
            )
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

    def transition_submission(
        self,
        submission,
        target_status: str,
        *,
        actor: str = "system",
        reason: str = "",
        target_step: str | None = None,
        strict: bool = False,
    ):
        old_status = getattr(submission, "process_status", None)
        old_step = getattr(submission, "workflow_step", None)
        transition_allowed = can_transition(old_status, target_status)
        if strict and not transition_allowed:
            raise ValueError(f"Niedozwolone przejście statusu: {old_status} -> {target_status}")
        submission.process_status = target_status
        if target_step is not None:
            submission.workflow_step = target_step
        self._record_workflow_event(
            getattr(submission, "submission_id", ""),
            previous_status=old_status,
            new_status=target_status,
            previous_step=old_step,
            new_step=getattr(submission, "workflow_step", None),
            actor=actor,
            reason=reason,
            source="workflow_transition_submission",
        )
        if self.audit_log_service:
            self.audit_log_service.log_event(
                "WORKFLOW_STATUS_CHANGED",
                getattr(submission, "submission_id", ""),
                getattr(submission, "form_slug", ""),
                old_value=old_status,
                new_value=target_status,
                actor=actor,
                metadata={"reason": reason, "validated": transition_allowed},
            )
        return submission

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
        submission_before = self.submission_repository.get_by_id(submission_id) or {}
        updates = {
            "correction_required": "Tak",
            "correction_message": message,
            "correction_fields": ",".join(fields),
            "correction_requested_at": datetime.now(timezone.utc).isoformat(),
            "process_status": WAITING_FOR_CORRECTION,
            "workflow_step": "waiting_for_correction",
        }
        updated = self.submission_repository.update(submission_id, updates)
        if updated:
            self._record_workflow_event(
                submission_id,
                previous_status=submission_before.get("process_status"),
                new_status=WAITING_FOR_CORRECTION,
                previous_step=submission_before.get("workflow_step"),
                new_step="waiting_for_correction",
                actor=actor,
                reason=message,
                source="workflow_request_correction",
            )
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
        submission = self.submission_repository.get_by_id(submission_id) or {}
        updated = self.submission_repository.update(
            submission_id,
            {
                "correction_required": "Nie",
                "process_status": CORRECTED,
                "workflow_step": "officer_review",
            },
        )
        if updated:
            self._record_workflow_event(
                submission_id,
                previous_status=submission.get("process_status"),
                new_status=CORRECTED,
                previous_step=submission.get("workflow_step"),
                new_step="officer_review",
                actor=actor,
                reason="correction_submitted",
                source="workflow_mark_corrected",
            )
        return updated

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

    def _record_workflow_event(
        self,
        submission_id: str,
        *,
        previous_status: str | None,
        new_status: str | None,
        previous_step: str | None,
        new_step: str | None,
        actor: str,
        reason: str,
        source: str,
    ) -> bool:
        if not self.submission_repository or not hasattr(self.submission_repository, "record_workflow_event"):
            return False
        actor_value = str(actor or "system").strip() or "system"
        try:
            normalized_previous = catalog_normalize_status(previous_status).value if previous_status else ""
            normalized_new = catalog_normalize_status(new_status).value if new_status else ""
            return bool(
                self.submission_repository.record_workflow_event(
                    submission_id,
                    {
                        "previous_status": normalized_previous,
                        "new_status": normalized_new,
                        "previous_step": previous_step or "",
                        "new_step": new_step or "",
                        "actor_role": actor_value,
                        "reason": reason,
                        "source": source,
                    },
                )
            )
        except Exception:
            return False
