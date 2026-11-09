from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


class ConditionalApproval:
    """A condition belongs to one decision and never waives input validation."""

    KEY = "_conditional_approval"

    @classmethod
    def active(cls, submission):
        data = submission.get("data_json") if isinstance(submission, Mapping) else submission.data_json
        state = (data or {}).get(cls.KEY) or {}
        return state if state.get("status") == "pending" else {}

    @staticmethod
    def inspect(db, submission):
        from copy import deepcopy
        from services.form_version_service import FormVersionService, FormVersionError

        service = FormVersionService()
        version = service.resolve_for_submission(db, submission)
        if version is None:
            raise FormVersionError("Warunkowa decyzja wymaga wersji formularza.")
        definition = version.definition_json or {}
        state = service.refresh_submission_completion(db, submission)
        state["errors"] = service.submission_data_errors(submission, definition, db=db)
        state["complete"] = not (state["errors"] or state["invalid_training_ids"] or state["missing_documents"])
        relaxed = deepcopy(definition)

        def relax(fields):
            for field in fields:
                if field.get("type") == "checkbox" or field.get("consent"):
                    continue
                # An absent group must not hide an outstanding required checkbox.
                children = field.get("fields") or []
                if any(child.get("type") == "checkbox" or child.get("consent") for child in children):
                    continue
                field["required"] = False
                field.pop("required_if", None)
                for permission in field.get("availability") or []:
                    permission["required"] = False
                relax(children)

        relax(relaxed.get("fields") or [])
        blocked = service.submission_data_errors(submission, relaxed, db=db)
        return version, state, blocked

    @classmethod
    def begin(cls, db, submission, *, deadline, reason, actor_id, target_step):
        from datetime import datetime, timezone
        from uuid import uuid4
        from services.form_version_service import FormVersionError

        if cls.active(submission):
            raise FormVersionError("Zgłoszenie ma już nierozliczony warunek.")
        version, state, blocked = cls.inspect(db, submission)
        if not reason.strip() or not actor_id:
            raise FormVersionError("Warunkowa decyzja wymaga uzasadnienia i wykonawcy.")
        if not state["errors"] or blocked or state["invalid_training_ids"] or state["missing_documents"]:
            raise FormVersionError("Odroczyć można wyłącznie brakujące dane, bez wymaganych checkboxów i błędów poprawności.")
        steps = (version.definition_json.get("workflow") or {}).get("steps") or []
        ids = [str(step.get("id") or "") for step in steps]
        current = submission.workflow_step or submission.workflow_stage
        if deadline not in ids or target_step not in ids or current not in ids:
            raise FormVersionError("Wybierz istniejący etap uzupełnienia.")
        start, end = ids.index(current), ids.index(deadline)
        if not start < ids.index(target_step) < end:
            raise FormVersionError("Uzupełnienie musi nastąpić po następnym etapie, przed wejściem w etap graniczny.")
        allowed = []
        for step in steps[start + 1:end]:
            if cls.protected_step(step):
                raise FormVersionError("Uzupełnij dane przed etapem dokumentowym lub końcowym.")
            allowed.append(str(step["id"]))
        marker = {
            "operation_id": str(uuid4()), "status": "pending", "actor_id": actor_id,
            "created_at": datetime.now(timezone.utc).isoformat(), "reason": reason.strip(),
            "missing_fields": sorted(state["errors"]), "deadline_step": deadline,
            "source_step": current, "allowed_steps": allowed, "form_version_id": version.id,
        }
        submission.data_json = {**dict(submission.data_json or {}), cls.KEY: marker}
        return marker

    @staticmethod
    def protected_step(step):
        return bool(step.get("final") or step.get("stage_type") in {
            "final", "documents", "document", "declaration", "agreement",
        } or step.get("action") in {"generate_document", "await_signature"})

    @classmethod
    def check(cls, db, submission, *, target_step=None, settle=False):
        from datetime import datetime, timezone
        from services.form_version_service import FormVersionError

        marker = cls.active(submission)
        if not marker:
            return None
        version, state, blocked = cls.inspect(db, submission)
        if version.id != marker["form_version_id"]:
            raise FormVersionError("Warunek dotyczy innej wersji formularza i wymaga ponownej oceny.")
        if state["complete"]:
            if not settle:
                return None
            closed = {**marker, "status": "completed", "completed_at": datetime.now(timezone.utc).isoformat()}
            data = dict(submission.data_json or {})
            data[cls.KEY] = closed
            data["_conditional_approval_history"] = [*data.get("_conditional_approval_history", []), closed]
            submission.data_json = data
            return closed
        destination = target_step or submission.workflow_step or submission.workflow_stage
        step = next((item for item in (version.definition_json.get("workflow") or {}).get("steps", [])
                     if item.get("id") == destination), {})
        if (blocked or state["invalid_training_ids"] or state["missing_documents"]
                or not set(state["errors"]).issubset(marker["missing_fields"])
                or version.id != marker["form_version_id"]
                or destination not in [marker["source_step"], *marker["allowed_steps"]]
                or cls.protected_step(step)):
            raise FormVersionError("Przed kontynuowaniem uzupełnij dane warunkowo zatwierdzonego zgłoszenia.")
        return None


class SubmissionDecisionService:
    def __init__(
        self,
        submission_repository=None,
        log: logging.Logger | None = None,
        strict_decision_audit_read: bool = False,
    ) -> None:
        self.submission_repository = submission_repository
        self.logger = log or logger
        self.strict_decision_audit_read = strict_decision_audit_read

    def latest_decision(self, submission: Mapping[str, Any]) -> dict:
        submission_id = str(submission.get("submission_id") or "").strip()
        decision = self._repository_decision(submission_id)
        if decision:
            return {
                "source": "submission_decisions",
                "used_legacy_fallback": False,
                "decision": decision,
            }
        legacy_decision = self._legacy_decision(submission)
        if legacy_decision:
            if self.strict_decision_audit_read:
                self.logger.error(
                    "strict_submission_decision_missing area=decisions submission_id=%s reason=legacy_decision_without_submission_decision",
                    submission_id,
                )
                return {
                    "source": "strict_missing_submission_decision",
                    "used_legacy_fallback": False,
                    "strict_mode": True,
                    "not_ready": True,
                    "error": "missing_submission_decision",
                    "decision": None,
                }
            self.logger.warning("Legacy decision fallback used for submission=%s.", submission_id)
            return {
                "source": "legacy_form_submission",
                "used_legacy_fallback": True,
                "decision": legacy_decision,
            }
        return {"source": "none", "used_legacy_fallback": False, "decision": None}

    def list_decisions(self, submission: Mapping[str, Any]) -> dict:
        submission_id = str(submission.get("submission_id") or "").strip()
        decisions = []
        if self.submission_repository and hasattr(self.submission_repository, "list_submission_decisions"):
            try:
                decisions = self.submission_repository.list_submission_decisions(submission_id)
            except Exception:
                self.logger.warning("Nie udalo sie odczytac decyzji dla %s.", submission_id, exc_info=True)
        if decisions:
            return {"source": "submission_decisions", "used_legacy_fallback": False, "decisions": decisions}
        latest = self.latest_decision(submission)
        return {
            "source": latest["source"],
            "used_legacy_fallback": latest["used_legacy_fallback"],
            "decisions": [latest["decision"]] if latest["decision"] else [],
        }

    def _repository_decision(self, submission_id: str) -> dict | None:
        if not self.submission_repository or not hasattr(self.submission_repository, "get_latest_submission_decision"):
            return None
        try:
            return self.submission_repository.get_latest_submission_decision(submission_id)
        except Exception:
            self.logger.warning("Nie udalo sie odczytac decyzji dla %s.", submission_id, exc_info=True)
            return None

    def _legacy_decision(self, submission: Mapping[str, Any]) -> dict | None:
        decision = str(submission.get("officer_decision") or "").strip()
        if not decision:
            status = str(submission.get("process_status") or "").upper()
            if "REJECTED" in status or str(submission.get("akceptacja") or "").strip().lower() == "nie":
                decision = "rejected"
            elif "ACCEPTED" in status or str(submission.get("akceptacja") or "").strip().lower() == "tak":
                decision = "accepted"
        if not decision:
            return None
        return {
            "decision": decision,
            "justification": str(submission.get("officer_decision_reason") or ""),
            "target_status": str(submission.get("process_status") or ""),
            "email_sent": str(
                submission.get("officer_decision_email_sent")
                or submission.get("decision_email_sent")
                or ""
            ).strip().lower()
            == "tak",
            "decided_at": submission.get("updated_at") or submission.get("created_at"),
        }
