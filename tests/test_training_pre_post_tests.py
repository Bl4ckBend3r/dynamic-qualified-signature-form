from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import (
    Base, Form, FormSubmission, FormVersion, SubmissionTraining,
    TrainingSurveyAnswer, TrainingSurveyQuestion, TrainingSurveyResponse,
)
from routes.admin.training_management import _csv_safe
from services.training_management_service import TrainingManagementError, TrainingManagementService


def _definition():
    return {"documents": [{"id": "declaration", "fields": [{
        "type": "training_selection", "enabled": True, "name": "selected_trainings",
        "catalog": [{"id": "excel", "name": "Excel", "active": True}],
    }]}]}


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(db):
    definition = _definition()
    form = Form(slug="tests", name="Tests", title="Tests", definition_json=definition)
    db.add(form)
    db.flush()
    version = FormVersion(form_id=form.id, version_major=1, version_minor=0, version_label="1.0", status="published", definition_json=definition)
    db.add(version)
    db.flush()
    submission = FormSubmission(submission_id="SUB-TEST", form_slug=form.slug, form_name=form.name, form_version_id=version.id, imiona="Jan", nazwisko="=FORMULA", email="jan@example.org")
    db.add(submission)
    db.flush()
    participant = SubmissionTraining(submission_id=submission.id, training_id="excel", training_name_snapshot="Excel", training_snapshot={"id": "excel", "name": "Excel"}, status="selected")
    db.add(participant)
    db.flush()
    return form, participant


def _test(service, db, form, survey_type, questions, *, policy="single_attempt"):
    survey = service.create_survey(
        db, form, "excel", name=survey_type, description="", anonymous=False,
        survey_type=survey_type, attempt_policy=policy, questions=questions, actor_id=None,
    )
    survey.status = "active"
    db.flush()
    invitation, _submission, token = service.survey_links(db, form, survey)[0]
    db.flush()
    return survey, invitation, token


def test_pre_and_post_use_independent_questions_and_backend_scoring():
    with _db() as db:
        form, _participant = _seed(db)
        service = TrainingManagementService()
        pre, _invitation, pre_token = _test(service, db, form, "pre_test", [{
            "text": "PRE only", "type": "single_choice", "options": ["A", "B"],
            "correct_answers": ["B"], "points": "2", "is_scored": True,
        }])
        post, _invitation, post_token = _test(service, db, form, "post_test", [{
            "text": "POST only", "type": "multiple_choice", "options": ["A", "B", "C"],
            "correct_answers": ["A", "C"], "points": "3", "is_scored": True,
        }])
        pre_question = service.resolve_survey_invitation(db, pre_token)[2][0]
        post_question = service.resolve_survey_invitation(db, post_token)[2][0]
        pre_response = service.submit_survey(db, pre_token, {str(pre_question.id): ["B"], "score_points": "100"})
        post_response = service.submit_survey(db, post_token, {str(post_question.id): ["A", "C"]})
        assert pre.survey_type == "pre_test" and post.survey_type == "post_test"
        assert pre_question.text != post_question.text
        assert float(pre_response.score_points) == 2 and float(pre_response.score_percent) == 100
        assert float(post_response.score_points) == 3 and float(post_response.score_percent) == 100


def test_wrong_and_inexact_multiple_choice_receive_zero_points():
    with _db() as db:
        form, _participant = _seed(db)
        service = TrainingManagementService()
        survey, _invitation, token = _test(service, db, form, "pre_test", [{
            "text": "Choose", "type": "multiple_choice", "options": ["A", "B", "C"],
            "correct_answers": ["A", "C"], "points": "4", "is_scored": True,
        }], policy="multiple_attempts")
        question = service.resolve_survey_invitation(db, token)[2][0]
        missing = service.submit_survey(db, token, {str(question.id): ["A"]})
        extra = service.submit_survey(db, token, {str(question.id): ["A", "B", "C"]})
        assert float(missing.score_points) == 0
        assert float(extra.score_points) == 0
        assert [item.attempt_number for item in db.execute(select(TrainingSurveyResponse).order_by(TrainingSurveyResponse.id)).scalars()] == [1, 2]


def test_single_attempt_cannot_be_completed_twice():
    with _db() as db:
        form, _participant = _seed(db)
        service = TrainingManagementService()
        _survey, _invitation, token = _test(service, db, form, "pre_test", [{
            "text": "True?", "type": "true_false", "correct_answers": ["true"],
            "points": "1", "is_scored": True,
        }])
        question = service.resolve_survey_invitation(db, token)[2][0]
        service.submit_survey(db, token, {str(question.id): ["true"]})
        try:
            service.submit_survey(db, token, {str(question.id): ["false"]})
        except TrainingManagementError as exc:
            assert "już ukończony" in str(exc)
        else:
            raise AssertionError("second completion must fail")
        assert db.execute(select(TrainingSurveyResponse)).scalars().all().__len__() == 1


def test_completed_attempt_keeps_question_snapshot_after_draft_change():
    with _db() as db:
        form, _participant = _seed(db)
        service = TrainingManagementService()
        _survey, _invitation, token = _test(service, db, form, "pre_test", [{
            "text": "Original", "type": "single_choice", "options": ["A", "B"],
            "correct_answers": ["A"], "points": "5", "is_scored": True,
            "comparison_key": "topic_a",
        }])
        question = service.resolve_survey_invitation(db, token)[2][0]
        response = service.submit_survey(db, token, {str(question.id): ["A"]})
        question.text = "Changed later"
        question.correct_answers_json = ["B"]
        answer = db.execute(select(TrainingSurveyAnswer)).scalar_one()
        assert answer.question_snapshot_json["text"] == "Original"
        assert answer.question_snapshot_json["correct_answers"] == ["A"]
        assert response.test_snapshot_json["questions"][0]["question_key"] == "topic_a"
        assert float(response.score_points) == 5


def test_comparison_uses_percentage_points_and_missing_post_is_not_zero():
    with _db() as db:
        form, participant = _seed(db)
        service = TrainingManagementService()
        _pre, _invitation, pre_token = _test(service, db, form, "pre_test", [{
            "text": "PRE", "type": "single_choice", "options": ["A", "B"],
            "correct_answers": ["A"], "points": "10", "is_scored": True,
        }])
        pre_question = service.resolve_survey_invitation(db, pre_token)[2][0]
        service.submit_survey(db, pre_token, {str(pre_question.id): ["B"]})
        missing = service.comparison(db, form, "excel")["rows"][0]
        assert missing["post"] is None and missing["change_percentage_points"] is None

        _post, _invitation, post_token = _test(service, db, form, "post_test", [{
            "text": "POST", "type": "single_choice", "options": ["A"],
            "correct_answers": ["A"], "points": "10", "is_scored": True,
        }])
        post_question = service.resolve_survey_invitation(db, post_token)[2][0]
        service.submit_survey(db, post_token, {str(post_question.id): ["A"]})
        compared = service.comparison(db, form, "excel")["rows"][0]
        assert compared["participant_id"] == participant.id
        assert compared["change_percentage_points"] == 100


def test_detailed_rows_are_long_and_csv_formula_values_are_escaped():
    with _db() as db:
        form, _participant = _seed(db)
        service = TrainingManagementService()
        survey, _invitation, token = _test(service, db, form, "pre_test", [
            {"text": "One", "type": "single_choice", "options": ["A"], "correct_answers": ["A"], "points": "1", "is_scored": True},
            {"text": "Two", "type": "text", "is_scored": False},
        ])
        questions = service.resolve_survey_invitation(db, token)[2]
        service.submit_survey(db, token, {str(questions[0].id): ["A"], str(questions[1].id): ["=2+2"]})
        rows = service.detailed_answer_rows(db, survey, include_pii=True)
        assert len(rows) == 2
        assert {row["question_text"] for row in rows} == {"One", "Two"}
        assert _csv_safe(rows[1]["answer"][0]) == "'=2+2"
        assert _csv_safe(rows[0]["participant_name"].split()[-1]) == "'=FORMULA"


def test_invitation_becomes_invalid_when_participant_is_cancelled():
    with _db() as db:
        form, participant = _seed(db)
        service = TrainingManagementService()
        _survey, _invitation, token = _test(service, db, form, "pre_test", [{
            "text": "One", "type": "single_choice", "options": ["A"],
            "correct_answers": ["A"], "points": "1", "is_scored": True,
        }])
        participant.status = "cancelled"
        assert service.resolve_survey_invitation(db, token) is None
