from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, FormSubmission, SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent
from services.admin_workflow_view_service import build_admin_workflow_view
from services.beneficiary_agreement_service import (
    BeneficiaryAgreementDecisionError,
    BeneficiaryAgreementService,
    can_edit_application_decision,
)
from services.process_instruction_service import build_process_instruction_view
from services.process_service import ProcessStatus
from services.documents.signed_document_service import SignedDocumentService
from services.document_service import DocumentService
from services.form_config_service import FormConfigService
from services.submission_document_service import SubmissionDocumentType


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def add_uploaded_agreement(db, *, with_file: bool = True, agreement_required: str = "Tak"):
    submission = FormSubmission(
        submission_id="agreement-review-1",
        form_slug="sample",
        form_name="Sample",
        agreement_required=agreement_required,
        agreement_signed="Tak",
        agreement_signature_valid="Tak",
        agreement_signed_filename="agreement-signed.pdf",
        process_status=ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value,
    )
    db.add(submission)
    db.flush()
    if with_file:
        db.add(
            SubmissionFile(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                document_id="agreement",
                document_type=SubmissionDocumentType.SIGNED_AGREEMENT,
                filename="agreement-signed.pdf",
                storage_path="output/sample/agreements/signed/agreement-signed.pdf",
                signed=True,
                status="signed",
            )
        )
    db.commit()
    return submission


def officer(role="admin"):
    return SimpleNamespace(id=7, email="officer@example.com", role=role)


def test_confirming_uploaded_agreement_records_decision_history_and_completes_process(db):
    submission = add_uploaded_agreement(db)

    result = BeneficiaryAgreementService().decide(
        db,
        submission,
        decision="accepted",
        reason="",
        actor=officer(),
        email_requested=True,
    )
    db.commit()

    assert result.decision_status == ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value
    assert submission.process_status == ProcessStatus.PROCESS_COMPLETED.value
    decision = db.query(SubmissionDecision).one()
    assert decision.decision == "office_agreement_accepted"
    assert decision.email_requested is True
    events = db.query(SubmissionWorkflowEvent).order_by(SubmissionWorkflowEvent.id).all()
    assert [event.source for event in events] == ["agreement_signed_by_office", "process_completed"]
    assert events[0].actor_id == 7


def test_rejecting_uploaded_agreement_requires_reason_and_allows_reupload(db):
    submission = add_uploaded_agreement(db)
    service = BeneficiaryAgreementService()

    with pytest.raises(BeneficiaryAgreementDecisionError, match="powód"):
        service.decide(db, submission, decision="rejected", reason="", actor=officer())

    service.decide(db, submission, decision="correction", reason="Brakuje strony 2.", actor=officer())
    db.commit()

    assert submission.process_status == ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE.value
    assert submission.agreement_signature_valid == ""
    assert submission.agreement_signature_error == "Brakuje strony 2."
    assert db.query(SubmissionDecision).one().justification == "Brakuje strony 2."
    assert db.query(SubmissionWorkflowEvent).one().source == "agreement_rejected_by_office"


def test_agreement_cannot_be_confirmed_without_uploaded_submission_file(db):
    submission = add_uploaded_agreement(db, with_file=False)

    with pytest.raises(BeneficiaryAgreementDecisionError, match="Nie znaleziono"):
        BeneficiaryAgreementService().decide(
            db,
            submission,
            decision="accepted",
            reason="",
            actor=officer(),
        )


def test_application_decision_is_not_editable_after_declaration_or_agreement_stage(db):
    submission = add_uploaded_agreement(db)
    assert can_edit_application_decision(submission) is False
    submission.process_status = ProcessStatus.WAITING_FOR_OFFICER_DECISION.value
    assert can_edit_application_decision(submission) is True


def test_admin_workflow_only_exposes_agreement_action_after_upload(db):
    submission = add_uploaded_agreement(db)
    view = build_admin_workflow_view(submission, can_review_agreement=True)

    assert view["can_edit_application_decision"] is False
    assert view["can_review_agreement"] is True
    agreement = next(section for section in view["sections"] if section["key"] == "agreement")
    assert agreement["state"] == "current"
    assert "Potwierdź" in agreement["action"]


def test_office_signature_action_does_not_require_optional_signature_verification_flag(db):
    submission = add_uploaded_agreement(db)
    submission.agreement_signature_valid = ""

    assert BeneficiaryAgreementService().can_review(db, submission) is True


def test_public_instruction_explains_office_signature_and_rejected_agreement_states():
    uploaded = build_process_instruction_view(ProcessStatus.AGREEMENT_UPLOADED_BY_BENEFICIARY.value)
    waiting = build_process_instruction_view(ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value)
    signed = build_process_instruction_view(ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value)
    rejected = build_process_instruction_view(ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE.value)

    assert uploaded["has_form_instruction"] is True
    assert "po stronie urzędu" in uploaded["instruction_steps"][-1]["description"]
    assert uploaded["instruction_steps"][-1]["current"] is True
    assert "Nie musisz" in waiting["instruction_steps"][-1]["description"]
    assert "podpisana przez urząd" in signed["instruction_steps"][-1]["description"]
    assert "wgraj ponownie" in rejected["next_action"]
    assert rejected["instruction_steps"][-1]["current"] is True


def test_valid_agreement_upload_waits_for_officer_instead_of_finishing_process():
    updates = SignedDocumentService().build_updates(
        {"agreement_required": "Tak"},
        "agreement",
        "agreement-signed.pdf",
        {"signature_type": "mszafir"},
        True,
        True,
        None,
    )

    assert updates["process_status"] == ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value


def test_declaration_skips_agreement_stage_when_form_does_not_require_it():
    updates = SignedDocumentService().build_updates(
        {"agreement_required": "Nie"},
        "declaration",
        "declaration-signed.pdf",
        {"signature_type": "mszafir"},
        True,
        True,
        None,
    )

    assert updates["process_status"] == ProcessStatus.PARTICIPANT_ACCEPTED.value


def test_default_workflow_places_officer_review_after_agreement_upload():
    config = FormConfigService().build_default_workflow_if_missing(
        {"documents": [{"id": "agreement", "enabled": True}]}
    )
    steps = {step["id"]: step for step in config["workflow"]["steps"]}

    assert steps["agreement_signature"]["next"] == "office_agreement_signature"
    assert steps["office_agreement_signature"]["type"] == "manual_decision"
    assert steps["office_agreement_signature"]["label"] == "Umowa podpisana przez urząd"
    assert steps["office_agreement_signature"]["decisions"]["accepted"] == "completed"


def test_legacy_uploaded_status_still_allows_office_signature_decision(db):
    submission = add_uploaded_agreement(db)
    submission.process_status = ProcessStatus.AGREEMENT_UPLOADED.value

    result = BeneficiaryAgreementService().decide(
        db, submission, decision="accepted", reason="", actor=officer()
    )

    assert result.decision_status == ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value


def test_agreement_upload_records_dedicated_workflow_event():
    class Repository:
        def __init__(self):
            self.events = []

        def record_workflow_event(self, submission_id, event):
            self.events.append((submission_id, event))
            return True

    repository = Repository()
    service = DocumentService(storage=None, submission_repository=repository)

    service._record_document_workflow_event(
        {"submission_id": "public-uuid", "form_slug": "sample", "row": {}},
        previous_status=ProcessStatus.AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE.value,
        new_status=ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value,
        document_id="agreement",
    )

    assert repository.events[0][0] == "public-uuid"
    assert repository.events[0][1]["source"] == "agreement_uploaded"
    assert repository.events[0][1]["actor_role"] == "participant"


@pytest.mark.parametrize(
    "status, expected_status, expected_action",
    [
        (ProcessStatus.AUTO_REJECTED.value, "Odrzucony automatycznie", "Dalsze kroki zablokowane"),
        (ProcessStatus.RETURNED_FOR_CORRECTION.value, "Wysłany do poprawy", "ponowne uzupełnienie"),
    ],
)
def test_admin_workflow_view_does_not_present_qualification_block_as_accepted(status, expected_status, expected_action):
    submission = SimpleNamespace(
        process_status=status,
        officer_decision="",
        declaration_required="Tak",
        agreement_required="Tak",
        declaration_signature_valid="",
        declaration_generated="",
        updated_at=None,
    )

    view = build_admin_workflow_view(submission)
    application = next(section for section in view["sections"] if section["key"] == "application")
    declaration = next(section for section in view["sections"] if section["key"] == "declaration")
    agreement = next(section for section in view["sections"] if section["key"] == "agreement")

    assert application["status"] == expected_status
    assert expected_action in application["action"]
    assert declaration["action"] == "Etap zablokowany"
    assert agreement["action"] == "Etap zablokowany"
