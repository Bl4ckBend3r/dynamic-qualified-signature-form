from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import (
    Base,
    Form,
    FormSubmission,
    FormVersion,
    SubmissionFile,
    SubmissionTraining,
    TrainingAttendanceRecord,
    TrainingParticipantActionHistory,
    TrainingSurveyInvitation,
)
from services.training_management_service import TrainingManagementService


def _definition():
    return {
        "documents": [{
            "id": "declaration",
            "fields": [{
                "type": "training_selection", "enabled": True,
                "name": "selected_trainings", "label": "Szkolenia",
                "required": True, "currency": "PLN", "max_total_amount": "5000.00",
                "catalog": [
                    {"id": "excel", "name": "Excel", "price": "1000.00", "capacity": 2, "active": True, "dates": [{"start_date": "2026-10-12"}]},
                    {"id": "python", "name": "Python", "price": "1200.00", "capacity": 2, "active": True, "dates": []},
                ],
            }],
        }]
    }


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(db):
    definition = _definition()
    form = Form(slug="training-form", name="Training form", title="Training", definition_json=definition)
    db.add(form)
    db.flush()
    version = FormVersion(form_id=form.id, version_major=2, version_minor=1, version_label="2.1", status="published", definition_json=definition)
    db.add(version)
    db.flush()
    submission = FormSubmission(submission_id="SUB-1", form_slug=form.slug, form_name=form.name, form_version_id=version.id, imiona="Jan", nazwisko="Kowalski", email="jan@example.org", telefon="123456789")
    db.add(submission)
    db.flush()
    excel = SubmissionTraining(submission_id=submission.id, training_id="excel", training_name_snapshot="Excel", training_price_snapshot="1000.00", training_snapshot=definition["documents"][0]["fields"][0]["catalog"][0], status="agreement_signed_by_office", is_locked=True)
    python = SubmissionTraining(submission_id=submission.id, training_id="python", training_name_snapshot="Python", training_price_snapshot="1200.00", training_snapshot=definition["documents"][0]["fields"][0]["catalog"][1], status="selected")
    db.add_all([excel, python])
    db.flush()
    agreement = SubmissionFile(submission_id=submission.id, public_submission_id=submission.submission_id, form_slug=form.slug, document_type="agreement_signed_by_office", filename="agreement.pdf", storage_path="private/agreement.pdf", training_key="excel", status="signed")
    db.add(agreement)
    db.commit()
    return form, version, submission, excel, python


def test_participant_pii_is_removed_from_view_model():
    with _db() as db:
        form, *_ = _seed(db)
        participant = TrainingManagementService().participants(db, form, "excel", include_pii=False)[0]
        assert participant["name"] == "Dane zastrzeżone"
        assert participant["email"] == ""
        assert participant["phone"] == ""
        assert "Kowalski" not in repr(participant)
        assert "jan@example.org" not in repr(participant)


def test_cancelling_one_training_preserves_other_training_files_and_form_version():
    with _db() as db:
        form, version, submission, excel, python = _seed(db)
        original_definition = version.definition_json.copy()
        TrainingManagementService().cancel_participation(db, form, "excel", excel.id, actor_id=None, reason="Rezygnacja uczestnika")
        db.commit()
        assert db.get(SubmissionTraining, excel.id).status == "cancelled"
        assert db.get(SubmissionTraining, excel.id).is_locked is False
        assert db.get(SubmissionTraining, python.id).status == "selected"
        assert db.get(FormSubmission, submission.id) is not None
        assert db.execute(select(SubmissionFile.status).where(SubmissionFile.training_key == "excel")).scalar_one() == "cancelled"
        assert db.get(FormVersion, version.id).version_label == "2.1"
        assert db.get(FormVersion, version.id).definition_json == original_definition
        assert db.execute(select(TrainingParticipantActionHistory)).scalar_one().reason == "Rezygnacja uczestnika"


def test_attendance_token_is_hashed_and_confirmation_is_idempotent():
    with _db() as db:
        form, _version, _submission, excel, _python = _seed(db)
        service = TrainingManagementService()
        session = service.create_attendance_session(db, form, "excel", name="Excel — 12.10.2026", session_date=date(2026, 10, 12), actor_id=None)
        session.status = "active"
        db.flush()
        links = service.attendance_links(db, session)
        assert len(links) == 1
        record, _submission, raw_token = links[0]
        assert raw_token not in record.token_hash
        db.commit()
        first = service.confirm_attendance(db, raw_token)
        db.commit()
        second = service.confirm_attendance(db, raw_token)
        db.commit()
        assert first is not None and second is not None
        assert db.execute(select(TrainingAttendanceRecord).where(TrainingAttendanceRecord.session_id == session.id)).scalars().all() == [record]
        assert record.status == "present"
        assert record.confirmation_method == "email_link"


def test_survey_invitation_is_hashed_and_response_updates_same_record():
    with _db() as db:
        form, *_ = _seed(db)
        service = TrainingManagementService()
        survey = service.create_survey(db, form, "excel", name="Satysfakcja", description="", anonymous=True, questions=[{"text": "Ocena", "type": "scale"}], actor_id=None)
        survey.status = "active"
        db.flush()
        invitation, _submission, raw_token = service.survey_links(db, form, survey)[0]
        assert raw_token not in invitation.token_hash
        db.commit()
        question = service.resolve_survey_invitation(db, raw_token)[2][0]
        first = service.submit_survey(db, raw_token, {str(question.id): ["4"]})
        db.commit()
        second = service.submit_survey(db, raw_token, {str(question.id): ["5"]})
        db.commit()
        assert first.id == second.id
        assert db.execute(select(TrainingSurveyInvitation).where(TrainingSurveyInvitation.id == invitation.id)).scalar_one().completed_at is not None
        results = service.survey_results(db, survey)
        assert results["responses"] == 1
        assert results["questions"][0]["average"] == 5


def test_training_wide_actions_are_audited_without_participants():
    with _db() as db:
        form = Form(slug="empty-training", name="Empty", title="Empty", definition_json=_definition())
        db.add(form)
        db.flush()
        service = TrainingManagementService()
        attendance_session = service.create_attendance_session(db, form, "excel", name="Empty session", session_date=date(2026, 10, 12), actor_id=None)
        service.set_attendance_session_status(db, attendance_session, "active", actor_id=None)
        service.create_survey(db, form, "excel", name="Empty survey", description="", anonymous=False, questions=[{"text": "Ocena", "type": "scale"}], actor_id=None)
        db.commit()
        history = db.execute(select(TrainingParticipantActionHistory).where(TrainingParticipantActionHistory.form_id == form.id)).scalars().all()
        assert [item.action for item in history] == ["attendance_session_created", "attendance_session_status", "survey_created"]
        assert all(item.submission_training_id is None for item in history)
