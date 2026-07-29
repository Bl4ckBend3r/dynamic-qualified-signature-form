import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Base, Form, FormSubmission, SubmissionTraining
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
    assert python["display_occupied_seats"] == 3
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
