from __future__ import annotations

import csv
from copy import deepcopy
from datetime import date
from html import escape
from io import BytesIO, StringIO

from flask import abort, current_app, flash, g, redirect, render_template, request, send_file, url_for
from sqlalchemy import select

from models import (
    FormSubmission,
    MailTemplate,
    SubmissionTraining,
    TrainingAttendanceRecord,
    TrainingAttendanceSession,
    TrainingParticipantActionHistory,
    TrainingSurvey,
)
from services.submission_training_service import ACTIVE_STATUSES
from services.admin_form_service import parse_training_catalog
from services.training_catalog_service import TrainingCatalogService
from services.training_availability_service import TrainingAvailabilityService
from services.training_management_service import TrainingManagementError

from . import bp, db_session_factory, ensure_form_access, has_permission
from .auth import login_required, validate_csrf
from .forms import (
    _audit_training_catalog_actions,
    _used_training_ids_for_form,
)


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def _training_service():
    return current_app.extensions["services"].training_management_service


def _require_permission(db, form, permission: str) -> None:
    if not has_permission(db, permission, form=form):
        abort(403)


def _session_for_form(db, form, training_id: str, session_id: int) -> TrainingAttendanceSession:
    session = db.get(TrainingAttendanceSession, session_id)
    if session is None or session.form_id != form.id or session.training_id != training_id:
        abort(404)
    return session


def _survey_for_form(db, form, training_id: str, survey_id: int) -> TrainingSurvey:
    survey = db.get(TrainingSurvey, survey_id)
    if survey is None or survey.form_id != form.id or survey.training_id != training_id:
        abort(404)
    return survey


@bp.get("/forms/<int:form_id>/training-management")
@login_required
def training_management(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        trainings = _training_service().list_trainings(db, form)
        return render_template(
            "admin/trainings/index.html",
            form=form,
            trainings=trainings,
            training_field=TrainingCatalogService.get_training_field(form),
            can_edit_catalog=has_permission(db, "can_edit_form", form=form),
            open_training_id=str(request.args.get("open") or "").strip(),
            open_create=request.args.get("create") == "1",
        )


@bp.post("/forms/<int:form_id>/training-management/catalog")
@login_required
def training_catalog_create_inline(form_id: int):
    validate_csrf()
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_edit_form")
        current_definition = deepcopy(form.definition_json or {})
        current_field = TrainingCatalogService.get_training_field(current_definition)
        if current_field is None:
            abort(409, description="Najpierw udostępnij etap wyboru szkoleń w konfiguracji formularza.")
        try:
            parsed = parse_training_catalog(request.form)
            if len(parsed) != 1:
                raise ValueError("Formularz musi zawierać dokładnie jedno szkolenie.")
            training_id = str(parsed[0].get("id") or "").strip()
            current_catalog = list(current_field.get("catalog") or [])
            if any(
                isinstance(item, dict)
                and str(item.get("id") or "").strip() == training_id
                for item in current_catalog
            ):
                raise ValueError("Techniczny identyfikator szkolenia jest już używany.")
            updated_field = deepcopy(current_field)
            updated_field["catalog"] = [*current_catalog, parsed[0]]
            updated_definition = deepcopy(current_definition)
            TrainingCatalogService._replace_training_field(updated_definition, updated_field)
            updated_definition, actions = TrainingCatalogService().reconcile_definition(
                current_definition,
                updated_definition,
                used_training_ids=_used_training_ids_for_form(db, form.slug),
                actor_id=g.admin_user.id,
            )
            errors = TrainingCatalogService().validate_field(
                TrainingCatalogService.get_training_field(updated_definition)
            )
            if errors:
                raise ValueError(" ".join(errors))
            form.definition_json = updated_definition
            db.commit()
            _audit_training_catalog_actions(form.slug, actions)
            flash("Dodano szkolenie.", "success")
            return redirect(url_for(
                "admin.training_management",
                form_id=form.id,
                open=training_id,
                _anchor=f"training-{training_id}",
            ))
        except ValueError as exc:
            db.rollback()
            flash(str(exc) or "Niepoprawne dane szkolenia.", "error")
    return redirect(url_for("admin.training_management", form_id=form_id, create=1))


@bp.post("/forms/<int:form_id>/training-management/<training_id>/catalog")
@login_required
def training_catalog_update_inline(form_id: int, training_id: str):
    validate_csrf()
    redirect_target = url_for(
        "admin.training_management",
        form_id=form_id,
        open=training_id,
        _anchor=f"training-{training_id}",
    )
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_edit_form")
        current_definition = deepcopy(form.definition_json or {})
        current_field = TrainingCatalogService.get_training_field(current_definition)
        current_catalog = list((current_field or {}).get("catalog") or [])
        current_item = next(
            (
                item
                for item in current_catalog
                if isinstance(item, dict)
                and str(item.get("id") or "").strip() == training_id
            ),
            None,
        )
        if current_item is None:
            abort(404)
        if current_item.get("archived"):
            abort(409, description="Archiwalne szkolenie jest tylko do odczytu.")
        try:
            parsed = parse_training_catalog(request.form)
            if len(parsed) != 1 or str(parsed[0].get("id") or "") != training_id:
                abort(400, description="Nieprawidłowy identyfikator szkolenia.")
            if request.form.get("catalog_action") == "restore":
                parsed[0]["active"] = True
            updated_field = deepcopy(current_field or {})
            updated_field["catalog"] = [
                parsed[0]
                if isinstance(item, dict)
                and str(item.get("id") or "").strip() == training_id
                else item
                for item in current_catalog
            ]
            updated_definition = deepcopy(current_definition)
            TrainingCatalogService._replace_training_field(
                updated_definition,
                updated_field,
            )
            reason = str(request.form.get("training_item_change_reason") or "").strip()
            updated_definition, actions = TrainingCatalogService().reconcile_definition(
                current_definition,
                updated_definition,
                used_training_ids=_used_training_ids_for_form(db, form.slug),
                removal_reasons={training_id: reason} if reason else {},
                actor_id=g.admin_user.id,
            )
            errors = TrainingCatalogService().validate_field(
                TrainingCatalogService.get_training_field(updated_definition)
            )
            if errors:
                raise ValueError(" ".join(errors))
            form.definition_json = updated_definition
            occupied = TrainingAvailabilityService(
                current_app.extensions["services"].submission_repository
            ).occupied_counts(form_slug=form.slug).get(training_id, 0)
            db.commit()
            _audit_training_catalog_actions(form.slug, actions)
            flash("Zapisano szczegóły szkolenia.", "success")
            capacity = parsed[0].get("capacity")
            if capacity is not None and int(capacity) < int(occupied):
                flash(
                    "Limit miejsc jest niższy od liczby istniejących rezerwacji. "
                    "Istniejący uczestnicy pozostali bez zmian, a nowe zapisy są zablokowane.",
                    "warning",
                )
        except TrainingManagementError as exc:
            db.rollback()
            flash(str(exc), "error")
        except ValueError as exc:
            db.rollback()
            flash(str(exc) or "Niepoprawne dane szkolenia.", "error")
    return redirect(redirect_target)


@bp.get("/forms/<int:form_id>/training-management/<training_id>")
@login_required
def training_management_detail(form_id: int, training_id: str):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        can_view_pii = has_permission(db, "can_view_sensitive_data", form=form)
        try:
            training = _training_service().require_training(db, form, training_id)
            participants = _training_service().participants(
                db, form, training_id, include_pii=can_view_pii,
                filters={
                    "status": request.args.get("status", ""),
                    "agreement": request.args.get("agreement", ""),
                    "attendance": request.args.get("attendance", ""),
                    "survey_sent": request.args.get("survey_sent", ""),
                    "query": request.args.get("query", "") if can_view_pii else "",
                },
            )
        except TrainingManagementError:
            abort(404)
        sessions = db.execute(select(TrainingAttendanceSession).where(TrainingAttendanceSession.form_id == form.id, TrainingAttendanceSession.training_id == training_id).order_by(TrainingAttendanceSession.session_date.desc(), TrainingAttendanceSession.id.desc())).scalars().all()
        attendance_records = {
            (record.session_id, record.submission_training_id): record
            for record in db.execute(
                select(TrainingAttendanceRecord).where(
                    TrainingAttendanceRecord.session_id.in_([item.id for item in sessions] or [-1])
                )
            ).scalars().all()
        }
        surveys = db.execute(select(TrainingSurvey).where(TrainingSurvey.form_id == form.id, TrainingSurvey.training_id == training_id).order_by(TrainingSurvey.id.desc())).scalars().all()
        history = db.execute(
            select(TrainingParticipantActionHistory)
            .where(TrainingParticipantActionHistory.form_id == form.id, TrainingParticipantActionHistory.training_id == training_id)
            .order_by(TrainingParticipantActionHistory.created_at.desc()).limit(100)
        ).scalars().all()
        templates = db.execute(select(MailTemplate).where(MailTemplate.form_id == form.id, MailTemplate.is_active.is_(True)).order_by(MailTemplate.name)).scalars().all()
        permissions = {key: has_permission(db, key, form=form) for key in ("can_view_sensitive_data", "can_send_email", "can_manage_documents", "can_export_data")}
        return render_template("admin/trainings/detail.html", form=form, training=training, participants=participants, sessions=sessions, attendance_records=attendance_records, surveys=surveys, history=history, templates=templates, permissions=permissions)


@bp.post("/forms/<int:form_id>/training-management/<training_id>/participants/<int:participant_id>/cancel")
@login_required
def training_participant_cancel(form_id: int, training_id: str, participant_id: int):
    validate_csrf()
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        _require_permission(db, form, "can_manage_documents")
        try:
            _training_service().cancel_participation(db, form, training_id, participant_id, actor_id=g.admin_user.id, reason=request.form.get("reason", ""))
            db.commit()
            flash("Uczestnictwo i powiązane umowy zostały unieważnione bez usuwania historii.", "success")
        except TrainingManagementError as exc:
            db.rollback()
            flash(str(exc), "error")
    return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))


@bp.post("/forms/<int:form_id>/training-management/<training_id>/messages")
@login_required
def training_message_send(form_id: int, training_id: str):
    validate_csrf()
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        _require_permission(db, form, "can_send_email")
        _require_permission(db, form, "can_view_sensitive_data")
        service = _training_service()
        try:
            service.require_training(db, form, training_id)
        except TrainingManagementError:
            abort(404)
        selected_ids = {int(value) for value in request.form.getlist("participant_id") if str(value).isdigit()}
        query = select(SubmissionTraining, FormSubmission).join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id).where(FormSubmission.form_slug == form.slug, SubmissionTraining.training_id == training_id)
        if request.form.get("all_active") == "1":
            query = query.where(SubmissionTraining.status.in_(ACTIVE_STATUSES))
        elif selected_ids:
            query = query.where(SubmissionTraining.id.in_(selected_ids))
        else:
            flash("Wybierz co najmniej jednego odbiorcę.", "error")
            return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))
        template_id = int(request.form.get("template_id")) if str(request.form.get("template_id", "")).isdigit() else None
        template = db.get(MailTemplate, template_id) if template_id else None
        if template is not None and (template.form_id != form.id or not template.is_active):
            abort(404)
        subject = str(request.form.get("subject") or "").strip()
        body = str(request.form.get("body") or "").strip()
        dispatch = current_app.extensions["services"].mail_dispatch_service
        sent = 0
        for _participant, submission in db.execute(query).all():
            if template:
                result = dispatch.dispatch_to_submission(db=db, form=form, submission=submission, template=template, to_email=submission.email, subject_template=subject, event_type="training_bulk_message", sent_by_id=g.admin_user.id, extra_context={"training_name": service.require_training(db, form, training_id)["name"]})
            else:
                result = dispatch.dispatch_raw(db=db, form=form, submission=submission, event_type="training_bulk_message", recipient=submission.email, subject=subject, html_body="<p>" + escape(body).replace("\n", "<br>") + "</p>", text_body=body, sent_by_id=g.admin_user.id)
            sent += result.status == "sent"
        db.commit()
        flash(f"Przetworzono indywidualne wysyłki. Wysłano: {sent}.", "success")
    return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))


@bp.post("/forms/<int:form_id>/training-management/<training_id>/attendance")
@login_required
def training_attendance_create(form_id: int, training_id: str):
    validate_csrf()
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        _require_permission(db, form, "can_manage_documents")
        try:
            session_date = date.fromisoformat(str(request.form.get("session_date") or ""))
            _training_service().create_attendance_session(db, form, training_id, name=request.form.get("name", ""), session_date=session_date, start_time=request.form.get("start_time", ""), end_time=request.form.get("end_time", ""), actor_id=g.admin_user.id)
            db.commit()
            flash("Utworzono roboczą sesję obecności.", "success")
        except (ValueError, TrainingManagementError) as exc:
            db.rollback()
            flash(str(exc) or "Nieprawidłowa data sesji.", "error")
    return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))


@bp.post("/forms/<int:form_id>/training-management/<training_id>/attendance/<int:session_id>/status")
@login_required
def training_attendance_status(form_id: int, training_id: str, session_id: int):
    validate_csrf()
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        _require_permission(db, form, "can_manage_documents")
        session = _session_for_form(db, form, training_id, session_id)
        try:
            _training_service().set_attendance_session_status(db, session, request.form.get("status", ""), actor_id=g.admin_user.id)
            db.commit()
        except TrainingManagementError as exc:
            flash(str(exc), "error")
    return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))


@bp.post("/forms/<int:form_id>/training-management/<training_id>/attendance/<int:session_id>/send")
@login_required
def training_attendance_send(form_id: int, training_id: str, session_id: int):
    validate_csrf()
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        _require_permission(db, form, "can_send_email")
        session = _session_for_form(db, form, training_id, session_id)
        if session.status != "active":
            flash("Linki można wysłać tylko dla aktywnej sesji.", "error")
            return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))
        dispatch = current_app.extensions["services"].mail_dispatch_service
        for record, submission, token in _training_service().attendance_links(db, session):
            link = url_for("training_public.attendance", token=token, _external=True)
            result = dispatch.dispatch_raw(db=db, form=form, submission=submission, event_type="training_attendance_invitation", recipient=submission.email, subject=f"Potwierdź obecność — {session.name}", html_body=f'<p><a href="{escape(link)}">Potwierdź obecność</a></p>', text_body=f"Potwierdź obecność: {link}", sent_by_id=g.admin_user.id)
            if result.log is not None:
                result.log.html_body = "[Indywidualny link obecności zredagowany]"
                result.log.text_body = "[Indywidualny link obecności zredagowany]"
            db.add(TrainingParticipantActionHistory(form_id=form.id, training_id=training_id, submission_training_id=record.submission_training_id, action="attendance_link_sent", new_value=str(session.id), actor_user_id=g.admin_user.id))
        db.commit()
        flash("Utworzono indywidualne linki i zapisano osobne wyniki wysyłki.", "success")
    return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))


@bp.post("/forms/<int:form_id>/training-management/<training_id>/attendance/<int:session_id>/participants/<int:participant_id>")
@login_required
def training_attendance_update(form_id: int, training_id: str, session_id: int, participant_id: int):
    validate_csrf()
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        _require_permission(db, form, "can_manage_documents")
        _session_for_form(db, form, training_id, session_id)
        try:
            _training_service().update_attendance(db, form, session_id, participant_id, request.form.get("status", ""), actor_id=g.admin_user.id)
            db.commit()
        except TrainingManagementError as exc:
            flash(str(exc), "error")
    return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))


@bp.get("/forms/<int:form_id>/training-management/<training_id>/attendance/<int:session_id>.csv")
@login_required
def training_attendance_export(form_id: int, training_id: str, session_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        _require_permission(db, form, "can_export_data")
        session = _session_for_form(db, form, training_id, session_id)
        include_pii = has_permission(db, "can_view_sensitive_data", form=form)
        rows = db.execute(select(TrainingAttendanceRecord, FormSubmission).join(SubmissionTraining, SubmissionTraining.id == TrainingAttendanceRecord.submission_training_id).join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id).where(TrainingAttendanceRecord.session_id == session.id).order_by(FormSubmission.nazwisko, FormSubmission.imiona)).all()
        output = StringIO(newline="")
        fields = ["lp"] + (["uczestnik"] if include_pii else []) + ["submission_id"] + (["email"] if include_pii else []) + ["status", "confirmed_at", "method"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for index, (record, submission) in enumerate(rows, 1):
            row = {"lp": index, "submission_id": submission.submission_id, "status": record.status, "confirmed_at": record.confirmed_at.isoformat() if record.confirmed_at else "", "method": record.confirmation_method}
            if include_pii:
                row["uczestnik"] = f"{submission.imiona} {submission.nazwisko}"
                row["email"] = submission.email
            writer.writerow({key: _csv_safe(value) for key, value in row.items()})
        payload = ("\ufeff" + output.getvalue()).encode("utf-8")
        return send_file(BytesIO(payload), mimetype="text/csv; charset=utf-8", as_attachment=True, download_name=f"obecnosc_{form.slug}_{training_id}_{session.id}.csv")


@bp.post("/forms/<int:form_id>/training-management/<training_id>/surveys")
@login_required
def training_survey_create(form_id: int, training_id: str):
    validate_csrf()
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        _require_permission(db, form, "can_manage_documents")
        questions = []
        for text, question_type, options in zip(request.form.getlist("question_text"), request.form.getlist("question_type"), request.form.getlist("question_options")):
            questions.append({"text": text, "type": question_type, "options": [item.strip() for item in str(options).splitlines() if item.strip()], "required": True})
        try:
            _training_service().create_survey(db, form, training_id, name=request.form.get("name", ""), description=request.form.get("description", ""), anonymous=request.form.get("anonymous") == "on", questions=questions, actor_id=g.admin_user.id)
            db.commit()
            flash("Utworzono ankietę roboczą.", "success")
        except TrainingManagementError as exc:
            db.rollback()
            flash(str(exc), "error")
    return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))


@bp.post("/forms/<int:form_id>/training-management/<training_id>/surveys/<int:survey_id>/status")
@login_required
def training_survey_status(form_id: int, training_id: str, survey_id: int):
    validate_csrf()
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        _require_permission(db, form, "can_manage_documents")
        survey = _survey_for_form(db, form, training_id, survey_id)
        try:
            _training_service().set_survey_status(db, form, survey, request.form.get("status", ""), actor_id=g.admin_user.id)
            db.commit()
        except TrainingManagementError as exc:
            db.rollback()
            flash(str(exc), "error")
    return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))


@bp.post("/forms/<int:form_id>/training-management/<training_id>/surveys/<int:survey_id>/send")
@login_required
def training_survey_send(form_id: int, training_id: str, survey_id: int):
    validate_csrf()
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        _require_permission(db, form, "can_send_email")
        survey = _survey_for_form(db, form, training_id, survey_id)
        if survey.status != "active":
            flash("Zaproszenia można wysłać tylko dla aktywnej ankiety.", "error")
            return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))
        dispatch = current_app.extensions["services"].mail_dispatch_service
        for invitation, submission, token in _training_service().survey_links(db, form, survey):
            link = url_for("training_public.survey", token=token, _external=True)
            result = dispatch.dispatch_raw(db=db, form=form, submission=submission, event_type="training_survey_invitation", recipient=submission.email, subject=survey.name, html_body=f'<p>{escape(survey.description)}</p><p><a href="{escape(link)}">Wypełnij ankietę</a></p>', text_body=f"{survey.description}\n\n{link}", sent_by_id=g.admin_user.id)
            if result.log is not None:
                result.log.html_body = "[Indywidualny link ankiety zredagowany]"
                result.log.text_body = "[Indywidualny link ankiety zredagowany]"
            db.add(TrainingParticipantActionHistory(form_id=form.id, training_id=training_id, submission_training_id=invitation.submission_training_id, action="survey_invitation_sent", new_value=str(survey.id), actor_user_id=g.admin_user.id))
        db.commit()
        flash("Wysłano indywidualne zaproszenia do ankiety.", "success")
    return redirect(url_for("admin.training_management_detail", form_id=form_id, training_id=training_id))


@bp.get("/forms/<int:form_id>/training-management/<training_id>/surveys/<int:survey_id>/results")
@login_required
def training_survey_results(form_id: int, training_id: str, survey_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_view_submissions")
        survey = _survey_for_form(db, form, training_id, survey_id)
        results = _training_service().survey_results(db, survey)
        return render_template("admin/trainings/survey_results.html", form=form, survey=survey, results=results)
