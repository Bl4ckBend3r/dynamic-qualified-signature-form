from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import Base, FormSubmission, SubmissionFile, SubmissionWorkflowEvent
from services.process_service import build_process_state
from services.submission_document_service import SubmissionDocumentService
from services.submission_stage_rollback_service import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    SUPERSEDED_FILE_STATUS,
    StageRollbackError,
    SubmissionStageRollbackService,
)


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rollback.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def add_submission(db, **overrides):
    values = {
        "submission_id": "rollback-uuid",
        "form_slug": "sample_form",
        "process_status": "AGREEMENT_WAITING_FOR_SIGNATURE",
        "workflow_step": "agreement_signature",
        "officer_decision": "accepted",
        "declaration_required": "Tak",
        "declaration_generated": "Tak",
        "declaration_filename": "deklaracja.pdf",
        "declaration_signed": "Tak",
        "declaration_signature_valid": "Tak",
        "declaration_signed_filename": "deklaracja-podpisana.pdf",
        "agreement_required": "Tak",
        "agreement_blocked": "Tak",
        "agreement_block_reason": "Historyczna blokada",
        "agreement_generated": "Tak",
        "agreement_filename": "umowa.pdf",
        "agreement_signed": "Tak",
        "agreement_signature_valid": "Tak",
        "agreement_signed_filename": "umowa-podpisana.pdf",
        "training_agreements": '[{"id":"one","filename":"umowa.pdf","signed_filename":"umowa-podpisana.pdf"}]',
        "additional_fields_completed": "Tak",
        "requirements_rejection_email_sent": "Tak",
    }
    values.update(overrides)
    submission = FormSubmission(**values)
    db.add(submission)
    db.flush()
    return submission


def actor(role):
    return SimpleNamespace(id=17, email=f"{role}@example.com", role=role)


def test_admin_can_rollback_only_one_logical_stage(db):
    submission = add_submission(db)
    service = SubmissionStageRollbackService()

    targets = service.get_allowed_targets(db, submission, actor_role=ROLE_ADMIN)

    assert [target.status for target in targets] == ["DECLARATION_SIGNED"]


def test_super_admin_can_choose_any_earlier_stage_and_final_is_super_admin_only(db):
    submission = add_submission(db, process_status="PROCESS_COMPLETED")
    service = SubmissionStageRollbackService()

    assert service.get_allowed_targets(db, submission, actor_role=ROLE_ADMIN) == []
    targets = service.get_allowed_targets(db, submission, actor_role=ROLE_SUPER_ADMIN)

    assert targets[0].status == "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE"
    assert "OFFICER_ACCEPTED" in {target.status for target in targets}
    assert "FORM_SUBMITTED" in {target.status for target in targets}


def test_rollback_rejects_future_status_and_requires_reason(db):
    submission = add_submission(db, process_status="DECLARATION_WAITING_FOR_SIGNATURE")
    service = SubmissionStageRollbackService()

    with pytest.raises(StageRollbackError, match="Powód"):
        service.rollback(
            db,
            submission,
            target_status="OFFICER_ACCEPTED",
            reason="",
            actor=actor(ROLE_ADMIN),
        )
    with pytest.raises(StageRollbackError, match="dozwolonym"):
        service.rollback(
            db,
            submission,
            target_status="AGREEMENT_WAITING_FOR_SIGNATURE",
            reason="Błędny wybór",
            actor=actor(ROLE_SUPER_ADMIN),
        )


def test_rollback_resets_later_fields_supersedes_files_and_records_audit(db):
    submission = add_submission(db)
    for document_type, filename in (
        ("declaration", "deklaracja.pdf"),
        ("signed_declaration", "deklaracja-podpisana.pdf"),
        ("training_agreement", "umowa.pdf"),
        ("signed_training_agreement", "umowa-podpisana.pdf"),
    ):
        db.add(
            SubmissionFile(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                document_id=document_type,
                document_type=document_type,
                filename=filename,
                storage_path=f"output/sample_form/{filename}",
                status="signed" if document_type.startswith("signed_") else "generated",
            )
        )
    db.flush()
    service = SubmissionStageRollbackService()

    result = service.rollback(
        db,
        submission,
        target_status="OFFICER_ACCEPTED",
        reason="Ponowna weryfikacja danych",
        actor=actor(ROLE_SUPER_ADMIN),
    )
    db.commit()

    assert result.superseded_files == 4
    assert submission.process_status == "OFFICER_ACCEPTED"
    assert submission.declaration_generated == ""
    assert submission.declaration_signed_filename == ""
    assert submission.agreement_generated == ""
    assert submission.agreement_blocked == ""
    assert submission.agreement_block_reason == ""
    assert submission.training_agreements == ""
    assert submission.additional_fields_completed == ""
    assert submission.requirements_rejection_email_sent == ""
    assert {row.status for row in db.scalars(select(SubmissionFile)).all()} == {SUPERSEDED_FILE_STATUS}
    event = db.scalar(select(SubmissionWorkflowEvent))
    assert event.submission_id == submission.id
    assert event.public_submission_id == submission.submission_id
    assert event.previous_status == "AGREEMENT_WAITING_FOR_SIGNATURE"
    assert event.new_status == "OFFICER_ACCEPTED"
    assert event.actor_id == 17
    assert event.actor_role == ROLE_SUPER_ADMIN
    assert event.reason == "Ponowna weryfikacja danych"
    assert event.source == "stage_rollback"


def test_rollback_to_review_clears_officer_decision_and_mail_flags(db):
    submission = add_submission(
        db,
        process_status="OFFICER_ACCEPTED",
        officer_decision_email_sent="Tak",
        decision_email_sent="Tak",
        acceptance_email_sent="Tak",
    )
    service = SubmissionStageRollbackService()

    service.rollback(
        db,
        submission,
        target_status="WAITING_FOR_OFFICER_DECISION",
        reason="Decyzja wymaga ponownej analizy",
        actor=actor(ROLE_ADMIN),
    )

    assert submission.officer_decision == ""
    assert submission.officer_decision_email_sent == ""
    assert submission.decision_email_sent == ""
    assert submission.acceptance_email_sent == ""


def test_non_admin_role_is_rejected_by_service(db):
    submission = add_submission(db)

    with pytest.raises(PermissionError):
        SubmissionStageRollbackService().get_allowed_targets(db, submission, actor_role="form_manager")


def test_explicit_rollback_status_is_authoritative_over_stale_document_flags():
    state = build_process_state(
        {
            "process_status": "OFFICER_ACCEPTED",
            "officer_decision": "accepted",
            "agreement_signed": "Tak",
            "agreement_signature_valid": "Tak",
        }
    )

    assert state.status.value == "OFFICER_ACCEPTED"


def test_superseded_document_is_not_returned_as_current_available_file():
    class Repository:
        def list_submission_files(self, _submission_id):
            return [
                {
                    "filename": "old.pdf",
                    "storage_path": "output/sample_form/pdf/old.pdf",
                    "status": SUPERSEDED_FILE_STATUS,
                },
                {
                    "filename": "current.pdf",
                    "storage_path": "output/sample_form/pdf/current.pdf",
                    "status": "generated",
                },
            ]

    class Storage:
        def exists(self, _path):
            return True

    service = SubmissionDocumentService(submission_repository=Repository(), storage=Storage())

    available = service.list_available_documents(
        {"submission_id": "rollback-uuid", "form_slug": "sample_form"}
    )

    assert [item["filename"] for item in available] == ["current.pdf"]
