import json
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from database import create_session_factory
from models import Base, FormSubmission, SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent
from scripts.backfill_p4_metadata import BackfillP4Metadata, build_parser, write_report
from services.submission_document_service import SubmissionDocumentType
from sqlalchemy import create_engine


def make_session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'backfill.db'}"
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


def test_backfill_defaults_to_dry_run_and_does_not_write(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        add_submission(db, pdf_filename="formularz.pdf", process_status="FORM_SUBMITTED")

    report = BackfillP4Metadata(session_factory, file_exists=lambda path: True).run()

    assert report["dry_run"] is True
    assert report["documents"]["created"] == 1
    with session_factory() as db:
        assert db.query(SubmissionFile).count() == 0
        assert db.query(SubmissionWorkflowEvent).count() == 0


def test_backfill_apply_creates_documents_workflow_and_decision(tmp_path):
    session_factory = make_session_factory(tmp_path)
    agreements = [
        {
            "id": "excel",
            "number": "ABC/1",
            "generated_at": "2026-06-10",
            "filename": "excel-umowa.pdf",
            "signed_filename": "excel-umowa-signed.pdf",
            "signature_valid": "Tak",
        }
    ]
    with session_factory() as db:
        add_submission(
            db,
            pdf_filename="formularz.pdf",
            signed_pdf_filename="formularz-signed.pdf",
            declaration_filename="deklaracja.pdf",
            declaration_signed_filename="deklaracja-signed.pdf",
            agreement_filename="umowa.pdf",
            agreement_signed_filename="umowa-signed.pdf",
            training_agreements=json.dumps(agreements),
            officer_decision="accepted",
            officer_decision_email_sent="Tak",
            process_status="OFFICER_ACCEPTED",
            workflow_step="officer_review",
        )

    report = BackfillP4Metadata(session_factory, apply=True, file_exists=lambda path: True).run()

    assert report["dry_run"] is False
    assert report["documents"]["created"] == 8
    assert report["workflow_events"]["created"] == 1
    assert report["decisions"]["created"] == 1
    with session_factory() as db:
        files = db.query(SubmissionFile).all()
        assert len(files) == 8
        types = {file.document_type for file in files}
        assert SubmissionDocumentType.FORM_PDF in types
        assert SubmissionDocumentType.SIGNED_TRAINING_AGREEMENT in types
        training_file = db.query(SubmissionFile).filter_by(document_type=SubmissionDocumentType.TRAINING_AGREEMENT).one()
        assert training_file.training_key == "excel"
        assert training_file.agreement_number == "ABC/1"
        decision = db.query(SubmissionDecision).one()
        assert decision.email_sent is True
        assert db.query(SubmissionWorkflowEvent).one().source == "backfill"


def test_backfill_is_idempotent(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        add_submission(db, pdf_filename="formularz.pdf", process_status="FORM_SUBMITTED")
    runner = BackfillP4Metadata(session_factory, apply=True, file_exists=lambda path: True)

    first = runner.run()
    second = runner.run()

    assert first["documents"]["created"] == 1
    assert second["documents"]["skipped_existing"] == 1
    assert second["workflow_events"]["skipped_existing"] == 1
    with session_factory() as db:
        assert db.query(SubmissionFile).count() == 1
        assert db.query(SubmissionWorkflowEvent).count() == 1


def test_backfill_does_not_overwrite_existing_storage_path(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        submission_id = add_submission(db, pdf_filename="formularz.pdf")
        submission = db.get(FormSubmission, submission_id)
        db.add(
            SubmissionFile(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                document_id="form_submission",
                document_type=SubmissionDocumentType.FORM_PDF,
                filename="formularz.pdf",
                storage_path="custom/path/formularz.pdf",
            )
        )
        db.commit()

    report = BackfillP4Metadata(session_factory, apply=True, file_exists=lambda path: True).run()

    assert report["documents"]["updated"] == 1
    with session_factory() as db:
        file_row = db.query(SubmissionFile).one()
        assert file_row.storage_path == "custom/path/formularz.pdf"
        assert file_row.generated_at is not None


def test_backfill_reports_unsafe_filename_without_stopping(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        add_submission(db, submission_id="bad", pdf_filename="../secret.pdf")
        add_submission(db, submission_id="good", pdf_filename="formularz.pdf")

    report = BackfillP4Metadata(session_factory, apply=True, file_exists=lambda path: True).run()

    assert report["processed_submissions"] == 2
    assert report["documents"]["unsafe_path"] == 1
    assert report["documents"]["created"] == 1
    assert report["errors"][0]["submission_id"] == "bad"


def test_backfill_limit_submission_id_and_report_file(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        add_submission(db, submission_id="a", pdf_filename="a.pdf")
        add_submission(db, submission_id="b", pdf_filename="b.pdf")

    limited = BackfillP4Metadata(session_factory, file_exists=lambda path: False).run(limit=1)
    selected = BackfillP4Metadata(session_factory, file_exists=lambda path: False).run(submission_id="b")
    report_path = tmp_path / "report.json"
    write_report(selected, str(report_path))

    assert limited["processed_submissions"] == 1
    assert selected["processed_submissions"] == 1
    assert selected["documents"]["missing_file"] == 1
    assert json.loads(report_path.read_text(encoding="utf-8"))["dry_run"] is True


def test_backfill_cli_parser_defaults_to_dry_run():
    args = build_parser().parse_args([])
    assert args.apply is False


def test_backfill_decisions_skip_missing_and_existing_decisions(tmp_path):
    session_factory = make_session_factory(tmp_path)
    with session_factory() as db:
        no_decision_id = add_submission(db, submission_id="no-decision", process_status="FORM_SUBMITTED")
        decided_id = add_submission(db, submission_id="decided", officer_decision="rejected", process_status="OFFICER_REJECTED")
        decided = db.get(FormSubmission, decided_id)
        db.add(
            SubmissionDecision(
                submission_id=decided.id,
                public_submission_id=decided.submission_id,
                form_slug=decided.form_slug,
                decision="rejected",
                target_status="OFFICER_REJECTED",
                officer_email="system",
            )
        )
        db.commit()

    report = BackfillP4Metadata(session_factory, apply=True, file_exists=lambda path: True).run()

    assert report["decisions"]["created"] == 0
    assert report["decisions"]["skipped_existing"] == 1
    with session_factory() as db:
        assert db.query(SubmissionDecision).count() == 1
        assert db.get(FormSubmission, no_decision_id).officer_decision == ""
