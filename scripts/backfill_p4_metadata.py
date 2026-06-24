from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import create_session_factory
from models import EmailLog, FormSubmission, SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent
from services.document_naming_service import resolve_pdf_storage_path
from services.submission_document_service import SubmissionDocumentType


DOCUMENT_COUNTERS = ("created", "updated", "skipped_existing", "missing_file", "unsafe_path", "ambiguous", "error")
WORKFLOW_COUNTERS = ("created", "skipped_existing", "error")
DECISION_COUNTERS = ("created", "skipped_existing", "error")


def empty_report(*, dry_run: bool) -> dict:
    return {
        "dry_run": dry_run,
        "processed_submissions": 0,
        "documents": {key: 0 for key in DOCUMENT_COUNTERS},
        "workflow_events": {key: 0 for key in WORKFLOW_COUNTERS},
        "decisions": {key: 0 for key in DECISION_COUNTERS},
        "errors": [],
    }


@dataclass(frozen=True)
class DocumentCandidate:
    document_id: str
    document_type: str
    filename: str
    storage_document_type: str
    signed: bool = False
    status: str = "generated"
    original_filename: str = ""
    signature_status: str = ""
    signature_validation_result: dict | None = None
    agreement_number: str = ""
    training_key: str = ""
    generated_at: datetime | None = None
    signed_at: datetime | None = None


class BackfillP4Metadata:
    def __init__(
        self,
        session_factory,
        *,
        apply: bool = False,
        output_dir: str = "output",
        file_exists=None,
    ) -> None:
        self.session_factory = session_factory
        self.apply = apply
        self.output_dir = output_dir
        self.file_exists = file_exists or self._local_file_exists

    def run(self, *, limit: int | None = None, submission_id: str | None = None) -> dict:
        report = empty_report(dry_run=not self.apply)
        with self.session_factory() as db:
            query = db.query(FormSubmission).order_by(FormSubmission.id)
            if submission_id:
                query = query.filter(FormSubmission.submission_id == submission_id)
            submissions = query.limit(limit).all() if limit else query.all()
            for submission in submissions:
                report["processed_submissions"] += 1
                try:
                    self._backfill_submission(db, submission, report)
                except Exception as exc:
                    self._error(report, submission.submission_id, "submission", "error", str(exc))
            if self.apply:
                db.commit()
            else:
                db.rollback()
        return report

    def _backfill_submission(self, db, submission: FormSubmission, report: dict) -> None:
        for candidate in self._document_candidates(submission):
            self._backfill_document(db, submission, candidate, report)
        self._backfill_workflow_event(db, submission, report)
        self._backfill_decision(db, submission, report)

    def _backfill_document(self, db, submission: FormSubmission, candidate: DocumentCandidate, report: dict) -> None:
        if not self._safe_filename(candidate.filename):
            report["documents"]["unsafe_path"] += 1
            self._error(report, submission.submission_id, candidate.document_type, "unsafe_path", candidate.filename)
            return

        existing = self._find_document(db, submission, candidate)
        storage_path = self._resolve_storage_path(submission, candidate)
        if storage_path is None:
            report["documents"]["unsafe_path"] += 1
            self._error(report, submission.submission_id, candidate.document_type, "unsafe_path", candidate.filename)
            return
        if not self.file_exists(storage_path):
            report["documents"]["missing_file"] += 1

        if existing:
            updates = self._missing_metadata_updates(existing, candidate, storage_path)
            if not updates:
                report["documents"]["skipped_existing"] += 1
                return
            if self.apply:
                for key, value in updates.items():
                    setattr(existing, key, value)
            report["documents"]["updated"] += 1
            return

        if self.apply:
            db.add(
                SubmissionFile(
                    submission_id=submission.id,
                    public_submission_id=submission.submission_id,
                    form_slug=submission.form_slug,
                    document_id=candidate.document_id,
                    document_type=candidate.document_type,
                    filename=candidate.filename,
                    original_filename=candidate.original_filename,
                    storage_path=storage_path,
                    signed=candidate.signed,
                    status=candidate.status,
                    signature_status=candidate.signature_status,
                    signature_validation_result=candidate.signature_validation_result or {},
                    agreement_number=candidate.agreement_number,
                    training_key=candidate.training_key,
                    generated_at=candidate.generated_at,
                    signed_at=candidate.signed_at,
                )
            )
        report["documents"]["created"] += 1

    def _backfill_workflow_event(self, db, submission: FormSubmission, report: dict) -> None:
        existing = (
            db.query(SubmissionWorkflowEvent)
            .filter(SubmissionWorkflowEvent.submission_id == submission.id)
            .first()
        )
        if existing:
            report["workflow_events"]["skipped_existing"] += 1
            return
        if self.apply:
            db.add(
                SubmissionWorkflowEvent(
                    submission_id=submission.id,
                    public_submission_id=submission.submission_id,
                    form_slug=submission.form_slug,
                    previous_status="",
                    new_status=submission.process_status or "",
                    previous_step="",
                    new_step=submission.workflow_step or "",
                    actor_role="system",
                    reason="Backfill initial workflow state",
                    source="backfill",
                    created_at=submission.created_at,
                )
            )
        report["workflow_events"]["created"] += 1

    def _backfill_decision(self, db, submission: FormSubmission, report: dict) -> None:
        decision_value = self._legacy_decision(submission)
        if not decision_value:
            return
        existing = (
            db.query(SubmissionDecision)
            .filter(SubmissionDecision.submission_id == submission.id)
            .first()
        )
        if existing:
            report["decisions"]["skipped_existing"] += 1
            return
        email_log_id = self._email_log_id(db, submission)
        if self.apply:
            db.add(
                SubmissionDecision(
                    submission_id=submission.id,
                    public_submission_id=submission.submission_id,
                    form_slug=submission.form_slug,
                    decision=decision_value,
                    justification=submission.officer_decision_reason or "",
                    officer_email="system",
                    previous_status="",
                    target_status=submission.process_status or "",
                    email_requested=self._truthy(submission.officer_decision_email_requested),
                    email_sent=self._truthy(submission.officer_decision_email_sent)
                    or self._truthy(submission.decision_email_sent),
                    email_log_id=email_log_id,
                    decided_at=submission.updated_at or submission.created_at,
                )
            )
        report["decisions"]["created"] += 1

    def _document_candidates(self, submission: FormSubmission) -> list[DocumentCandidate]:
        created_at = self._as_datetime(submission.created_at)
        updated_at = self._as_datetime(submission.updated_at)
        candidates = []
        if submission.pdf_filename:
            candidates.append(
                DocumentCandidate(
                    document_id="form_submission",
                    document_type=SubmissionDocumentType.FORM_PDF,
                    filename=submission.pdf_filename,
                    storage_document_type="",
                    generated_at=created_at,
                )
            )
        if submission.signed_pdf_filename:
            candidates.append(
                DocumentCandidate(
                    document_id="form_submission",
                    document_type=SubmissionDocumentType.SIGNED_FORM_PDF,
                    filename=submission.signed_pdf_filename,
                    storage_document_type="",
                    signed=True,
                    status="signed",
                    signature_status=submission.signature_status or "",
                    signature_validation_result=self._signature_result(
                        signature_status=submission.signature_status,
                        signature_request_id=submission.signature_request_id,
                    ),
                    signed_at=updated_at,
                )
            )
        if submission.declaration_filename:
            candidates.append(
                DocumentCandidate(
                    document_id="declaration",
                    document_type=SubmissionDocumentType.DECLARATION,
                    filename=submission.declaration_filename,
                    storage_document_type="declaration",
                    generated_at=created_at,
                )
            )
        if submission.declaration_signed_filename:
            candidates.append(
                DocumentCandidate(
                    document_id="declaration",
                    document_type=SubmissionDocumentType.SIGNED_DECLARATION,
                    filename=submission.declaration_signed_filename,
                    storage_document_type="declaration",
                    signed=True,
                    status="signed",
                    signature_status=submission.declaration_signature_valid or "",
                    signature_validation_result=self._signature_result(
                        signature_type=submission.declaration_signature_type,
                        signature_valid=submission.declaration_signature_valid,
                        signature_error=submission.declaration_signature_error,
                    ),
                    signed_at=updated_at,
                )
            )
        if submission.agreement_filename:
            candidates.append(
                DocumentCandidate(
                    document_id="agreement",
                    document_type=SubmissionDocumentType.AGREEMENT,
                    filename=submission.agreement_filename,
                    storage_document_type="agreement",
                    generated_at=self._as_datetime(submission.agreement_generated_at) or created_at,
                )
            )
        if submission.agreement_signed_filename:
            candidates.append(
                DocumentCandidate(
                    document_id="agreement",
                    document_type=SubmissionDocumentType.SIGNED_AGREEMENT,
                    filename=submission.agreement_signed_filename,
                    storage_document_type="agreement",
                    signed=True,
                    status="signed",
                    signature_status=submission.agreement_signature_valid or "",
                    signature_validation_result=self._signature_result(
                        signature_type=submission.agreement_signature_type,
                        signature_valid=submission.agreement_signature_valid,
                        signature_error=submission.agreement_signature_error,
                    ),
                    signed_at=updated_at,
                )
            )
        candidates.extend(self._training_agreement_candidates(submission, created_at, updated_at))
        return candidates

    def _training_agreement_candidates(
        self,
        submission: FormSubmission,
        created_at: datetime | None,
        updated_at: datetime | None,
    ) -> list[DocumentCandidate]:
        candidates = []
        for index, agreement in enumerate(self._json_list(submission.training_agreements), start=1):
            training_key = str(agreement.get("training_id") or agreement.get("id") or f"training_{index}")
            agreement_number = str(agreement.get("number") or agreement.get("agreement_number") or "")
            generated_at = self._as_datetime(agreement.get("generated_at")) or self._as_datetime(submission.agreement_generated_at) or created_at
            filename = str(agreement.get("filename") or "").strip()
            if filename:
                candidates.append(
                    DocumentCandidate(
                        document_id="training_agreement",
                        document_type=SubmissionDocumentType.TRAINING_AGREEMENT,
                        filename=filename,
                        storage_document_type="agreement",
                        agreement_number=agreement_number,
                        training_key=training_key,
                        generated_at=generated_at,
                    )
                )
            signed_filename = str(agreement.get("signed_filename") or "").strip()
            if signed_filename:
                candidates.append(
                    DocumentCandidate(
                        document_id="training_agreement",
                        document_type=SubmissionDocumentType.SIGNED_TRAINING_AGREEMENT,
                        filename=signed_filename,
                        storage_document_type="agreement",
                        signed=True,
                        status="signed",
                        signature_status=str(agreement.get("signature_type") or agreement.get("signature_valid") or ""),
                        signature_validation_result=self._signature_result(
                            signature_type=agreement.get("signature_type"),
                            signature_valid=agreement.get("signature_valid"),
                            signature_error=agreement.get("signature_error"),
                        ),
                        agreement_number=agreement_number,
                        training_key=training_key,
                        signed_at=updated_at,
                    )
                )
        return candidates

    def _find_document(self, db, submission: FormSubmission, candidate: DocumentCandidate) -> SubmissionFile | None:
        return (
            db.query(SubmissionFile)
            .filter(SubmissionFile.submission_id == submission.id)
            .filter(SubmissionFile.document_type == candidate.document_type)
            .filter(SubmissionFile.filename == candidate.filename)
            .filter(SubmissionFile.training_key == candidate.training_key)
            .first()
        )

    def _missing_metadata_updates(
        self,
        existing: SubmissionFile,
        candidate: DocumentCandidate,
        storage_path: str,
    ) -> dict:
        updates = {}
        for key in (
            "document_id",
            "original_filename",
            "signature_status",
            "agreement_number",
            "training_key",
        ):
            value = getattr(candidate, key)
            if value and not getattr(existing, key):
                updates[key] = value
        if not existing.storage_path and storage_path:
            updates["storage_path"] = storage_path
        if not existing.signature_validation_result and candidate.signature_validation_result:
            updates["signature_validation_result"] = candidate.signature_validation_result
        if existing.generated_at is None and candidate.generated_at is not None:
            updates["generated_at"] = candidate.generated_at
        if existing.signed_at is None and candidate.signed_at is not None:
            updates["signed_at"] = candidate.signed_at
        if existing.status in {"", "uploaded"} and candidate.status:
            updates["status"] = candidate.status
        return updates

    def _resolve_storage_path(self, submission: FormSubmission, candidate: DocumentCandidate) -> str | None:
        try:
            return resolve_pdf_storage_path(
                output_dir=self.output_dir,
                slug=submission.form_slug,
                filename=candidate.filename,
                document_type=candidate.storage_document_type,
                signed=candidate.signed,
            )
        except ValueError:
            return None

    def _email_log_id(self, db, submission: FormSubmission) -> int | None:
        logs = (
            db.query(EmailLog)
            .filter(EmailLog.public_submission_id == submission.submission_id)
            .all()
        )
        return logs[0].id if len(logs) == 1 else None

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

    def _safe_filename(self, filename: str) -> bool:
        raw = str(filename or "").strip()
        if not raw or raw in {".", ".."}:
            return False
        path = Path(raw)
        return path.name == raw and not path.is_absolute() and ".." not in path.parts and "/" not in raw and "\\" not in raw

    def _local_file_exists(self, storage_path: str) -> bool:
        return Path(storage_path).exists()

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

    def _as_datetime(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, time.min, tzinfo=timezone.utc)
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _truthy(self, value: Any) -> bool:
        return str(value or "").strip().lower() in {"tak", "true", "1", "yes", "sent"}

    def _signature_result(self, **values: Any) -> dict:
        return {key: value for key, value in values.items() if value not in {None, ""}}

    def _error(self, report: dict, submission_id: str, document_type: str, error_type: str, message: str) -> None:
        report["errors"].append(
            {
                "submission_id": submission_id,
                "document_type": document_type,
                "error_type": error_type,
                "message": message,
            }
        )


def write_report(report: dict, report_path: str | None) -> None:
    if not report_path:
        return
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill P4 metadata from legacy FormSubmission fields.")
    parser.add_argument("--apply", action="store_true", help="Persist changes. Without this flag the script runs in dry-run mode.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of processed submissions.")
    parser.add_argument("--submission-id", default=None, help="Process one public submission_id.")
    parser.add_argument("--report", default=None, help="Optional JSON report path.")
    parser.add_argument("--database-url", default=None, help="Database URL. Defaults to DATABASE_URL env var.")
    parser.add_argument("--output-dir", default=os.getenv("NEXTCLOUD_OUTPUT_DIR", "output"), help="Output directory used to rebuild storage paths.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = args.database_url or os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required for P4 backfill.", file=sys.stderr)
        return 2
    session_factory = create_session_factory(database_url)
    runner = BackfillP4Metadata(
        session_factory,
        apply=bool(args.apply),
        output_dir=args.output_dir,
    )
    report = runner.run(limit=args.limit, submission_id=args.submission_id)
    write_report(report, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
