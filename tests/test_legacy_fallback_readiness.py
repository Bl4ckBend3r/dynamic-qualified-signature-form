import json

import pytest

pytest.importorskip("sqlalchemy")

from database import create_session_factory
from models import Base, FormSubmission, SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent
from scripts.check_legacy_fallback_readiness import build_parser, main, write_report
from services.legacy_fallback_readiness_service import LegacyFallbackReadinessService
from services.legacy_fallback_report_service import LegacyFallbackReportService
from services.submission_document_service import SubmissionDocumentType
from sqlalchemy import create_engine


def make_database(tmp_path, name="readiness.db"):
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


def test_documents_readiness_is_ready_when_metadata_and_file_exist(tmp_path):
    _, session_factory = make_database(tmp_path)
    with session_factory() as db:
        submission_id = add_submission(db, imiona="Jan", pesel="12345678901", pdf_filename="formularz.pdf")
        submission = db.get(FormSubmission, submission_id)
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
        db.commit()

        service = LegacyFallbackReadinessService(
            LegacyFallbackReportService(file_exists=lambda path: True)
        )
        report = service.build_readiness_report(db, area="documents")

    assert report["ready"] is True
    assert report["areas"]["documents"]["blocking_fallback_records"] == 0
    assert "12345678901" not in json.dumps(report)
    assert "Jan" not in json.dumps(report)


def test_documents_readiness_detects_missing_metadata_storage_path_and_file(tmp_path):
    _, session_factory = make_database(tmp_path)
    with session_factory() as db:
        missing_metadata_id = add_submission(db, submission_id="missing-metadata", pdf_filename="a.pdf")
        missing_path_id = add_submission(db, submission_id="missing-path", pdf_filename="b.pdf")
        missing_file_id = add_submission(db, submission_id="missing-file", pdf_filename="c.pdf")
        missing_path = db.get(FormSubmission, missing_path_id)
        missing_file = db.get(FormSubmission, missing_file_id)
        db.add(
            SubmissionFile(
                submission_id=missing_path.id,
                public_submission_id=missing_path.submission_id,
                form_slug=missing_path.form_slug,
                document_type=SubmissionDocumentType.FORM_PDF,
                filename="b.pdf",
                storage_path="",
            )
        )
        db.add(
            SubmissionFile(
                submission_id=missing_file.id,
                public_submission_id=missing_file.submission_id,
                form_slug=missing_file.form_slug,
                document_type=SubmissionDocumentType.FORM_PDF,
                filename="c.pdf",
                storage_path="output/sample/pdf/c.pdf",
            )
        )
        db.commit()

        service = LegacyFallbackReadinessService(LegacyFallbackReportService(file_exists=lambda path: False))
        report = service.build_readiness_report(db, area="documents")

    assert missing_metadata_id
    assert report["ready"] is False
    assert report["areas"]["documents"]["missing_metadata"] == 1
    assert report["areas"]["documents"]["missing_storage_path"] == 1
    assert report["areas"]["documents"]["missing_physical_file"] == 1


def test_workflow_and_decision_readiness_detect_blockers(tmp_path):
    _, session_factory = make_database(tmp_path)
    with session_factory() as db:
        workflow_id = add_submission(db, submission_id="workflow-missing", process_status="FORM_SUBMITTED")
        decision_id = add_submission(db, submission_id="legacy-decision", officer_decision="accepted")
        ready_id = add_submission(db, submission_id="ready", process_status="FORM_SUBMITTED")
        ready = db.get(FormSubmission, ready_id)
        decision = db.get(FormSubmission, decision_id)
        db.add(
            SubmissionWorkflowEvent(
                submission_id=ready.id,
                public_submission_id=ready.submission_id,
                form_slug=ready.form_slug,
                new_status="FORM_SUBMITTED",
                source="test",
            )
        )
        db.add(
            SubmissionDecision(
                submission_id=decision.id,
                public_submission_id=decision.submission_id,
                form_slug=decision.form_slug,
                decision="accepted",
            )
        )
        db.commit()

        service = LegacyFallbackReadinessService()
        workflow_report = service.build_readiness_report(db, area="workflow", submission_id="workflow-missing")
        decision_report = service.build_readiness_report(db, area="decisions", submission_id="legacy-decision")
        ready_workflow = service.build_readiness_report(db, area="workflow", submission_id="ready")

    assert workflow_id
    assert workflow_report["ready"] is False
    assert workflow_report["areas"]["workflow"]["missing_events"] == 1
    assert decision_report["ready"] is True
    assert ready_workflow["ready"] is True


def test_decision_readiness_blocks_legacy_decision_without_submission_decision(tmp_path):
    _, session_factory = make_database(tmp_path)
    with session_factory() as db:
        add_submission(db, submission_id="legacy-only", officer_decision="accepted")

        report = LegacyFallbackReadinessService().build_readiness_report(db, area="decisions")

    assert report["ready"] is False
    assert report["areas"]["decisions"]["missing_decisions"] == 1


def test_readiness_cli_parser_and_exit_codes(tmp_path, capsys, monkeypatch):
    args = build_parser().parse_args(["--area", "documents", "--database-url", "sqlite:///x.db"])
    assert args.area == "documents"
    assert args.database_url == "sqlite:///x.db"

    ready_url, _ = make_database(tmp_path, "ready.db")
    assert main(["--area", "documents", "--database-url", ready_url]) == 0

    blocked_url, session_factory = make_database(tmp_path, "blocked.db")
    with session_factory() as db:
        add_submission(db, pdf_filename="formularz.pdf")

    report_path = tmp_path / "readiness.json"
    assert main(["--area", "documents", "--database-url", blocked_url, "--report", str(report_path)]) == 1
    assert json.loads(report_path.read_text(encoding="utf-8"))["ready"] is False
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["--area", "documents"]) == 2

    captured = capsys.readouterr()
    assert "DATABASE_URL is required" in captured.err


def test_readiness_rollout_recommendations_and_cli_report(tmp_path):
    database_url, session_factory = make_database(tmp_path, "rollout.db")
    with session_factory() as db:
        ready_id = add_submission(db, submission_id="ready-doc", pdf_filename="formularz.pdf")
        ready = db.get(FormSubmission, ready_id)
        db.add(
            SubmissionFile(
                submission_id=ready.id,
                public_submission_id=ready.submission_id,
                form_slug=ready.form_slug,
                document_type=SubmissionDocumentType.FORM_PDF,
                filename="formularz.pdf",
                storage_path="output/sample/pdf/formularz.pdf",
            )
        )
        add_submission(db, submission_id="workflow-blocked", process_status="FORM_SUBMITTED")
        db.commit()

        plan = LegacyFallbackReadinessService(
            LegacyFallbackReportService(file_exists=lambda path: True)
        ).build_rollout_plan(db)

    assert plan["recommendations"]["documents"]["recommended_action"] == "enable_strict"
    assert plan["recommendations"]["workflow"]["recommended_action"] == "keep_fallback"
    assert plan["recommendations"]["decisions"]["recommended_action"] == "enable_strict"
    assert "12345678901" not in json.dumps(plan)

    report_path = tmp_path / "rollout.json"
    assert main(["--area", "all", "--recommend", "--database-url", database_url, "--report", str(report_path)]) == 1
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["recommendations"]["workflow"]["recommended_action"] == "keep_fallback"


def test_write_report_creates_parent_directory(tmp_path):
    report_path = tmp_path / "nested" / "readiness.json"

    write_report({"ready": True}, str(report_path))

    assert json.loads(report_path.read_text(encoding="utf-8"))["ready"] is True
