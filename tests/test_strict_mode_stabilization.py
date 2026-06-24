import json

import pytest

pytest.importorskip("sqlalchemy")

from database import create_session_factory
from models import Base, FormSubmission, SubmissionFile, SubmissionWorkflowEvent
from scripts.report_strict_mode_stabilization import build_parser, main, write_report
from services.legacy_fallback_report_service import LegacyFallbackReportService
from services.strict_mode_stabilization_service import StrictModeStabilizationService
from services.submission_document_service import SubmissionDocumentType
from sqlalchemy import create_engine


def make_database(tmp_path, name="stabilization.db"):
    database_url = f"sqlite:///{tmp_path / name}"
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    return database_url, create_session_factory(database_url)


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


def add_document_metadata(db, submission):
    db.add(
        SubmissionFile(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug=submission.form_slug,
            document_type=SubmissionDocumentType.FORM_PDF,
            filename="formularz.pdf",
            storage_path="output/sample/pdf/formularz.pdf",
        )
    )


def add_workflow_event(db, submission):
    db.add(
        SubmissionWorkflowEvent(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug=submission.form_slug,
            new_status="FORM_SUBMITTED",
            source="test",
        )
    )


def test_stabilization_report_shows_documents_workflow_and_decisions(tmp_path):
    _, session_factory = make_database(tmp_path)
    with session_factory() as db:
        ready_id = add_submission(
            db,
            submission_id="ready",
            imiona="Jan",
            pesel="12345678901",
            pdf_filename="formularz.pdf",
        )
        ready = db.get(FormSubmission, ready_id)
        add_document_metadata(db, ready)
        add_workflow_event(db, ready)
        db.commit()

        service = StrictModeStabilizationService(
            report_service=LegacyFallbackReportService(file_exists=lambda path: True),
            strict_flags={
                "STRICT_DOCUMENT_METADATA_READ": False,
                "STRICT_WORKFLOW_HISTORY_READ": False,
                "STRICT_DECISION_AUDIT_READ": False,
            },
        )
        report = service.build_stabilization_report(db)

    assert set(report["areas"]) == {"documents", "workflow", "decisions"}
    assert report["areas"]["documents"]["recommended_action"] == "enable_strict"
    assert report["areas"]["workflow"]["recommended_action"] == "enable_strict"
    assert report["areas"]["decisions"]["recommended_action"] == "enable_strict"
    assert "12345678901" not in json.dumps(report)
    assert "Jan" not in json.dumps(report)


def test_stabilization_recommendations_cover_keep_stabilize_and_removal(tmp_path):
    _, session_factory = make_database(tmp_path)
    with session_factory() as db:
        blocked_id = add_submission(db, submission_id="blocked", pdf_filename="formularz.pdf")
        blocked = db.get(FormSubmission, blocked_id)
        service = StrictModeStabilizationService(
            report_service=LegacyFallbackReportService(file_exists=lambda path: True),
            strict_flags={
                "STRICT_DOCUMENT_METADATA_READ": False,
                "STRICT_WORKFLOW_HISTORY_READ": True,
                "STRICT_DECISION_AUDIT_READ": True,
            },
        )
        blocked_report = service.build_stabilization_report(db)

        add_document_metadata(db, blocked)
        add_workflow_event(db, blocked)
        db.commit()
        stable_report = service.build_stabilization_report(db, submission_id="blocked")

    assert blocked_report["areas"]["documents"]["recommended_action"] == "keep_fallback"
    assert blocked_report["areas"]["workflow"]["recommended_action"] == "stabilize"
    assert blocked_report["areas"]["workflow"]["strict_events_detected"] == 1
    assert stable_report["areas"]["workflow"]["recommended_action"] == "ready_for_legacy_removal"
    assert stable_report["areas"]["workflow"]["migration_candidate"] is True
    assert stable_report["areas"]["decisions"]["recommended_action"] == "ready_for_legacy_removal"


def test_stabilization_cli_filters_and_writes_json_without_writes(tmp_path, monkeypatch):
    database_url, session_factory = make_database(tmp_path)
    with session_factory() as db:
        add_submission(db, submission_id="a", pdf_filename="a.pdf")
        add_submission(db, submission_id="b", pdf_filename="b.pdf")
        before_count = db.query(FormSubmission).count()

    args = build_parser().parse_args(["--area", "documents", "--limit", "1", "--database-url", database_url])
    assert args.area == "documents"
    assert args.limit == 1
    assert args.database_url == database_url

    monkeypatch.setenv("STRICT_DOCUMENT_METADATA_READ", "true")
    report_path = tmp_path / "reports" / "strict_stabilization.json"
    assert main(
        [
            "--area",
            "documents",
            "--limit",
            "1",
            "--database-url",
            database_url,
            "--report",
            str(report_path),
        ]
    ) == 0
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(saved["areas"]) == {"documents"}
    assert saved["processed_submissions"] == 1
    assert saved["areas"]["documents"]["recommended_action"] == "stabilize"

    with session_factory() as db:
        assert db.query(FormSubmission).count() == before_count


def test_stabilization_cli_submission_id_and_missing_database_url(tmp_path, monkeypatch, capsys):
    database_url, session_factory = make_database(tmp_path)
    with session_factory() as db:
        add_submission(db, submission_id="a", pdf_filename="a.pdf")
        add_submission(db, submission_id="b", pdf_filename="b.pdf")

    assert main(["--area", "documents", "--submission-id", "b", "--database-url", database_url]) == 0
    captured = capsys.readouterr()
    assert '"processed_submissions": 1' in captured.out

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["--area", "documents"]) == 2
    captured = capsys.readouterr()
    assert "DATABASE_URL is required" in captured.err


def test_write_report_creates_parent_directory(tmp_path):
    report_path = tmp_path / "nested" / "strict.json"

    write_report({"ready_for_legacy_removal": False}, str(report_path))

    assert json.loads(report_path.read_text(encoding="utf-8"))["ready_for_legacy_removal"] is False


def test_stabilization_blocks_legacy_removal_on_schema_mismatch():
    class SchemaMismatchReportService:
        def build_fallback_report(self, db, *, limit=None, submission_id=None):
            return {
                "processed_submissions": 1,
                "schema_mismatch": True,
                "documents": {
                    "using_new_metadata": 0,
                    "using_legacy_fallback": 0,
                    "missing_submission_file": 0,
                    "missing_storage_path": 0,
                    "missing_physical_file": 0,
                    "ambiguous": 0,
                    "errors": 1,
                },
                "workflow": {"using_events": 0, "using_legacy_fallback": 0, "missing_events": 0, "normalized_statuses": 0, "errors": 0},
                "decisions": {"using_submission_decision": 0, "using_legacy_fallback": 0, "missing_decision": 0, "ambiguous": 0, "errors": 0},
                "fallback_records": [],
                "errors": [
                    {
                        "submission_id": "abc",
                        "area": "documents",
                        "category": "schema_mismatch",
                        "error_type": "ProgrammingError",
                        "message": "UndefinedColumn: original_filename",
                    }
                ],
            }

        def summarize_fallback_usage(self, report):
            return {"documents": 0, "workflow": 0, "decisions": 0, "total_fallback_records": 0}

    service = StrictModeStabilizationService(
        report_service=SchemaMismatchReportService(),
        strict_flags={
            "STRICT_DOCUMENT_METADATA_READ": True,
            "STRICT_WORKFLOW_HISTORY_READ": True,
            "STRICT_DECISION_AUDIT_READ": True,
        },
    )

    report = service.build_stabilization_report(object(), area="documents")

    assert report["ready_for_legacy_removal"] is False
    assert report["requires_schema_upgrade"] is True
    assert report["areas"]["documents"]["requires_schema_upgrade"] is True
    assert report["areas"]["documents"]["recommended_action"] == "keep_fallback"
