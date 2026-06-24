from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


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
