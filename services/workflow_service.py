from __future__ import annotations

import logging
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
from services.workflow_state_service import FinalOutcome, final_outcome_for_status
from form_loader import evaluate_scoped_condition


logger = logging.getLogger(__name__)


class WorkflowTransitionError(ValueError):
    pass


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
            label = str(step.get("admin_label") or step.get("user_label") or step.get("label") or step.get("name") or step["id"])
            labels.setdefault(str(step["id"]), label)
            status_code = str(step.get("status") or step.get("status_code") or "").strip()
            if status_code:
                labels.setdefault(status_code, label)
    if status_id in labels:
        return labels[status_id]
    return get_status_label(status_id)


class WorkflowService:
    def __init__(self, submission_repository=None, audit_log_service=None, *, side_effect_handlers=None, workflow_sla_service=None, document_service=None) -> None:
        self.submission_repository = submission_repository
        self.audit_log_service = audit_log_service
        self.side_effect_handlers = dict(side_effect_handlers or {})
        self.workflow_sla_service = workflow_sla_service
        self.document_service = document_service
        self._driving = set()

    @staticmethod
    def _is_decision_step(step: dict) -> bool:
        if step.get("stage_type") == "decision" or step.get("type") == "decision":
            return True
        if step.get("stage_type") in {"officer_action", "automatic", "system", "user_action", "document", "final"} and step.get("explicit_stage"):
            return False
        return bool(step.get("type") == "manual_decision" or step.get("stage_type") == "officer_action"
                    or step.get("requires_officer_action") or step.get("decisions"))

    def enter_submission_review(self, submission: dict, form_config: dict) -> None:
        """Follow the submission edge into a configured manual gate, never past it."""
        if (form_config.get("workflow") or {}).get("flow_mode") == "explicit":
            step = self._find_step(form_config, self.get_current_step(submission, form_config)) or {}
            if step.get("type") == "form_submit" or step.get("stage_type") == "user_action":
                self.advance_after_action(submission, form_config)
            else:
                self.run_automatic_steps(submission, form_config)
            return
        current = self.get_current_step(submission, form_config)
        step = self._find_step(form_config, current) or {}
        if step.get("type") != "form_submit":
            return
        target = self.resolve_next_step(form_config, current, submission_data=submission.get("data_json"))
        review = self._find_step(form_config, target) or {}
        if not self._is_decision_step(review):
            return
        status = review.get("status") or "WAITING_FOR_OFFICER_DECISION"
        if self.transition_to(submission["submission_id"], target, actor="system", metadata={"target_status": status, "reason": "submission_entered_review"}):
            submission.update(workflow_step=target, workflow_stage=target, process_status=status)

    def definition_for(self, submission: dict) -> dict:
        """Resolve immutable configuration, never the current form editor."""
        if not getattr(self.submission_repository, "session_factory", None) or not submission.get("form_version_id"):
            return {}
        from models import FormVersion
        with self.submission_repository.session_factory() as db:
            version = db.get(FormVersion, submission["form_version_id"])
            return dict(version.definition_json or {}) if version else {}

    def advance_after_action(self, submission: dict, form_config: dict, *, actor="system") -> None:
        """Complete the current action, follow its edge and run automatic nodes."""
        if (form_config.get("workflow") or {}).get("flow_mode") != "explicit":
            return
        current = self.get_current_step(submission, form_config)
        step = self._find_step(form_config, current) or {}
        if step.get("stage_type") == "decision" or step.get("final") or step.get("stage_type") == "final":
            return
        from services.documents.document_workflow_service import is_document_step, document_step_state
        if is_document_step(step):
            fresh = self.submission_repository.get_by_id(submission["submission_id"]) or {}
            if self.get_current_step(fresh, form_config) != current or not document_step_state(fresh, current).get("completed"):
                raise WorkflowTransitionError("Dokument nie spełnia jeszcze wymaganych warunków zakończenia.")
        target = self.resolve_next_step(form_config, current, submission_data=submission.get("data_json"))
        if target:
            self._enter_configured_step(submission, form_config, target, actor=actor)
            self.run_automatic_steps(submission, form_config)

    def _enter_configured_step(self, submission, form_config, target, *, actor="system"):
        step = self._find_step(form_config, target)
        if not step or step.get("active", True) is False:
            raise WorkflowTransitionError("Przejście wskazuje nieistniejący lub nieaktywny etap.")
        status = step.get("status") or self._status_for_step(target)
        if not self.transition_to(submission["submission_id"], target, actor=actor,
                                  metadata={"target_status": status, "reason": "configured_workflow_edge", "expected_step": self.get_current_step(submission, form_config)}):
            raise WorkflowTransitionError("Nie udało się zapisać przejścia workflow.")
        submission.update(workflow_step=target, workflow_stage=target, process_status=status)

    def run_automatic_steps(self, submission: dict, form_config: dict) -> None:
        if (form_config.get("workflow") or {}).get("flow_mode") != "explicit":
            return
        public_id = submission["submission_id"]
        if public_id in self._driving:
            return
        self._driving.add(public_id)
        try:
            visited = set()
            while True:
                current = self.get_current_step(submission, form_config)
                step = self._find_step(form_config, current) or {}
                kind = step.get("stage_type")
                action = step.get("action") or "none"
                from services.documents.document_workflow_service import is_document_step
                if is_document_step(step):
                    if current in visited:
                        raise WorkflowTransitionError("Pętla dokumentowa wymaga działania użytkownika lub urzędnika.")
                    visited.add(current)
                    if not self.document_service.document_workflow.start(submission, form_config):
                        return
                    target = self.resolve_next_step(form_config, current, submission_data=submission.get("data_json"))
                    self._enter_configured_step(submission, form_config, target)
                    continue
                if kind in {"decision", "final", "user_action", "officer_action"} or step.get("final"):
                    return
                if kind == "document" and action != "generate_document":
                    return
                if current in visited:
                    raise WorkflowTransitionError("Automatyczna pętla workflow wymaga działania użytkownika lub urzędnika.")
                visited.add(current)
                if action == "generate_document":
                    if not self.document_service or not step.get("document_id"):
                        raise WorkflowTransitionError("Etap generowania nie ma skonfigurowanego dokumentu.")
                    if step["document_id"] == "training_agreement":
                        documents = self.document_service.generate_documents_for_collection(submission, form_config, step["document_id"], "selected_trainings", "training")
                        generated = {"filename": "collection"} if documents and all(d.get("filename") for d in documents) else None
                    else:
                        generated = self.document_service.generate_document(submission, form_config, step["document_id"])
                    if not generated or not generated.get("enabled", True) or not generated.get("filename"):
                        raise WorkflowTransitionError("Nie udało się wykonać akcji dokumentowej etapu.")
                elif action != "none":
                    handler = self.side_effect_handlers.get(action)
                    if not handler:
                        raise WorkflowTransitionError("Brak obsługi skonfigurowanej akcji automatycznej.")
                    handler(submission_id=public_id, submission=submission, target_step=current, metadata={"form_config": form_config})
                target = self.resolve_next_step(form_config, current, submission_data=submission.get("data_json"))
                if not target:
                    return
                self._enter_configured_step(submission, form_config, target)
        finally:
            self._driving.discard(public_id)

    def get_current_step(self, submission: dict, form_config: dict) -> str:
        explicit = str(submission.get("workflow_stage") or submission.get("workflow_step") or "").strip()
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
        old_step = submission.get("workflow_stage") or submission.get("workflow_step")
        from services.documents.document_workflow_service import is_document_step, document_step_state
        old_config = self._find_step(self.definition_for(submission), old_step) or {}
        if target_step != old_step and is_document_step(old_config) and not document_step_state(submission, old_step).get("completed"):
            raise WorkflowTransitionError("Najpierw zakończ wymagane czynności dokumentu.")
        target_status = str((metadata or {}).get("target_status") or self._status_for_step(target_step))
        side_effects = self._run_side_effects(
            (metadata or {}).get("side_effects") or [],
            submission_id=submission_id,
            submission=submission,
            target_step=target_step,
            metadata=metadata or {},
        )
        updates = {
            "workflow_step": target_step,
            "workflow_stage": target_step,
            "process_status": target_status,
            "legacy_process_status": str(submission.get("legacy_process_status") or submission.get("process_status") or ""),
            "final_outcome": final_outcome_for_status(target_status).value,
        }
        if isinstance((metadata or {}).get("document_states"), dict):
            updates["document_states"] = dict((metadata or {})["document_states"])
        if (metadata or {}).get("officer_decision"):
            updates["officer_decision"] = str((metadata or {})["officer_decision"])
        expected_step = (metadata or {}).get("expected_step")
        if expected_step is not None and hasattr(self.submission_repository, "update_if_workflow_step"):
            updated = self.submission_repository.update_if_workflow_step(submission_id, expected_step, updates)
        else:
            updated = self.submission_repository.update(submission_id, updates)
        if updated:
            logger.info(
                "workflow_transition",
                extra={"event": "workflow_transition", "operation": "workflow_transition", "workflow_step": target_step},
            )
            self._record_workflow_event(
                submission_id,
                previous_status=submission.get("process_status"),
                new_status=updates["process_status"],
                previous_step=old_step,
                new_step=target_step,
                actor=actor,
                reason=str((metadata or {}).get("reason") or ""),
                decision_code=str((metadata or {}).get("officer_decision") or ""),
                user_message=str((metadata or {}).get("user_message") or ""),
                side_effects=side_effects,
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
        if updated and self.workflow_sla_service and hasattr(self.submission_repository, "session_factory"):
            from models import FormSubmission
            from sqlalchemy import select

            with self.submission_repository.session_factory() as db:
                model = db.execute(
                    select(FormSubmission).where(FormSubmission.submission_id == submission_id)
                ).scalar_one_or_none()
                if model is not None:
                    self.workflow_sla_service.transition(
                        db, model, previous_step=old_step, new_step=target_step
                    )
                    db.commit()
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
        old_step = getattr(submission, "workflow_stage", None) or getattr(submission, "workflow_step", None)
        from services.documents.document_workflow_service import is_document_step, document_step_state
        version = getattr(submission, "form_version", None)
        definition = (version.definition_json if version else {}) or {}
        current_config = self._find_step(definition, old_step) or {}
        if is_document_step(current_config) and (target_step != old_step or target_status != old_status):
            if not document_step_state({"document_states": submission.document_states}, old_step).get("completed"):
                raise WorkflowTransitionError("Najpierw zakończ wymagane czynności dokumentu.")
        transition_allowed = can_transition(old_status, target_status)
        if strict and not transition_allowed:
            raise ValueError(f"Niedozwolone przejście statusu: {old_status} -> {target_status}")
        submission.process_status = target_status
        if target_step is not None:
            submission.workflow_step = target_step
            if hasattr(submission, "workflow_stage"):
                submission.workflow_stage = target_step
        if hasattr(submission, "legacy_process_status") and not submission.legacy_process_status:
            submission.legacy_process_status = str(old_status or "")
        if hasattr(submission, "final_outcome"):
            submission.final_outcome = final_outcome_for_status(target_status).value
        new_step = getattr(submission, "workflow_stage", None) or getattr(submission, "workflow_step", None)
        if self.workflow_sla_service and old_step != new_step:
            from sqlalchemy.orm import object_session

            db = object_session(submission)
            if db is not None:
                self.workflow_sla_service.transition(
                    db,
                    submission,
                    previous_step=old_step,
                    new_step=new_step,
                )
        self._record_workflow_event(
            getattr(submission, "submission_id", ""),
            previous_status=old_status,
            new_status=target_status,
            previous_step=old_step,
            new_step=new_step,
            actor=actor,
            reason=reason,
            decision_code="",
            user_message="",
            side_effects={},
            source="workflow_transition_submission",
        )
        logger.info(
            "workflow_transition",
            extra={"event": "workflow_transition", "operation": "workflow_transition", "workflow_step": new_step},
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
        if self._is_decision_step(self._find_step(form_config, current) or {}):
            return False
        available = self.get_available_actions(submission, form_config)
        return any(action.get("target_step") == step_id for action in available)

    def get_available_actions(self, submission: dict, form_config: dict) -> list[dict]:
        current_step = self._find_step(form_config, self.get_current_step(submission, form_config))
        if not current_step:
            return []
        from services.documents.document_workflow_service import is_document_step
        if is_document_step(current_step):
            return []  # Only lifecycle-specific download/upload actions can complete this stage.
        if self._is_decision_step(current_step):
            configured = [item for item in (form_config.get("workflow") or {}).get("decision_types", [])
                          if item.get("active", True) and item.get("step_id") == current_step.get("id")]
            if configured:
                return [{"id": item["code"], "label": item.get("label") or item["code"], "target_step": item.get("target_step")} for item in configured]
            return [
                {"id": decision, "label": decision, "target_step": target}
                for decision, target in (current_step.get("decisions") or {}).items()
            ]
        if current_step.get("transitions"):
            try:
                next_step = self.resolve_next_step(
                    form_config,
                    str(current_step.get("id") or ""),
                    submission_data=dict(submission.get("data_json") or submission),
                )
            except WorkflowTransitionError:
                return []
        else:
            next_step = current_step.get("next")
        return [{"id": "next", "label": "Next", "target_step": next_step}] if next_step else []

    def resolve_next_step(
        self,
        form_config: dict,
        current_step: str,
        decision: str | None = None,
        submission_data: dict | None = None,
    ) -> str | None:
        step = self._find_step(form_config, current_step)
        if not step:
            return None
        if decision is not None:
            configured = next((item for item in (form_config.get("workflow") or {}).get("decision_types", [])
                               if item.get("active", True) and item.get("step_id") == current_step and item.get("code") == decision), None)
            if configured:
                return configured.get("target_step")
            return (step.get("decisions") or {}).get(decision)
        if self._is_decision_step(step) and not ((form_config.get("workflow") or {}).get("flow_mode") == "explicit" and step.get("stage_type") != "decision"):
            return None
        transitions = [item for item in step.get("transitions") or [] if isinstance(item, dict)]
        if transitions:
            values = dict(submission_data or {})
            matches = [
                item for item in transitions
                if evaluate_scoped_condition(item.get("when"), root_data=values, current_data=values)
            ]
            if len(matches) != 1:
                raise WorkflowTransitionError(
                    f"Etap '{current_step}' wymaga dokładnie jednego pasującego przejścia warunkowego; znaleziono {len(matches)}."
                )
            target = str(matches[0].get("next") or "").strip()
            if not target:
                raise WorkflowTransitionError(f"Przejście warunkowe etapu '{current_step}' nie ma celu.")
            return target
        return step.get("next")

    def complete_officer_action(self, submission, form_config, expected_step, *, actor):
        current = self.get_current_step(submission, form_config)
        step = self._find_step(form_config, current) or {}
        if form_config.get("workflow", {}).get("flow_mode") != "explicit" or current != expected_step or step.get("stage_type") != "officer_action":
            raise WorkflowTransitionError("Akcja urzędnika nie jest dostępna na bieżącym etapie.")
        self.advance_after_action(submission, form_config, actor=actor)

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
        definition = self.definition_for(submission_before)
        if definition.get("workflow", {}).get("flow_mode") == "explicit":
            raise WorkflowTransitionError("Wybierz skonfigurowaną opcję decyzji kierującą do poprawy.")
        updates = {
            "correction_required": "Tak",
            "correction_message": message,
            "correction_fields": ",".join(fields),
            "correction_requested_at": datetime.now(timezone.utc).isoformat(),
            "process_status": WAITING_FOR_CORRECTION,
            "workflow_step": "waiting_for_correction",
            "workflow_stage": "waiting_for_correction",
            "final_outcome": FinalOutcome.ACTIVE.value,
            "legacy_process_status": str(
                submission_before.get("legacy_process_status") or submission_before.get("process_status") or ""
            ),
            "officer_decision": "correction_required",
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
                decision_code="correction_required",
                user_message=message,
                side_effects={},
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
        definition = self.definition_for(submission)
        if definition.get("workflow", {}).get("flow_mode") == "explicit":
            self.submission_repository.update(submission_id, {"correction_required": "Nie"})
            self.advance_after_action(submission, definition, actor=actor)
            return True
        updated = self.submission_repository.update(
            submission_id,
            {
                "correction_required": "Nie",
                "process_status": CORRECTED,
                "workflow_step": "officer_review",
                "workflow_stage": "officer_review",
                "final_outcome": FinalOutcome.ACTIVE.value,
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
                decision_code="",
                user_message="",
                side_effects={},
                source="workflow_mark_corrected",
            )
        return updated

    def _find_step(self, form_config: dict, step_id: str) -> dict | None:
        for step in (form_config.get("workflow") or {}).get("steps", []):
            if step.get("id") == step_id:
                return {**step, "explicit_stage": True} if form_config["workflow"].get("flow_mode") == "explicit" else step
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
        decision_code: str = "",
        user_message: str = "",
        side_effects: dict | None = None,
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
                        "decision_code": decision_code,
                        "user_message": user_message,
                        "side_effects": dict(side_effects or {}),
                        "source": source,
                    },
                )
            )
        except Exception:
            return False

    def _run_side_effects(
        self,
        requested,
        *,
        submission_id: str,
        submission: dict,
        target_step: str,
        metadata: dict,
    ) -> dict:
        """Execute configured transition actions through one controlled registry."""

        results: dict[str, dict] = {}
        for raw_name in requested if isinstance(requested, (list, tuple, set)) else []:
            name = str(raw_name or "").strip()
            if not name:
                continue
            handler = self.side_effect_handlers.get(name)
            if handler is None:
                results[name] = {"status": "skipped", "reason": "handler_not_configured"}
                continue
            try:
                value = handler(
                    submission_id=submission_id,
                    submission=submission,
                    target_step=target_step,
                    metadata=metadata,
                )
                results[name] = {"status": "completed", "result": value}
            except Exception as exc:
                results[name] = {"status": "failed", "error": str(exc)}
        return results
