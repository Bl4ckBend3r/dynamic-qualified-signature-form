import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Base, Form, FormSubmission, SubmissionFile, SubmissionTraining, SubmissionWorkflowEvent
from services.submission_training_service import (
    SubmissionTrainingService,
    TrainingSelectionError,
)


FIELD = {
    "name": "selected_trainings",
    "required": True,
    "currency": "PLN",
    "max_total_amount": "5000",
    "catalog": [
        {"id": "python", "name": "Python", "price": "3000", "active": True},
        {"id": "excel", "name": "Excel", "price": "2000", "active": True},
        {"id": "inactive", "name": "Nieaktywne", "price": "100", "active": False},
    ],
}


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_submission(db):
    submission = FormSubmission(
        submission_id="public-1",
        form_slug="sample",
        form_name="Sample",
        access_token="secret",
        officer_decision="TAK",
        declaration_generated="Tak",
        declaration_signed="Tak",
        declaration_signature_valid="Tak",
    )
    db.add(submission)
    db.flush()
    return submission


def test_selection_is_available_only_after_acceptance_and_valid_declaration(db):
    form = Form(slug="sample", name="Sample", training_selection_open=True)
    submission = make_submission(db)

    assert SubmissionTrainingService.can_select(form, submission) is True
    submission.declaration_signature_valid = "Nie"
    assert SubmissionTrainingService.can_select(form, submission) is False
    submission.declaration_signature_valid = "Tak"
    form.training_selection_open = False
    assert SubmissionTrainingService.can_select(form, submission) is False


def test_locked_training_cannot_be_removed_and_counts_towards_limit(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python"])
    row = db.query(SubmissionTraining).one()
    row.is_locked = True
    row.status = "agreement_uploaded_by_beneficiary"

    summary = service.save(db, submission, FIELD, [])

    assert [item["id"] for item in summary["items"]] == ["python"]
    assert summary["limit_used"] == 3000
    assert json.loads(submission.selected_trainings)[0]["id"] == "python"


def test_limit_validation_includes_locked_training(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python"])
    row = db.query(SubmissionTraining).one()
    row.is_locked = True

    with pytest.raises(TrainingSelectionError, match="przekracza limit"):
        service.save(db, submission, {**FIELD, "max_total_amount": "4000"}, ["excel"])


def test_uploading_agreement_locks_only_its_training(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python", "excel"])
    submission.training_agreements = json.dumps(
        [
            {"id": "agreement-python", "training": {"id": "python", "name": "Python", "price": "3000"}},
            {"id": "agreement-excel", "training": {"id": "excel", "name": "Excel", "price": "2000"}},
        ]
    )

    assert service.lock_for_agreement(db, submission, "agreement-python") is True

    rows = {row.training_id: row for row in db.query(SubmissionTraining).all()}
    assert rows["python"].is_locked is True
    assert rows["python"].agreement_id == "agreement-python"
    assert rows["excel"].is_locked is False
    event = db.query(SubmissionWorkflowEvent).filter_by(source="agreement_uploaded_by_beneficiary").one()
    assert event.side_effects["submission_training_id"] == rows["python"].id


def test_public_template_uses_one_bulk_uploader_even_for_single_training():
    template = (Path(__file__).resolve().parents[1] / "templates" / "documents_to_sign.html").read_text(encoding="utf-8")

    assert "result.uploadable_training_agreements|length > 1" not in template
    assert template.count("data-bulk-agreement-upload") == 1
    assert "signed_agreement_pdf_{{ loop.index }}" not in template
    assert "Wgraj podpisane umowy" in template


def test_locked_training_uses_associated_signed_file_when_legacy_json_is_stale(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python"])
    submission.training_agreements = json.dumps([
        {"id": "agreement-python", "training_id": "python", "filename": "python.pdf", "signature_valid": False}
    ])
    signed_file = SubmissionFile(
        submission_id=submission.id,
        public_submission_id=submission.submission_id,
        form_slug=submission.form_slug,
        document_id="training_agreement",
        document_type="signed_training_agreement",
        file_role="participant_signed",
        storage_provider="local",
        filename="python-signed.pdf",
        storage_path="output/sample/python-signed.pdf",
        signed=True,
    )
    db.add(signed_file)
    db.flush()

    assert service.lock_for_agreement(db, submission, "agreement-python", agreement_file_id=signed_file.id) is True
    summary = service.summary(db, submission, FIELD)

    assert summary["items"][0]["signed_agreement_filename"] == "python-signed.pdf"
    assert summary["items"][0]["is_locked"] is True


def test_generated_agreement_is_associated_with_one_training(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python", "excel"])

    service.associate_generated_agreements(
        db,
        submission,
        [{"id": "python", "training_id": "python", "filename": "python.pdf"}],
    )

    rows = {row.training_id: row for row in db.query(SubmissionTraining).all()}
    assert rows["python"].agreement_id == "python"
    assert rows["python"].status == "agreement_generated"
    assert rows["excel"].agreement_id == ""
    assert rows["excel"].status == "selected"


def test_downloaded_agreement_moves_only_its_training_to_upload_stage_without_locking(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python", "excel"])
    service.associate_generated_agreements(
        db,
        submission,
        [
            {"id": "agreement-python", "training_id": "python", "filename": "python.pdf"},
            {"id": "agreement-excel", "training_id": "excel", "filename": "excel.pdf"},
        ],
    )

    assert service.mark_agreement_downloaded(db, submission, "agreement-python", filename="python.pdf") is True
    assert service.mark_agreement_downloaded(db, submission, "agreement-python", filename="python.pdf") is False

    rows = {row.training_id: row for row in db.query(SubmissionTraining).all()}
    assert rows["python"].status == "agreement_waiting_for_beneficiary_signature"
    assert rows["python"].agreement_downloaded_at is not None
    assert rows["python"].is_locked is False
    assert rows["excel"].status == "agreement_generated"
    assert submission.process_status == "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE"
    assert db.query(SubmissionWorkflowEvent).filter_by(source="agreement_downloaded_by_beneficiary").count() == 1


def test_selection_and_generated_or_downloaded_agreements_do_not_use_locked_limit(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python"])
    row = db.query(SubmissionTraining).one()
    assert service.summary(db, submission, FIELD)["limit_used"] == 0

    service.associate_generated_agreements(db, submission, [{"id": "python", "training_id": "python"}])
    service.mark_agreement_downloaded(db, submission, "python")
    summary = service.summary(db, submission, FIELD)

    assert row.is_locked is False
    assert summary["limit_used"] == 0
    assert summary["limit_pending"] == 3000


def test_upload_rejects_lock_when_training_capacity_is_exhausted(db):
    field = {**FIELD, "catalog": [{**FIELD["catalog"][0], "capacity": 1}]}
    service = SubmissionTrainingService()
    first = make_submission(db)
    first.submission_id = "first"
    second = make_submission(db)
    second.submission_id = "second"
    for submission, agreement_id in ((first, "first-python"), (second, "second-python")):
        service.save(db, submission, field, ["python"])
        submission.training_agreements = json.dumps([
            {"id": agreement_id, "training": {"id": "python", "name": "Python", "price": "3000", "capacity": 1}}
        ])

    assert service.lock_for_agreement(db, first, "first-python") is True
    with pytest.raises(TrainingSelectionError, match="Brak dostępnych miejsc"):
        service.lock_for_agreement(db, second, "second-python")

    second_row = db.query(SubmissionTraining).filter_by(submission_id=second.id).one()
    assert second_row.is_locked is False


def test_legacy_signed_agreement_is_backfilled_as_locked(db):
    submission = make_submission(db)
    submission.selected_trainings = json.dumps(
        [{"id": "python", "name": "Python", "price": "3000"}]
    )
    submission.training_agreements = json.dumps(
        [{"id": "python", "training_id": "python", "signature_valid": True}]
    )

    summary = SubmissionTrainingService().summary(db, submission, FIELD)

    assert summary["items"][0]["is_locked"] is True
    row = db.query(SubmissionTraining).one()
    assert row.locked_by_event == "legacy_signed_agreement"


def test_existing_selected_normalized_row_is_backfilled_when_legacy_agreement_is_signed(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python"])
    submission.training_agreements = json.dumps([
        {"id": "python", "training_id": "python", "signature_valid": True}
    ])

    service.synchronize_legacy(db, submission)

    row = db.query(SubmissionTraining).one()
    assert row.is_locked is True
    assert row.status == "agreement_uploaded_by_beneficiary"
    assert row.locked_by_event == "legacy_signed_agreement"


def test_selection_view_contains_full_catalog_status_and_place_counts(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python"])

    view = service.selection_view(
        db,
        submission,
        FIELD,
        {
            "python": {
                "occupied_seats": 2,
                "available_seats": 8,
                "is_available": True,
            }
        },
    )

    python = next(item for item in view["catalog"] if item["id"] == "python")
    excel = next(item for item in view["catalog"] if item["id"] == "excel")
    assert python["participant_status_label"] == "Wybrane"
    assert python["display_occupied_seats"] == 2
    assert excel["participant_status_label"] == "Dostępne"


def test_locked_inactive_training_remains_visible_as_history(db):
    submission = make_submission(db)
    db.add(
        SubmissionTraining(
            submission_id=submission.id,
            training_id="inactive",
            training_name_snapshot="Nieaktywne",
            training_price_snapshot="100.00",
            status="agreement_uploaded_by_beneficiary",
            is_locked=True,
        )
    )
    db.flush()

    view = SubmissionTrainingService().selection_view(
        db,
        submission,
        FIELD,
        {},
    )

    historical = next(
        item for item in view["catalog"] if item["id"] == "inactive"
    )
    assert historical["active"] is False
    assert historical["is_selected"] is True
    assert historical["is_locked"] is True
    assert historical["participant_status_label"] == "Podpisana umowa wgrana"


def test_selection_snapshot_is_not_changed_by_later_catalog_edits(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    original_field = {
        "name": "selected_trainings",
        "currency": "PLN",
        "max_total_amount": "5000",
        "catalog": [
            {
                "id": "stable-id",
                "name": "Excel — edycja wiosenna",
                "description": "Opis zapisany w chwili wyboru.",
                "price": "1200.00",
                "currency": "PLN",
                "active": True,
                "version": 2,
                "dates": [
                    {
                        "start_date": "2026-09-10",
                        "start_time": "09:00",
                        "end_time": "15:00",
                        "location": "Poznań",
                    }
                ],
            }
        ],
    }

    service.save(db, submission, original_field, ["stable-id"])
    row = db.query(SubmissionTraining).one()

    assert row.training_snapshot["name"] == "Excel — edycja wiosenna"
    assert row.training_snapshot["price"] == "1200.00"
    assert row.training_snapshot["version"] == 2
    assert row.training_snapshot["location"] == "Poznań"
    assert row.training_snapshot["dates"][0]["start_date"] == "2026-09-10"

    changed_field = {
        **original_field,
        "catalog": [
            {
                **original_field["catalog"][0],
                "name": "Excel — nowa edycja",
                "description": "Nowy opis katalogowy.",
                "price": "1800.00",
                "version": 3,
                "dates": [
                    {
                        "start_date": "2027-01-15",
                        "location": "Warszawa",
                    }
                ],
            }
        ],
    }
    summary = service.summary(db, submission, changed_field)
    stored_selection = json.loads(submission.selected_trainings)[0]

    assert summary["items"][0]["name"] == "Excel — edycja wiosenna"
    assert summary["items"][0]["price"] == "1200.00"
    assert summary["items"][0]["description"] == "Opis zapisany w chwili wyboru."
    assert summary["items"][0]["version"] == 2
    assert summary["items"][0]["location"] == "Poznań"
    assert stored_selection["name"] == "Excel — edycja wiosenna"
    assert stored_selection["price"] == "1200.00"


def test_archived_locked_training_remains_in_limit_without_catalog_record(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python"])
    row = db.query(SubmissionTraining).one()
    row.is_locked = True
    row.status = "agreement_signed_by_office"

    field_without_archived_training = {
        **FIELD,
        "catalog": [
            training
            for training in FIELD["catalog"]
            if training["id"] != "python"
        ],
    }
    summary = service.summary(db, submission, field_without_archived_training)

    assert summary["limit_used"] == 3000
    assert summary["limit_pending"] == 0
    assert summary["limit_locked"] == 3000
    assert summary["items"][0]["id"] == "python"
    assert summary["items"][0]["name"] == "Python"


def test_previously_selected_archived_training_is_preserved_on_save(db):
    submission = make_submission(db)
    service = SubmissionTrainingService()
    service.save(db, submission, FIELD, ["python"])
    archived_field = {
        **FIELD,
        "catalog": [
            training
            for training in FIELD["catalog"]
            if training["id"] != "python"
        ],
    }

    summary = service.save(db, submission, archived_field, ["python"])
    row = db.query(SubmissionTraining).one()

    assert row.status == "selected"
    assert summary["limit_used"] == 0
    assert summary["limit_pending"] == 3000
    assert summary["items"][0]["id"] == "python"
    assert json.loads(submission.selected_trainings)[0]["id"] == "python"
