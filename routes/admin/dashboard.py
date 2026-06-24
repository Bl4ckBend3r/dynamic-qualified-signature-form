from __future__ import annotations

from flask import current_app, g, render_template
from sqlalchemy import func, select
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
        submissions_count = db.execute(submissions_query).scalar() or 0
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
        email_errors_count = db.execute(select(func.count(EmailLog.id)).where(EmailLog.status == "failed")).scalar() or 0
    return render_template(
        "admin/dashboard.html",
        forms_count=forms_count,
        submissions_count=submissions_count,
        pending_count=pending_count,
        documents_count=documents_count,
        email_errors_count=email_errors_count,
    )
