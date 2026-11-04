from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Base, Form, FormSubmission, SubmissionFile, SubmissionTraining, SubmissionWorkflowEvent
from services.office_signed_agreement_service import OfficeSignedAgreementService


class FakeStorage:
    output_dir = "output"

    def __init__(self, existing):
        self.existing = set(existing)

    def exists(self, path):
        return path in self.existing

    def office_signed_agreement_directory(self, slug):
        return f"output/{slug}/pdf/umowy/podpisane_przez_urzad"

    def read_bytes(self, path):
        return b"%PDF-1.7 test fixture"


def _verified_service(storage):
    return OfficeSignedAgreementService(
        storage,
        verifier=lambda payload: {
            "is_signed": True,
            "validation_status": "VALID",
            "cryptographically_valid": True,
            "integrity_ok": True,
            "trusted": True,
            "revocation_status": "good",
            "signature_type": "qualified",
        },
    )


def test_office_signed_files_are_registered_per_training_with_partial_result():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    folder = "output/sample/pdf/umowy/podpisane_przez_urzad"
    storage = FakeStorage({folder, f"{folder}/python-signed.pdf"})
    with Session(engine) as db:
        form = Form(slug="sample", name="Sample", training_selection_open=False)
        submission = FormSubmission(
            submission_id="public-1",
            form_slug="sample",
            form_name="Sample",
            access_token="token",
            process_status="AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
            workflow_step="AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
        )
        db.add_all([form, submission])
        db.flush()
        python_file = SubmissionFile(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug="sample",
            document_id="training_agreement",
            document_type="signed_training_agreement",
            file_role="participant_signed",
            storage_provider="nextcloud",
            filename="python-signed.pdf",
            storage_path="output/sample/pdf/umowy/podpisane_przez_beneficjenta/python-signed.pdf",
            training_key="agreement-python",
            signed=True,
        )
        excel_file = SubmissionFile(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug="sample",
            document_id="training_agreement",
            document_type="signed_training_agreement",
            file_role="participant_signed",
            storage_provider="nextcloud",
            filename="excel-signed.pdf",
            storage_path="output/sample/pdf/umowy/podpisane_przez_beneficjenta/excel-signed.pdf",
            training_key="agreement-excel",
            signed=True,
        )
        db.add_all([python_file, excel_file])
        db.flush()
        python = SubmissionTraining(
            submission_id=submission.id, training_id="python", training_name_snapshot="Python",
            training_price_snapshot="1000", status="agreement_waiting_for_office_signature",
            is_locked=True, agreement_id="agreement-python", agreement_file_id=python_file.id,
        )
        excel = SubmissionTraining(
            submission_id=submission.id, training_id="excel", training_name_snapshot="Excel",
            training_price_snapshot="1200", status="agreement_waiting_for_office_signature",
            is_locked=True, agreement_id="agreement-excel", agreement_file_id=excel_file.id,
        )
        future = SubmissionTraining(
            submission_id=submission.id, training_id="future", training_name_snapshot="Future",
            training_price_snapshot="800", status="selected", is_locked=False,
        )
        db.add_all([python, excel, future])
        db.flush()

        result = _verified_service(storage).check(
            db, form, submission, actor=SimpleNamespace(id=1, email="admin@example.com", role="admin")
        )

        assert result.found_count == 1
        assert result.missing_filenames == ("excel-signed.pdf",)
        assert result.training_ids == ("agreement-python",)
        assert python.status == "agreement_signed_by_office"
        assert excel.status == "agreement_waiting_for_office_signature"
        assert submission.process_status == "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE"
        final = db.query(SubmissionFile).filter_by(document_type="agreement_signed_by_office").one()
        assert final.training_key == "agreement-python"
        assert final.storage_path == f"{folder}/python-signed.pdf"
        event = db.query(SubmissionWorkflowEvent).filter_by(source="agreement_signed_by_office").one()
        assert event.side_effects["training_ids"] == ["python"]

        repeated = _verified_service(storage).check(
            db, form, submission, actor=SimpleNamespace(id=1, email="admin@example.com", role="admin")
        )
        assert repeated.found_count == 0
        assert repeated.already_confirmed == ("python-signed.pdf",)
        assert db.query(SubmissionFile).filter_by(document_type="agreement_signed_by_office").count() == 1


def test_office_signed_check_can_target_one_training_only():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    folder = "output/sample/pdf/umowy/podpisane_przez_urzad"
    storage = FakeStorage({folder, f"{folder}/python.pdf"})
    with Session(engine) as db:
        form = Form(slug="sample", name="Sample")
        submission = FormSubmission(submission_id="public-2", form_slug="sample", form_name="Sample", access_token="token")
        db.add_all([form, submission])
        db.flush()
        signed_file = SubmissionFile(
            submission_id=submission.id, public_submission_id=submission.submission_id, form_slug="sample",
            document_id="training_agreement", document_type="signed_training_agreement",
            file_role="participant_signed", storage_provider="nextcloud", filename="python.pdf",
            storage_path="beneficiary/python.pdf", training_key="python", signed=True,
        )
        db.add(signed_file)
        db.flush()
        training = SubmissionTraining(
            submission_id=submission.id, training_id="python", training_name_snapshot="Python",
            training_price_snapshot="1000", status="agreement_uploaded_by_beneficiary",
            is_locked=True, agreement_id="python", agreement_file_id=signed_file.id,
        )
        db.add(training)
        db.flush()

        result = _verified_service(storage).check(
            db, form, submission, actor=SimpleNamespace(role="admin"), training_key="python"
        )

        assert result.expected_filenames == ("python.pdf",)
        assert result.found_count == 1
        assert training.status == "agreement_signed_by_office"


def test_office_signed_check_rejects_filename_conflict_between_trainings():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    folder = "output/sample/pdf/umowy/podpisane_przez_urzad"
    storage = FakeStorage({folder, f"{folder}/same.pdf"})
    with Session(engine) as db:
        form = Form(slug="sample", name="Sample")
        submission = FormSubmission(
            submission_id="public-conflict", form_slug="sample", form_name="Sample", access_token="token"
        )
        db.add_all([form, submission])
        db.flush()
        for index, training_id in enumerate(("python", "excel"), start=1):
            signed_file = SubmissionFile(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug="sample",
                document_id="training_agreement",
                document_type="signed_training_agreement",
                file_role="participant_signed",
                storage_provider="nextcloud",
                filename="same.pdf",
                storage_path=f"beneficiary/{index}/same.pdf",
                training_key=training_id,
                signed=True,
            )
            db.add(signed_file)
            db.flush()
            db.add(SubmissionTraining(
                submission_id=submission.id,
                training_id=training_id,
                training_name_snapshot=training_id,
                training_price_snapshot="1000",
                status="agreement_waiting_for_office_signature",
                is_locked=True,
                agreement_id=training_id,
                agreement_file_id=signed_file.id,
            ))
        db.flush()

        result = _verified_service(storage).check(
            db, form, submission, actor=SimpleNamespace(role="admin")
        )

        assert result.found_count == 0
        assert len(result.errors) == 1
        assert "Konflikt nazwy same.pdf" in result.errors[0]
        assert db.query(SubmissionFile).filter_by(document_type="agreement_signed_by_office").count() == 0
