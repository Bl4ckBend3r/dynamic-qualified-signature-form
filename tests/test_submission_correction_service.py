from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import Base, FormSubmission, SubmissionFile, SubmissionWorkflowEvent
from services.process_service import ProcessStatus, build_process_state
from services.submission_correction_service import SubmissionCorrectionError, SubmissionCorrectionService


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'correction.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_submission(db):
    submission = FormSubmission(
        submission_id="public-correction-uuid",
        form_slug="sample_form",
        data_json={"email": "person@example.com", "wiek": "45", "_qualification": {"passed": False}},
        email="person@example.com",
        wiek=45,
        process_status="AUTO_REJECTED",
        workflow_step="auto_rejected",
        officer_decision="accepted",
        selected_trainings='[{"id":"training"}]',
        agreement_filename="agreement.pdf",
        agreement_generated="Tak",
        signature_status="signed",
    )
    db.add(submission)
    db.flush()
    db.add(SubmissionFile(
        submission_id=submission.id,
        public_submission_id=submission.submission_id,
        form_slug=submission.form_slug,
        document_id="training_agreement",
        document_type="training_agreement",
        filename="agreement.pdf",
        storage_path="output/sample_form/pdf/agreement.pdf",
        status="generated",
    ))
    db.flush()
    return submission


def test_return_for_correction_clears_answers_supersedes_files_and_preserves_audit(db):
    submission = make_submission(db)
    result = SubmissionCorrectionService().return_for_correction(
        db,
        submission,
        form_config={"fields": [{"name": "email"}, {"name": "wiek"}]},
        reason="Nieprawidłowe kryterium",
        message_to_user="Uzupełnij dane ponownie.",
        clear_submission=True,
        actor=SimpleNamespace(id=7, email="admin@example.com", role="admin"),
    )
    db.commit()

    assert result.recipient_email == "person@example.com"
    assert result.superseded_files == 1
    assert submission.process_status == ProcessStatus.RETURNED_FOR_CORRECTION.value
    assert submission.email == ""
    assert submission.wiek is None
    assert submission.selected_trainings == ""
    assert submission.agreement_filename == ""
    assert submission.data_json["_qualification"]["passed"] is False
    assert submission.data_json["_correction_history"][0]["reason"] == "Nieprawidłowe kryterium"
    assert db.scalar(select(SubmissionFile)).status == "superseded"
    event = db.scalar(select(SubmissionWorkflowEvent))
    assert event.submission_id == submission.id
    assert event.public_submission_id == submission.submission_id
    assert event.previous_status == "AUTO_REJECTED"
    assert event.new_status == "RETURNED_FOR_CORRECTION"
    assert event.actor_role == "admin"
    assert event.source == "returned_for_correction"


def test_return_without_clear_preserves_user_answers_but_blocks_downstream_actions(db):
    submission = make_submission(db)
    SubmissionCorrectionService().return_for_correction(
        db,
        submission,
        form_config={"fields": [{"name": "email"}, {"name": "wiek"}]},
        reason="Ponowna weryfikacja",
        message_to_user="",
        clear_submission=False,
        actor=SimpleNamespace(id=8, email="admin@example.com", role="super_admin"),
    )

    assert submission.email == "person@example.com"
    assert submission.wiek == 45
    assert submission.data_json["wiek"] == "45"
    state = build_process_state({column.name: getattr(submission, column.name) for column in submission.__table__.columns})
    assert state.can_sign_documents is False
    assert state.can_generate_agreement is False


def test_return_requires_reason_and_admin_role(db):
    submission = make_submission(db)
    service = SubmissionCorrectionService()
    with pytest.raises(SubmissionCorrectionError, match="powód"):
        service.return_for_correction(
            db, submission, form_config={}, reason="", message_to_user="", clear_submission=True,
            actor=SimpleNamespace(role="admin"),
        )
    with pytest.raises(PermissionError):
        service.return_for_correction(
            db, submission, form_config={}, reason="Powód", message_to_user="", clear_submission=True,
            actor=SimpleNamespace(role="form_manager"),
        )
