from __future__ import annotations

from flask import current_app, g, render_template
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from models import EmailLog, FormSubmission, SubmissionFile

from . import (
    ROLE_SUPER_ADMIN,
    accessible_form_ids,
    accessible_form_slugs,
    bp,
    count_accessible_forms,
    db_session_factory,
    login_required,
)


@bp.get("/dashboard")
@login_required
def dashboard():
    user = g.admin_user
    with db_session_factory()() as db:
        form_ids = accessible_form_ids(db, user)
        forms_count = count_accessible_forms(db, user, form_ids)
        submissions_query = select(func.count(FormSubmission.id))
        if user.role != ROLE_SUPER_ADMIN:
            slugs = accessible_form_slugs(db, form_ids)
            submissions_query = submissions_query.where(FormSubmission.form_slug.in_(slugs or [""]))
        else:
            slugs = None
        submissions_count = db.execute(submissions_query).scalar() or 0
        assignment_queues = current_app.extensions["services"].submission_assignment_service.queue_counts(
            db, user_id=user.id, form_slugs=slugs
        )
        pending_query = select(func.count(FormSubmission.id)).where(
            FormSubmission.process_status.in_(["FORM_SUBMITTED", "WAITING_FOR_OFFICER_DECISION"])
        )
        if user.role != ROLE_SUPER_ADMIN:
            slugs = accessible_form_slugs(db, form_ids)
            pending_query = pending_query.where(FormSubmission.form_slug.in_(slugs or [""]))
        pending_count = db.execute(pending_query).scalar() or 0
        try:
            documents_count = db.execute(select(func.count(SubmissionFile.id))).scalar() or 0
        except SQLAlchemyError as exc:
            db.rollback()
            current_app.logger.warning(
                "schema_mismatch area=documents submission_id=dashboard reason=submission_files_unavailable error=%s",
                exc.__class__.__name__,
                exc_info=True,
            )
            documents_count = 0
        email_scope = []
        if user.role != ROLE_SUPER_ADMIN:
            email_scope.append(EmailLog.form_id.in_(form_ids or [-1]))
        try:
            delivery_scope = [
                or_(EmailLog.event_type.is_(None), EmailLog.event_type != "smtp_test"),
                *email_scope,
            ]
            sent_query = select(func.count(EmailLog.id)).where(EmailLog.status == "sent", *delivery_scope)
            failed_query = select(func.count(EmailLog.id)).where(EmailLog.status == "failed", *delivery_scope)
            last_attempt_query = select(func.max(EmailLog.created_at)).where(*delivery_scope)
            last_errors_query = (
                select(EmailLog)
                .where(EmailLog.status == "failed", *delivery_scope)
                .order_by(EmailLog.created_at.desc(), EmailLog.id.desc())
                .limit(5)
            )
            email_sent_count = db.execute(sent_query).scalar() or 0
            email_errors_count = db.execute(failed_query).scalar() or 0
            last_email_attempt_at = db.execute(last_attempt_query).scalar()
            last_email_errors = db.execute(last_errors_query).scalars().all()
            smtp_scope = [EmailLog.event_type == "smtp_test", *email_scope]
            smtp_success_count = db.execute(
                select(func.count(EmailLog.id)).where(EmailLog.status == "sent", *smtp_scope)
            ).scalar() or 0
            smtp_failure_count = db.execute(
                select(func.count(EmailLog.id)).where(EmailLog.status == "failed", *smtp_scope)
            ).scalar() or 0
            last_smtp_attempt_at = db.execute(
                select(func.max(EmailLog.created_at)).where(*smtp_scope)
            ).scalar()
            last_smtp_error = db.execute(
                select(EmailLog)
                .where(EmailLog.status == "failed", *smtp_scope)
                .order_by(EmailLog.created_at.desc(), EmailLog.id.desc())
                .limit(1)
            ).scalar_one_or_none()
        except SQLAlchemyError:
            db.rollback()
            current_app.logger.exception(
                "Nie udało się odczytać statystyk email_logs. "
                "Brakuje kolumn w bazie danych. Uruchom: alembic upgrade head"
            )
            email_sent_count = 0
            email_errors_count = 0
            last_email_attempt_at = None
            last_email_errors = []
            smtp_success_count = 0
            smtp_failure_count = 0
            last_smtp_attempt_at = None
            last_smtp_error = None
    return render_template(
        "admin/dashboard.html",
        forms_count=forms_count,
        submissions_count=submissions_count,
        pending_count=pending_count,
        documents_count=documents_count,
        email_errors_count=email_errors_count,
        email_sent_count=email_sent_count,
        last_email_attempt_at=last_email_attempt_at,
        last_email_errors=last_email_errors,
        smtp_success_count=smtp_success_count,
        smtp_failure_count=smtp_failure_count,
        last_smtp_attempt_at=last_smtp_attempt_at,
        last_smtp_error=last_smtp_error,
        assignment_queues=assignment_queues,
    )
