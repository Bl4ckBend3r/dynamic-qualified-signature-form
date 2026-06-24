from __future__ import annotations

from datetime import datetime, timezone

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from models import FormSubmission, SubmissionDecision
from services.admin_form_service import form_has_additional_fields
from services.admin_submission_service import (
    admin_status_label,
    build_filter_fields,
    filter_submissions,
    sort_submissions,
    submission_value,
)
from services.process_service import ProcessStatus
from statuses import WAITING_FOR_CORRECTION

from . import (
    OFFICER_DECISIONS,
    ROLE_SUPER_ADMIN,
    active_fields_for_form,
    bp,
    db_session_factory,
    ensure_form_access,
    list_accessible_forms,
    login_required,
)


@bp.get("/submissions")
@login_required
def submissions_all():
    user = g.admin_user
    with db_session_factory()() as db:
        forms = list_accessible_forms(db, user)
        form_by_slug = {form.slug: form for form in forms}
        slugs = list(form_by_slug.keys())
        if user.role != ROLE_SUPER_ADMIN and not slugs:
            submissions = []
        else:
            query = select(FormSubmission)
            if user.role != ROLE_SUPER_ADMIN:
                query = query.where(FormSubmission.form_slug.in_(slugs))
            submissions = db.execute(query).scalars().all()
        submissions = filter_submissions(submissions, request.args)
        submissions = sort_submissions(
            submissions,
            request.args.get("sort") or "created_at",
            request.args.get("direction") or "desc",
        )
        return render_template(
            "admin/submissions/all.html",
            submissions=submissions,
            form_by_slug=form_by_slug,
            filters=request.args,
            status_label=admin_status_label,
        )


@bp.get("/forms/<int:form_id>/submissions")
@login_required
def submissions_list(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        fields = active_fields_for_form(db, form.id)
        submissions = db.execute(
            select(FormSubmission).where(FormSubmission.form_slug == form.slug)
        ).scalars().all()
        submissions = filter_submissions(submissions, request.args)
        submissions = sort_submissions(submissions, request.args.get("sort") or "created_at", request.args.get("direction") or "desc")
        return render_template(
            "admin/submissions/list.html",
            form=form,
            fields=fields,
            submissions=submissions,
            filters=request.args,
            submission_value=submission_value,
            filter_fields=build_filter_fields(fields, submissions),
            officer_decisions=OFFICER_DECISIONS,
            status_label=lambda status: admin_status_label(status, form),
        )


@bp.route("/forms/<int:form_id>/submissions/<int:submission_pk>", methods=["GET", "POST"])
@login_required
def submission_detail(form_id: int, submission_pk: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        if submission.form_slug != form.slug:
            abort(404)
        if request.method == "POST":
            current_app.extensions["services"].workflow_service.transition_submission(
                submission,
                request.form.get("process_status", "").strip() or submission.process_status,
                actor="officer",
                reason="admin_manual_status_edit",
            )
            submission.officer_decision = request.form.get("officer_decision", "").strip()
            submission.updated_at = datetime.now(timezone.utc)
            db.commit()
            flash("Status zgloszenia zostal zmieniony.", "success")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        submission_data = {column.name: getattr(submission, column.name) for column in submission.__table__.columns}
        services = current_app.extensions["services"]
        files = services.submission_document_service.list_documents(submission.submission_id)
        workflow_history = services.submission_workflow_history_service.list_history(submission_data)
        decision_history = services.submission_decision_service.list_decisions(submission_data)
        return render_template(
            "admin/submissions/detail.html",
            form=form,
            submission=submission,
            files=files,
            workflow_history=workflow_history,
            decision_history=decision_history,
            status_label=lambda status: admin_status_label(status, form),
        )


@bp.post("/forms/<int:form_id>/submissions/<int:submission_pk>/decision")
@login_required
def submission_decision_update(form_id: int, submission_pk: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        if submission.form_slug != form.slug:
            abort(404)
        result = save_officer_decision(db, form, submission, request.form.get("officer_decision", ""), request.form.get("officer_decision_reason", ""))
        if result["missing_reason"]:
            flash("Podaj powod odrzucenia albo skierowania wniosku do poprawy.", "error")
            return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))
        if result["schema_warning"]:
            flash(
                "Decyzja zostala zapisana, ale audyt decyzji wymaga migracji schematu P4 (uruchom check_p4_schema.py i alembic upgrade head).",
                "warning",
            )
        flash("Decyzja urzednika zostala zapisana.", "success")
    if result["send_mail"]:
        try:
            current_app.extensions["services"].mail_dispatch_service.dispatch_decision_email(result["public_submission_id"], result["decision"])
        except Exception as exc:
            current_app.logger.exception("Nie udalo sie wyslac maila decyzji dla %s: %s", result["public_submission_id"], exc)
    return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))


@bp.post("/forms/<int:form_id>/submissions/decisions")
@login_required
def submissions_decisions_update(form_id: int):
    mail_queue = []
    saved_count = 0
    skipped_count = 0
    missing_reason_count = 0
    schema_warning = False
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        raw_ids = request.form.getlist("submission_row_ids")
        submission_ids = [int(item) for item in raw_ids if str(item).isdigit()]
        submissions = db.execute(
            select(FormSubmission).where(FormSubmission.id.in_(submission_ids), FormSubmission.form_slug == form.slug)
        ).scalars().all()
        for submission in submissions:
            decision = request.form.get(f"officer_decision_{submission.id}", "")
            reason = request.form.get(f"officer_decision_reason_{submission.id}", "")
            result = save_officer_decision(db, form, submission, decision, reason, skip_unchanged=True)
            if result["missing_reason"]:
                missing_reason_count += 1
                continue
            if result["skipped"]:
                skipped_count += 1
                continue
            saved_count += 1
            schema_warning = schema_warning or result["schema_warning"]
            if result["send_mail"]:
                mail_queue.append((result["public_submission_id"], result["decision"]))
        if schema_warning:
            flash(
                "Czesc decyzji zostala zapisana bez audytu P4, bo schemat bazy wymaga migracji.",
                "warning",
            )
        if missing_reason_count:
            flash(f"Pominieto {missing_reason_count} decyzji: podaj powod odrzucenia albo poprawy.", "error")
        flash(f"Zapisano decyzje: {saved_count}. Bez zmian: {skipped_count}.", "success" if saved_count else "warning")
    for public_submission_id, decision in mail_queue:
        try:
            current_app.extensions["services"].mail_dispatch_service.dispatch_decision_email(public_submission_id, decision)
        except Exception as exc:
            current_app.logger.exception("Nie udalo sie wyslac maila decyzji dla %s: %s", public_submission_id, exc)
    return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))


def save_officer_decision(db, form, submission, decision_value: str, reason_value: str, *, skip_unchanged: bool = False) -> dict:
    decision = str(decision_value or "").strip()
    allowed_decisions = {value for value, _ in OFFICER_DECISIONS}
    if decision not in allowed_decisions:
        abort(400)

    reason = str(reason_value or "").strip() if decision in {"rejected", "correction"} else ""
    if decision in {"rejected", "correction"} and not reason:
        return {"missing_reason": True, "skipped": False, "schema_warning": False, "send_mail": False}

    previous_decision = submission.officer_decision or ""
    previous_reason = submission.officer_decision_reason or ""
    previous_status = submission.process_status
    public_submission_id = submission.submission_id
    if skip_unchanged and decision == previous_decision and reason == previous_reason:
        return {"missing_reason": False, "skipped": True, "schema_warning": False, "send_mail": False}

    submission.officer_decision = decision
    submission.officer_decision_reason = reason
    if decision == "accepted":
        target_status = (
            ProcessStatus.ACCEPTED_WAITING_FOR_ADDITIONAL_FIELDS.value
            if form_has_additional_fields(form)
            else ProcessStatus.OFFICER_ACCEPTED.value
        )
    elif decision == "rejected":
        target_status = ProcessStatus.OFFICER_REJECTED.value
    elif decision == "correction":
        target_status = WAITING_FOR_CORRECTION
        submission.correction_required = "Tak"
        submission.correction_message = reason
        submission.correction_requested_at = datetime.now(timezone.utc)
        submission.workflow_step = "waiting_for_correction"
    else:
        target_status = submission.process_status

    if decision != "correction":
        submission.correction_required = "Nie"
    current_app.extensions["services"].workflow_service.transition_submission(
        submission,
        target_status,
        actor="officer",
        reason="officer_decision",
    )
    submission.updated_at = datetime.now(timezone.utc)
    db.commit()

    schema_warning = False
    try:
        db.add(
            SubmissionDecision(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                decision=decision,
                justification=submission.officer_decision_reason,
                officer_id=getattr(g.admin_user, "id", None),
                officer_email=getattr(g.admin_user, "email", ""),
                previous_status=previous_status or "",
                target_status=target_status or "",
                email_requested=decision in {"accepted", "rejected"} and decision != previous_decision,
                email_sent=False,
                decided_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        schema_warning = True
        current_app.logger.warning(
            "schema_mismatch area=decisions submission_id=%s reason=submission_decisions_unavailable error=%s",
            public_submission_id,
            exc.__class__.__name__,
            exc_info=True,
        )

    return {
        "decision": decision,
        "missing_reason": False,
        "public_submission_id": public_submission_id,
        "schema_warning": schema_warning,
        "send_mail": decision in {"accepted", "rejected"} and decision != previous_decision,
        "skipped": False,
    }
