from __future__ import annotations

from flask import Blueprint, current_app, render_template, request
from sqlalchemy.exc import IntegrityError

from database import create_session_factory
from services.training_management_service import TrainingManagementError
from routes.public_forms import _valid_public_csrf


bp = Blueprint("training_public", __name__)


def _db():
    return create_session_factory(current_app.config["DATABASE_URL"])()


@bp.route("/training-attendance/<token>", methods=["GET", "POST"])
def attendance(token: str):
    with _db() as db:
        service = current_app.extensions["services"].training_management_service
        resolved = service.confirm_attendance(db, token) if request.method == "POST" and _valid_public_csrf(request.form)[0] else None
        if request.method == "POST" and resolved:
            db.commit()
            record, session = resolved
            return render_template(
                "training_attendance_public.html",
                valid=True,
                confirmed=True,
                record=record,
                session=session,
            )
        token_record = service.resolve_attendance_token(db, token) if request.method == "GET" else None
        if token_record:
            db.rollback()
            record, session = token_record
            return render_template(
                "training_attendance_public.html",
                valid=True,
                confirmed=record.status == "present",
                record=record,
                session=session,
            )
        return render_template("training_attendance_public.html", valid=False, confirmed=False), 400


@bp.route("/training-survey/<token>", methods=["GET", "POST"])
def survey(token: str):
    with _db() as db:
        service = current_app.extensions["services"].training_management_service
        resolved = service.resolve_survey_invitation(db, token)
        if resolved is None:
            return render_template("training_survey_public.html", valid=False), 400
        invitation, survey_record, questions = resolved
        if request.method == "POST":
            if not _valid_public_csrf(request.form)[0]:
                return render_template("training_survey_public.html", valid=False), 400
            answers = {
                str(question.id): request.form.getlist(f"question_{question.id}")
                for question in questions
            }
            try:
                response = service.submit_survey(db, token, answers)
                db.commit()
                return render_template("training_survey_public.html", valid=True, completed=True, survey=survey_record, questions=questions, response=response)
            except TrainingManagementError as exc:
                db.rollback()
                return render_template("training_survey_public.html", valid=True, completed=False, survey=survey_record, questions=questions, error=str(exc)), 400
            except IntegrityError:
                db.rollback()
                if survey_record.survey_type in {"pre_test", "post_test"} and survey_record.attempt_policy == "single_attempt":
                    return render_template("training_survey_public.html", valid=True, completed=True, survey=survey_record, questions=questions)
                raise
        db.rollback()
        completed = bool(invitation.completed_at) and (survey_record.survey_type == "survey" or survey_record.attempt_policy == "single_attempt")
        return render_template("training_survey_public.html", valid=True, completed=completed, survey=survey_record, questions=questions)
