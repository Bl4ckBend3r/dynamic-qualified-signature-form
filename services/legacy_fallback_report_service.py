from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from models import FormSubmission, SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent
from services.document_naming_service import resolve_pdf_storage_path
from services.status_catalog import normalize_status
from services.submission_document_service import SubmissionDocumentType


DOCUMENT_TYPES = {
    "pdf_filename": ("form_submission", SubmissionDocumentType.FORM_PDF, "", False),
    "signed_pdf_filename": ("form_submission", SubmissionDocumentType.SIGNED_FORM_PDF, "", True),
    "declaration_filename": ("declaration", SubmissionDocumentType.DECLARATION, "declaration", False),
    "declaration_signed_filename": ("declaration", SubmissionDocumentType.SIGNED_DECLARATION, "declaration", True),
    "agreement_filename": ("agreement", SubmissionDocumentType.AGREEMENT, "agreement", False),
    "agreement_signed_filename": ("agreement", SubmissionDocumentType.SIGNED_AGREEMENT, "agreement", True),
}


def empty_fallback_report() -> dict:
    return {
        "processed_submissions": 0,
        "schema_mismatch": False,
        "documents": {
            "using_new_metadata": 0,
            "using_legacy_fallback": 0,
            "missing_submission_file": 0,
            "missing_storage_path": 0,
            "missing_physical_file": 0,
            "ambiguous": 0,
            "errors": 0,
        },
        "workflow": {
            "using_events": 0,
            "using_legacy_fallback": 0,
            "missing_events": 0,
            "normalized_statuses": 0,
            "errors": 0,
        },
        "decisions": {
            "using_submission_decision": 0,
            "using_legacy_fallback": 0,
            "missing_decision": 0,
            "ambiguous": 0,
            "errors": 0,
        },
        "fallback_records": [],
        "errors": [],
    }


class StrictFallbackError(RuntimeError):
    pass


class LegacyFallbackReportService:
    def __init__(
        self,
        *,
        output_dir: str = "output",
        file_exists=None,
        strict_document_metadata_read: bool = False,
        strict_workflow_history_read: bool = False,
        strict_decision_audit_read: bool = False,
    ) -> None:
        self.output_dir = output_dir
        self.file_exists = file_exists or self._local_file_exists
        self.strict_document_metadata_read = strict_document_metadata_read
        self.strict_workflow_history_read = strict_workflow_history_read
        self.strict_decision_audit_read = strict_decision_audit_read

    def build_fallback_report(
        self,
        db,
        *,
        limit: int | None = None,
        submission_id: str | None = None,
    ) -> dict:
        report = empty_fallback_report()
        query = db.query(FormSubmission).order_by(FormSubmission.id)
        if submission_id:
            query = query.filter(FormSubmission.submission_id == submission_id)
        submissions = query.limit(limit).all() if limit else query.all()
        for submission in submissions:
            report["processed_submissions"] += 1
            try:
                self.scan_documents(db, submission, report)
                self.scan_workflow(db, submission, report)
                self.scan_decisions(db, submission, report)
            except Exception as exc:
                self._rollback(db)
                report["errors"].append(
                    self._error_entry(submission, "submission", exc)
                )
        return report

    def scan_documents(self, db, submission: FormSubmission, report: dict) -> None:
        for candidate in self._document_candidates(submission):
            try:
                matches = (
                    db.query(SubmissionFile)
                    .filter(SubmissionFile.submission_id == submission.id)
                    .filter(SubmissionFile.document_type == candidate["document_type"])
                    .filter(SubmissionFile.filename == candidate["filename"])
                    .filter(SubmissionFile.training_key == candidate["training_key"])
                    .all()
                )
                if len(matches) > 1:
                    report["documents"]["ambiguous"] += 1
                    self._fallback(report, submission, "documents", "ambiguous", candidate, "Niejednoznaczne metadane dokumentu.")
                    continue
                if not matches:
                    report["documents"]["using_legacy_fallback"] += 1
                    report["documents"]["missing_submission_file"] += 1
                    self._fallback(report, submission, "documents", "missing_submission_file", candidate, "Brak SubmissionFile dla dokumentu legacy.")
                    if self.strict_document_metadata_read:
                        raise StrictFallbackError("Brak wymaganych metadanych SubmissionFile.")
                    continue
                file_row = matches[0]
                report["documents"]["using_new_metadata"] += 1
                if not file_row.storage_path:
                    report["documents"]["using_legacy_fallback"] += 1
                    report["documents"]["missing_storage_path"] += 1
                    self._fallback(report, submission, "documents", "missing_storage_path", candidate, "SubmissionFile nie ma storage_path.")
                    if self.strict_document_metadata_read:
                        raise StrictFallbackError("SubmissionFile bez storage_path.")
                    continue
                if not self.file_exists(file_row.storage_path):
                    report["documents"]["missing_physical_file"] += 1
                    self._fallback(report, submission, "documents", "missing_physical_file", candidate, "Plik fizyczny nie zostal potwierdzony.")
            except StrictFallbackError:
                report["documents"]["errors"] += 1
                raise
            except Exception as exc:
                self._rollback(db)
                report["documents"]["errors"] += 1
                report["errors"].append(self._error_entry(submission, "documents", exc))
                if self._is_schema_mismatch(exc):
                    report["schema_mismatch"] = True

    def scan_workflow(self, db, submission: FormSubmission, report: dict) -> None:
        try:
            events = db.query(SubmissionWorkflowEvent).filter(SubmissionWorkflowEvent.submission_id == submission.id).all()
            if events:
                report["workflow"]["using_events"] += 1
            else:
                report["workflow"]["using_legacy_fallback"] += 1
                report["workflow"]["missing_events"] += 1
                self._fallback(report, submission, "workflow", "missing_events", None, "Brak SubmissionWorkflowEvent.")
                if self.strict_workflow_history_read:
                    raise StrictFallbackError("Brak wymaganych eventow workflow.")
            raw_status = str(submission.process_status or "")
            if raw_status and normalize_status(raw_status).value != raw_status:
                report["workflow"]["normalized_statuses"] += 1
        except StrictFallbackError:
            report["workflow"]["errors"] += 1
            raise
        except Exception as exc:
            self._rollback(db)
            report["workflow"]["errors"] += 1
            report["errors"].append(self._error_entry(submission, "workflow", exc))
            if self._is_schema_mismatch(exc):
                report["schema_mismatch"] = True

    def scan_decisions(self, db, submission: FormSubmission, report: dict) -> None:
        try:
            decisions = db.query(SubmissionDecision).filter(SubmissionDecision.submission_id == submission.id).all()
            legacy_decision = self._legacy_decision(submission)
            if len(decisions) > 1:
                report["decisions"]["ambiguous"] += 1
            if decisions:
                report["decisions"]["using_submission_decision"] += 1
                return
            if legacy_decision:
                report["decisions"]["using_legacy_fallback"] += 1
                self._fallback(report, submission, "decisions", "legacy_decision_only", None, "Decyzja istnieje tylko w polach legacy.")
                if self.strict_decision_audit_read:
                    raise StrictFallbackError("Brak wymaganego SubmissionDecision.")
            else:
                report["decisions"]["missing_decision"] += 1
        except StrictFallbackError:
            report["decisions"]["errors"] += 1
            raise
        except Exception as exc:
            self._rollback(db)
            report["decisions"]["errors"] += 1
            report["errors"].append(self._error_entry(submission, "decisions", exc))
            if self._is_schema_mismatch(exc):
                report["schema_mismatch"] = True

    def summarize_fallback_usage(self, report: dict) -> dict:
        return {
            "documents": report["documents"]["using_legacy_fallback"],
            "workflow": report["workflow"]["using_legacy_fallback"],
            "decisions": report["decisions"]["using_legacy_fallback"],
            "total_fallback_records": len(report["fallback_records"]),
        }

    def _rollback(self, db) -> None:
        if hasattr(db, "rollback"):
            try:
                db.rollback()
            except Exception:
                pass

    def _error_entry(self, submission: FormSubmission, area: str, exc: Exception) -> dict:
        category = "schema_mismatch" if self._is_schema_mismatch(exc) else "technical_error"
        return {
            "submission_id": submission.submission_id,
            "area": area,
            "category": category,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }

    def _is_schema_mismatch(self, exc: Exception) -> bool:
        text = f"{exc.__class__.__name__} {exc}".lower()
        patterns = (
            "undefinedcolumn",
            "undefinedtable",
            "no such column",
            "no such table",
            "does not exist",
            "nie istnieje",
            "undefined column",
            "undefined table",
        )
        return any(pattern in text for pattern in patterns)

    def _document_candidates(self, submission: FormSubmission) -> list[dict]:
        candidates = []
        for field_name, (document_id, document_type, storage_document_type, signed) in DOCUMENT_TYPES.items():
            filename = str(getattr(submission, field_name, "") or "").strip()
            if filename:
                candidates.append(
                    {
                        "document_id": document_id,
                        "document_type": document_type,
                        "filename": Path(filename).name,
                        "training_key": "",
                        "storage_path": self._safe_storage_path(submission.form_slug, filename, storage_document_type, signed),
                    }
                )
        for index, agreement in enumerate(self._json_list(submission.training_agreements), start=1):
            training_key = str(agreement.get("training_id") or agreement.get("id") or f"training_{index}")
            filename = str(agreement.get("filename") or "").strip()
            if filename:
                candidates.append(
                    {
                        "document_id": "training_agreement",
                        "document_type": SubmissionDocumentType.TRAINING_AGREEMENT,
                        "filename": Path(filename).name,
                        "training_key": training_key,
                        "storage_path": self._safe_storage_path(submission.form_slug, filename, "agreement", False),
                    }
                )
            signed_filename = str(agreement.get("signed_filename") or "").strip()
            if signed_filename:
                candidates.append(
                    {
                        "document_id": "training_agreement",
                        "document_type": SubmissionDocumentType.SIGNED_TRAINING_AGREEMENT,
                        "filename": Path(signed_filename).name,
                        "training_key": training_key,
                        "storage_path": self._safe_storage_path(submission.form_slug, signed_filename, "agreement", True),
                    }
                )
        return candidates

    def _safe_storage_path(self, slug: str, filename: str, document_type: str, signed: bool) -> str:
        try:
            return resolve_pdf_storage_path(
                output_dir=self.output_dir,
                slug=slug,
                filename=filename,
                document_type=document_type,
                signed=signed,
            )
        except ValueError:
            return ""

    def _legacy_decision(self, submission: FormSubmission) -> str:
        decision = str(submission.officer_decision or "").strip()
        if decision:
            return decision
        status = str(submission.process_status or "").upper()
        if "REJECTED" in status or str(submission.akceptacja or "").strip().lower() == "nie":
            return "rejected"
        if "ACCEPTED" in status or str(submission.akceptacja or "").strip().lower() == "tak":
            return "accepted"
        return ""

    def _json_list(self, value: Any) -> list[dict]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        raw = str(value or "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    def _fallback(
        self,
        report: dict,
        submission: FormSubmission,
        area: str,
        fallback_type: str,
        candidate: dict | None,
        reason: str,
    ) -> None:
        report["fallback_records"].append(
            {
                "submission_id": submission.submission_id,
                "area": area,
                "fallback_type": fallback_type,
                "document_type": (candidate or {}).get("document_type"),
                "filename": (candidate or {}).get("filename"),
                "reason": reason,
            }
        )

    def _local_file_exists(self, storage_path: str) -> bool:
        return Path(storage_path).exists()
