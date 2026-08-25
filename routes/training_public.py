from __future__ import annotations

from flask import Blueprint, current_app, render_template, request

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
            _record, session = resolved
            return render_template("training_attendance_public.html", valid=True, confirmed=True, session=session)
        token_record = service.resolve_attendance_token(db, token) if request.method == "GET" else None
        if token_record:
            db.rollback()
            _record, session = token_record
            return render_template("training_attendance_public.html", valid=True, confirmed=False, session=session)
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
                service.submit_survey(db, token, answers)
                db.commit()
                return render_template("training_survey_public.html", valid=True, completed=True, survey=survey_record, questions=questions)
            except TrainingManagementError as exc:
                db.rollback()
                return render_template("training_survey_public.html", valid=True, completed=False, survey=survey_record, questions=questions, error=str(exc)), 400
        db.rollback()
        return render_template("training_survey_public.html", valid=True, completed=bool(invitation.completed_at), survey=survey_record, questions=questions)
