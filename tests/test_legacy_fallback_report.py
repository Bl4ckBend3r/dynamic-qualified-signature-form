import json

import pytest

pytest.importorskip("sqlalchemy")

from database import create_session_factory
from models import Base, FormSubmission, SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent
from scripts.report_legacy_fallbacks import build_parser, write_report
from services.legacy_fallback_report_service import LegacyFallbackReportService
from services.submission_document_service import SubmissionDocumentType
from sqlalchemy import create_engine


def make_session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'fallbacks.db'}"
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    return create_session_factory(database_url)


def add_submission(db, **kwargs):
    submission = FormSubmission(
        submission_id=kwargs.pop("submission_id", "abc"),
        form_slug=kwargs.pop("form_slug", "sample"),
        form_name=kwargs.pop("form_name", "Sample"),
        **kwargs,
    )
    db.add(submission)
    db.commit()
    return submission.id


def test_report_counts_new_metadata_and_legacy_fallbacks(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        submission_id = add_submission(db, pdf_filename="formularz.pdf", declaration_filename="deklaracja.pdf")
        submission = db.get(FormSubmission, submission_id)
        db.add(
            SubmissionFile(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                document_id="form_submission",
                document_type=SubmissionDocumentType.FORM_PDF,
                filename="formularz.pdf",
                storage_path="output/sample/pdf/formularz.pdf",
            )
        )
        db.add(
            SubmissionWorkflowEvent(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                new_status="FORM_SUBMITTED",
                source="backfill",
            )
        )
        db.commit()

        report = LegacyFallbackReportService(file_exists=lambda path: True).build_fallback_report(db)

    assert report["documents"]["using_new_metadata"] == 1
    assert report["documents"]["missing_submission_file"] == 1
    assert report["workflow"]["using_events"] == 1
    assert report["decisions"]["missing_decision"] == 1
    assert report["fallback_records"][0]["submission_id"] == "abc"
    assert "pesel" not in json.dumps(report).lower()


def test_report_detects_missing_storage_path_and_physical_file(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        first_id = add_submission(db, submission_id="no-path", pdf_filename="a.pdf")
        first = db.get(FormSubmission, first_id)
        second_id = add_submission(db, submission_id="missing-file", pdf_filename="b.pdf")
        second = db.get(FormSubmission, second_id)
        db.add(
            SubmissionFile(
                submission_id=first.id,
                public_submission_id=first.submission_id,
                form_slug=first.form_slug,
                document_type=SubmissionDocumentType.FORM_PDF,
                filename="a.pdf",
                storage_path="",
            )
        )
        db.add(
            SubmissionFile(
                submission_id=second.id,
                public_submission_id=second.submission_id,
                form_slug=second.form_slug,
                document_type=SubmissionDocumentType.FORM_PDF,
                filename="b.pdf",
                storage_path="output/sample/pdf/b.pdf",
            )
        )
        db.commit()

        report = LegacyFallbackReportService(file_exists=lambda path: False).build_fallback_report(db)

    assert report["documents"]["missing_storage_path"] == 1
    assert report["documents"]["missing_physical_file"] == 1


def test_report_detects_workflow_and_decision_legacy(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        add_submission(
            db,
            submission_id="legacy",
            process_status="OFFICER_ACCEPTED",
            workflow_step="officer_review",
            officer_decision="accepted",
            officer_decision_email_sent="Tak",
        )
        report = LegacyFallbackReportService(file_exists=lambda path: True).build_fallback_report(db)

    assert report["workflow"]["missing_events"] == 1
    assert report["decisions"]["using_legacy_fallback"] == 1
    assert report["fallback_records"][0]["area"] in {"workflow", "decisions"}


def test_report_limit_submission_id_and_json_file(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        add_submission(db, submission_id="a", pdf_filename="a.pdf")
        add_submission(db, submission_id="b", pdf_filename="b.pdf")
        service = LegacyFallbackReportService(file_exists=lambda path: True)
        limited = service.build_fallback_report(db, limit=1)
        selected = service.build_fallback_report(db, submission_id="b")

    report_path = tmp_path / "report.json"
    write_report(selected, str(report_path))

    assert limited["processed_submissions"] == 1
    assert selected["processed_submissions"] == 1
    assert json.loads(report_path.read_text(encoding="utf-8"))["processed_submissions"] == 1


def test_report_strict_modes_are_disabled_by_default_and_testable(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        add_submission(db, pdf_filename="formularz.pdf", officer_decision="accepted")
        relaxed = LegacyFallbackReportService(file_exists=lambda path: True).build_fallback_report(db)
        strict = LegacyFallbackReportService(
            file_exists=lambda path: True,
            strict_document_metadata_read=True,
            strict_workflow_history_read=True,
            strict_decision_audit_read=True,
        ).build_fallback_report(db)

    assert relaxed["documents"]["missing_submission_file"] == 1
    assert strict["documents"]["errors"] >= 1


def test_report_strict_workflow_and_decision_modes_are_testable(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        workflow_id = add_submission(db, submission_id="workflow-only", process_status="FORM_SUBMITTED")
        decision_id = add_submission(db, submission_id="decision-only", officer_decision="accepted")
        decision_submission = db.get(FormSubmission, decision_id)
        db.add(
            SubmissionWorkflowEvent(
                submission_id=decision_submission.id,
                public_submission_id=decision_submission.submission_id,
                form_slug=decision_submission.form_slug,
                new_status="FORM_SUBMITTED",
                source="test",
            )
        )
        db.commit()
        workflow_report = LegacyFallbackReportService(strict_workflow_history_read=True).build_fallback_report(
            db,
            submission_id="workflow-only",
        )
        decision_report = LegacyFallbackReportService(strict_decision_audit_read=True).build_fallback_report(
            db,
            submission_id="decision-only",
        )

    assert workflow_id
    assert workflow_report["workflow"]["errors"] == 1
    assert decision_report["decisions"]["errors"] == 1


def test_report_cli_parser_supports_filters_and_database_url():
    args = build_parser().parse_args(["--limit", "10", "--submission-id", "abc", "--database-url", "sqlite:///x.db"])
    assert args.limit == 10
    assert args.submission_id == "abc"
    assert args.database_url == "sqlite:///x.db"


def test_report_rolls_back_session_and_marks_schema_mismatch_on_sql_error():
    class BrokenQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            raise RuntimeError("psycopg.errors.UndefinedColumn: kolumna submission_files.original_filename nie istnieje")

    class FakeSession:
        def __init__(self):
            self.rollbacks = 0

        def query(self, model):
            return BrokenQuery()

        def rollback(self):
            self.rollbacks += 1

    db = FakeSession()
    service = LegacyFallbackReportService(file_exists=lambda path: True)
    fallback_report = {
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
        "workflow": {"using_events": 0, "using_legacy_fallback": 0, "missing_events": 0, "normalized_statuses": 0, "errors": 0},
        "decisions": {"using_submission_decision": 0, "using_legacy_fallback": 0, "missing_decision": 0, "ambiguous": 0, "errors": 0},
        "fallback_records": [],
        "errors": [],
    }
    submission = FormSubmission(submission_id="abc", form_slug="sample", form_name="Sample", pdf_filename="formularz.pdf")
    submission.id = 1

    service.scan_documents(db, submission, fallback_report)

    assert db.rollbacks == 1
    assert fallback_report["schema_mismatch"] is True
    assert fallback_report["documents"]["errors"] == 1
    assert fallback_report["errors"][0]["category"] == "schema_mismatch"
    assert "InFailedSqlTransaction" not in fallback_report["errors"][0]["message"]
