from __future__ import annotations

import html
from datetime import datetime, timezone

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from models import (
    EmailLog,
    Form,
    FormSubmission,
    MailFooter,
    MailTemplate,
    SubmissionDecision,
    SubmissionFile,
    SubmissionWorkflowEvent,
    User,
)
from services.admin_form_service import form_has_additional_fields
from services.admin_workflow_view_service import build_admin_workflow_view
from services.beneficiary_agreement_service import (
    AGREEMENT_DECISION_ROLES,
    BeneficiaryAgreementDecisionError,
    can_edit_application_decision,
)
from services.blocked_agreement_admin_service import BlockedAgreementActionError
from services.admin_submission_service import (
    admin_status_label,
    build_status_filter_options,
    build_submission_detail_sections,
    build_filter_fields,
    filter_submissions,
    paginate_submissions,
    sort_submissions,
    submission_value,
)
from services.process_service import ProcessStatus
from services.process_instruction_service import build_process_instruction_view
from services.office_signed_agreement_service import OfficeSignedAgreementError
from services.training_agreement_service import get_training_selection_field
from services.training_availability_service import TrainingAvailabilityService
from services.training_service import format_price_pln
from services.submission_stage_rollback_service import ALLOWED_ROLES, StageRollbackError
from services.submission_correction_service import SubmissionCorrectionError
from statuses import WAITING_FOR_CORRECTION

from . import (
    OFFICER_DECISIONS,
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    active_fields_for_form,
    bp,
    can_manage_form,
    db_session_factory,
    ensure_form_access,
    list_accessible_forms,
    login_required,
    role_required,
)


def _pagination_urls(endpoint: str, pagination: dict, **route_values) -> dict[str, str]:
    values = request.args.to_dict(flat=True)
    result = {"previous": "", "next": ""}
    if pagination["has_previous"]:
        result["previous"] = url_for(endpoint, **route_values, **{**values, "page": pagination["page"] - 1})
    if pagination["has_next"]:
        result["next"] = url_for(endpoint, **route_values, **{**values, "page": pagination["page"] + 1})
    return result


def _submission_sort_urls(endpoint: str, **route_values) -> dict[str, str]:
    current_sort = request.args.get("sort") or "created_at"
    current_direction = request.args.get("direction") if request.args.get("direction") in {"asc", "desc"} else "desc"
    result = {}
    for field in ("submission_id", "full_name", "email", "telefon", "created_at", "form_slug", "process_status", "workflow_stage"):
        values = request.args.to_dict(flat=True)
        values.pop("page", None)
        values["sort"] = field
        values["direction"] = "desc" if current_sort == field and current_direction == "asc" else "asc"
        result[field] = url_for(endpoint, **route_values, **values)
    return result


def _get_primary_agreement_number(submission) -> str:
    agreements = getattr(submission, "training_agreements", None) or []

    if isinstance(agreements, dict):
        agreements = agreements.get("agreements", [])

    if not isinstance(agreements, list):
        return ""

    for agreement in agreements:
        if not isinstance(agreement, dict):
            continue

        number = (
            agreement.get("agreement_number")
            or agreement.get("number")
            or agreement.get("agreementNo")
            or ""
        )

        if number:
            return str(number)

    return ""


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
        requested_form_id = str(request.args.get("form_id") or "").strip()
        selected_form = next((form for form in forms if str(form.id) == requested_form_id), None)
        filter_args = request.args.to_dict(flat=True)
        if requested_form_id:
            filter_args["form_slug"] = selected_form.slug if selected_form else "__invalid_form__"
        submissions = filter_submissions(
            submissions,
            filter_args,
            timezone_name=current_app.config.get("APP_TIMEZONE", "Europe/Warsaw"),
        )
        submissions = sort_submissions(
            submissions,
            request.args.get("sort") or "created_at",
            request.args.get("direction") or "desc",
        )
        status_options = build_status_filter_options(forms, submissions)
        submissions, pagination = paginate_submissions(
            submissions, request.args.get("page"), request.args.get("per_page")
        )
        pagination_urls = _pagination_urls("admin.submissions_all", pagination)
        sort_urls = _submission_sort_urls("admin.submissions_all")
        return render_template(
            "admin/submissions/all.html",
            submissions=submissions,
            form_by_slug=form_by_slug,
            filters=request.args,
            status_label=admin_status_label,
            status_options=status_options,
            pagination=pagination,
            pagination_urls=pagination_urls,
            can_edit_application_decision=can_edit_application_decision,
            forms=forms,
            sort_urls=sort_urls,
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
        submissions = filter_submissions(
            submissions,
            request.args,
            timezone_name=current_app.config.get("APP_TIMEZONE", "Europe/Warsaw"),
        )
        submissions = sort_submissions(submissions, request.args.get("sort") or "created_at", request.args.get("direction") or "desc")
        status_options = build_status_filter_options(form, submissions)
        filter_fields = build_filter_fields(fields, submissions)
        submissions, pagination = paginate_submissions(
            submissions, request.args.get("page"), request.args.get("per_page")
        )
        pagination_urls = _pagination_urls("admin.submissions_list", pagination, form_id=form.id)
        sort_urls = _submission_sort_urls("admin.submissions_list", form_id=form.id)
        return render_template(
            "admin/submissions/list.html",
            form=form,
            fields=fields,
            submissions=submissions,
            filters=request.args,
            submission_value=submission_value,
            filter_fields=filter_fields,
            officer_decisions=OFFICER_DECISIONS,
            can_edit_application_decision=can_edit_application_decision,
            status_label=lambda status: admin_status_label(status, form),
            status_options=status_options,
            pagination=pagination,
            pagination_urls=pagination_urls,
            sort_urls=sort_urls,
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
            requested_status = request.form.get("process_status", "").strip()
            if requested_status and requested_status != submission.process_status:
                flash("Statusu nie można zmieniać ręcznie. Użyj kontrolowanej funkcji „Cofnij etap”.", "error")
                return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
            flash("Użyj aktywnej decyzji w odpowiedniej sekcji workflow.", "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        submission_data = {column.name: getattr(submission, column.name) for column in submission.__table__.columns}
        services = current_app.extensions["services"]
        files = services.submission_document_service.list_documents(submission.submission_id)
        workflow_history = services.submission_workflow_history_service.list_history(submission_data)
        decision_history = services.submission_decision_service.list_decisions(submission_data)
        can_review_agreement = services.beneficiary_agreement_service.can_review(db, submission)
        office_signed_agreement_view = {
            "folder": services.office_signed_agreement_service.folder_for_form(form),
            "expected_filename": str(submission.agreement_signed_filename or "").strip(),
            "available": submission.process_status == ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value,
        }
        workflow_view = build_admin_workflow_view(
            submission,
            decisions=decision_history.get("decisions") or [],
            can_review_agreement=can_review_agreement,
        )
        detail_view = build_submission_detail_sections(form, submission)
        training_field = get_training_selection_field(form.definition_json or {})
        participant_training_view = None
        if training_field:
            availability = TrainingAvailabilityService(
                services.submission_repository
            ).availability_for_field(
                form_slug=form.slug,
                field=training_field,
                current_submission_id=submission.submission_id,
            )
            training_selection_view = services.submission_training_service.selection_view(
                db,
                submission,
                training_field,
                availability,
                form=form,
            )
            participant_training_view = training_selection_view["summary"]
            participant_training_view["catalog"] = training_selection_view["catalog"]
            for item in participant_training_view["items"]:
                item["agreement_url"] = (
                    url_for("documents.download_pdf", slug=form.slug, filename=item["agreement_filename"], token=submission.access_token)
                    if item.get("agreement_filename") else ""
                )
                item["signed_agreement_url"] = (
                    url_for("documents.download_signed_pdf", slug=form.slug, filename=item["signed_agreement_filename"], token=submission.access_token)
                    if item.get("signed_agreement_filename") else ""
                )
                item["office_signed_agreement_url"] = (
                    url_for("documents.download_signed_pdf", slug=form.slug, filename=item["office_signed_agreement_filename"], token=submission.access_token)
                    if item.get("office_signed_agreement_filename") else ""
                )
            currency = str(training_field.get("currency") or "PLN")
            participant_training_view.update(
                limit_total_formatted=format_price_pln(
                    participant_training_view["limit_total"], currency
                )
                if participant_training_view["limit_total"] is not None
                else None,
                limit_used_formatted=format_price_pln(
                    participant_training_view["limit_used"], currency
                ),
                limit_remaining_formatted=format_price_pln(
                    participant_training_view["limit_remaining"], currency
                )
                if participant_training_view["limit_remaining"] is not None
                else None,
                limit_pending_formatted=format_price_pln(
                    participant_training_view["limit_pending"], currency
                ),
            )
            db.commit()
        can_manage = can_manage_form(db, g.admin_user, form.id)
        is_agreement_blocked = (
            submission.process_status == ProcessStatus.AGREEMENT_BLOCKED.value
            or str(submission.agreement_blocked or "").strip().lower() == "tak"
        )
        block_data = dict(submission.data_json or {})
        blocked_agreement_view = {
            "is_blocked": is_agreement_blocked,
            "reason": str(submission.agreement_block_reason or "").strip(),
            "blocked_at": block_data.get("_agreement_blocked_at"),
            "source": str(block_data.get("_agreement_block_source") or "Warunki deklaracji"),
            "can_manage": can_manage and g.admin_user.role in {ROLE_ADMIN, ROLE_SUPER_ADMIN},
            "can_unblock": can_manage and g.admin_user.role == ROLE_SUPER_ADMIN,
            "can_reject_final": can_manage and g.admin_user.role in {ROLE_ADMIN, ROLE_SUPER_ADMIN},
        }
        rollback_options = []
        if can_manage and g.admin_user.role in ALLOWED_ROLES:
            rollback_options = services.submission_stage_rollback_service.get_allowed_targets(
                db,
                submission,
                actor_role=g.admin_user.role,
                form_config=form.definition_json or {},
            )
        correspondence_logs = db.execute(
            select(EmailLog)
            .where(
                (EmailLog.submission_id == submission.id)
                | (EmailLog.public_submission_id == submission.submission_id)
            )
            .order_by(EmailLog.created_at.desc(), EmailLog.id.desc())
        ).scalars().all()
        template_ids = {item.template_id for item in correspondence_logs if item.template_id}
        sender_ids = {item.sent_by_id for item in correspondence_logs if item.sent_by_id}
        template_names = {
            item.id: item.name
            for item in db.execute(select(MailTemplate).where(MailTemplate.id.in_(template_ids))).scalars().all()
        } if template_ids else {}
        sender_names = {
            item.id: item.email
            for item in db.execute(select(User).where(User.id.in_(sender_ids))).scalars().all()
        } if sender_ids else {}
        correspondence = [
            {
                "log": item,
                "template_name": template_names.get(item.template_id, "System / bez szablonu"),
                "sender_name": sender_names.get(item.sent_by_id, "System"),
            }
            for item in correspondence_logs
        ]
        return render_template(
            "admin/submissions/detail.html",
            form=form,
            submission=submission,
            detail_view=detail_view,
            files=files,
            workflow_history=workflow_history,
            decision_history=decision_history,
            workflow_view=workflow_view,
            rollback_options=rollback_options,
            blocked_agreement_view=blocked_agreement_view,
            office_signed_agreement_view=office_signed_agreement_view,
            participant_training_view=participant_training_view,
            correspondence=correspondence,
            status_label=lambda status: admin_status_label(status, form),
            read_only=not can_manage,
        )


@bp.post("/submissions/<submission_id>/rollback-stage")
@login_required
@role_required(ROLE_ADMIN, ROLE_SUPER_ADMIN)
def submission_stage_rollback(submission_id: str):
    public_submission_id = str(submission_id or "").strip()
    with db_session_factory()() as db:
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == public_submission_id)
        ).scalar_one_or_none()
        if submission is None:
            abort(404)
        form = db.execute(select(Form).where(Form.slug == submission.form_slug)).scalar_one_or_none()
        if form is None:
            abort(404)
        ensure_form_access(db, form.id, manage=True)

        target_status = request.form.get("target_status", "").strip()
        reason = request.form.get("rollback_reason", "").strip()
        send_notification = request.form.get("send_notification") == "on"
        service = current_app.extensions["services"].submission_stage_rollback_service
        try:
            result = service.rollback(
                db,
                submission,
                target_status=target_status,
                reason=reason,
                actor=g.admin_user,
                form_config=form.definition_json or {},
            )
            db.commit()
        except StageRollbackError as exc:
            db.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        except SQLAlchemyError:
            db.rollback()
            current_app.logger.exception(
                "submission_stage_rollback_failed public_submission_id=%s",
                submission.submission_id,
            )
            flash("Nie udało się cofnąć etapu. Spróbuj ponownie.", "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))

        current_app.logger.info(
            "submission_stage_rollback public_submission_id=%s internal_submission_id=%s "
            "previous_status=%s new_status=%s actor_role=%s superseded_files=%s",
            submission.submission_id,
            submission.id,
            result.previous_status,
            result.new_status,
            g.admin_user.role,
            result.superseded_files,
        )

        mail_result = None
        if send_notification:
            if not str(submission.email or "").strip():
                flash("Etap cofnięto, ale użytkownik nie ma adresu e-mail — powiadomienie nie zostało wysłane.", "warning")
            else:
                try:
                    mail_result = _send_stage_rollback_email(db, form, submission, reason, result.new_status)
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    current_app.logger.exception(
                        "stage_rollback_mail_failed public_submission_id=%s error=%s",
                        submission.submission_id,
                        exc.__class__.__name__,
                    )
                    mail_result = None
                if mail_result is None:
                    flash("Etap cofnięto, ale nie udało się przygotować powiadomienia e-mail.", "warning")
                elif mail_result.status != "sent":
                    flash(
                        "Etap cofnięto, ale powiadomienie e-mail nie zostało wysłane: "
                        + (mail_result.error_message or "brak szczegółów błędu"),
                        "warning",
                    )

        flash(
            f"Cofnięto etap zgłoszenia do: {admin_status_label(result.new_status, form)}.",
            "success",
        )
        return redirect(request.form.get("next") or url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))


@bp.post("/submissions/<submission_id>/agreement/unblock")
@login_required
@role_required(ROLE_SUPER_ADMIN)
def submission_agreement_unblock(submission_id: str):
    with db_session_factory()() as db:
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == str(submission_id).strip())
        ).scalar_one_or_none()
        if submission is None:
            abort(404)
        form = db.execute(select(Form).where(Form.slug == submission.form_slug)).scalar_one_or_none()
        if form is None:
            abort(404)
        ensure_form_access(db, form.id, manage=True)
        reason = request.form.get("reason", "").strip()
        try:
            result = current_app.extensions["services"].blocked_agreement_admin_service.unblock(
                db,
                submission,
                reason=reason,
                actor=g.admin_user,
            )
            db.commit()
        except (BlockedAgreementActionError, PermissionError) as exc:
            db.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        except SQLAlchemyError:
            db.rollback()
            current_app.logger.exception(
                "manual_agreement_unblock_failed public_submission_id=%s",
                submission.submission_id,
            )
            flash("Nie udało się odblokować umowy. Spróbuj ponownie.", "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))

        _log_blocked_agreement_action(
            "AGREEMENT_UNBLOCKED",
            submission,
            result,
            reason=reason,
        )
        flash("Umowa została odblokowana i może przejść do generowania.", "success")
        return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))


@bp.post("/submissions/<submission_id>/reject-final")
@login_required
@role_required(ROLE_ADMIN, ROLE_SUPER_ADMIN)
def submission_reject_final(submission_id: str):
    with db_session_factory()() as db:
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == str(submission_id).strip())
        ).scalar_one_or_none()
        if submission is None:
            abort(404)
        form = db.execute(select(Form).where(Form.slug == submission.form_slug)).scalar_one_or_none()
        if form is None:
            abort(404)
        ensure_form_access(db, form.id, manage=True)
        reason = request.form.get("reason", "").strip()
        send_notification = request.form.get("send_notification") == "on"
        try:
            result = current_app.extensions["services"].blocked_agreement_admin_service.reject_final(
                db,
                submission,
                reason=reason,
                actor=g.admin_user,
                email_requested=send_notification,
            )
            db.commit()
        except (BlockedAgreementActionError, PermissionError) as exc:
            db.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        except SQLAlchemyError:
            db.rollback()
            current_app.logger.exception(
                "agreement_block_final_rejection_failed public_submission_id=%s",
                submission.submission_id,
            )
            flash("Nie udało się zakończyć procesu jako odrzuconego. Spróbuj ponownie.", "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))

        _log_blocked_agreement_action(
            "AGREEMENT_BLOCK_REJECTED_FINAL",
            submission,
            result,
            reason=reason,
        )
        if send_notification:
            mail_result = current_app.extensions["services"].mail_dispatch_service.dispatch_decision_email(
                submission.submission_id,
                "rejected",
            )
            if mail_result.status not in {"sent", "queued"}:
                flash("Proces zakończono, ale powiadomienie e-mail nie zostało wysłane.", "warning")
        flash("Proces został zakończony jako odrzucony.", "success")
        return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))


def _log_blocked_agreement_action(event_type: str, submission, result, *, reason: str) -> None:
    try:
        current_app.extensions["services"].audit_log_service.log_event(
            event_type,
            submission.submission_id,
            submission.form_slug,
            old_value=result.previous_status,
            new_value=result.new_status,
            actor=str(g.admin_user.email or g.admin_user.role),
            metadata={
                "reason": reason,
                "original_block_reason": result.previous_block_reason,
                "actor_id": g.admin_user.id,
                "actor_role": g.admin_user.role,
            },
        )
    except Exception as exc:
        current_app.logger.exception(
            "blocked_agreement_audit_failed public_submission_id=%s error=%s",
            submission.submission_id,
            exc.__class__.__name__,
        )


@bp.post("/submissions/<submission_id>/return-for-correction")
@login_required
@role_required(ROLE_ADMIN, ROLE_SUPER_ADMIN)
def submission_return_for_correction(submission_id: str):
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}
    reason = str(payload.get("reason") or "").strip()
    message_to_user = str(payload.get("message_to_user") or "").strip()
    clear_submission = _request_bool(payload, "clear_submission", default=True)
    send_email = _request_bool(payload, "send_email", default=True)

    with db_session_factory()() as db:
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == str(submission_id).strip())
        ).scalar_one_or_none()
        if submission is None:
            abort(404)
        form = db.execute(select(Form).where(Form.slug == submission.form_slug)).scalar_one_or_none()
        if form is None:
            abort(404)
        ensure_form_access(db, form.id, manage=True)
        if not str(submission.access_token or "").strip():
            submission.access_token = current_app.extensions["services"].access_token_service.generate_token()
        try:
            result = current_app.extensions["services"].submission_correction_service.return_for_correction(
                db,
                submission,
                form_config=form.definition_json or {},
                reason=reason,
                message_to_user=message_to_user,
                clear_submission=clear_submission,
                actor=g.admin_user,
            )
            db.commit()
        except (SubmissionCorrectionError, PermissionError) as exc:
            db.rollback()
            if request.is_json:
                return {"ok": False, "error": str(exc)}, 400
            flash(str(exc), "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        except SQLAlchemyError:
            db.rollback()
            current_app.logger.exception(
                "submission_return_for_correction_failed public_submission_id=%s",
                submission.submission_id,
            )
            if request.is_json:
                return {"ok": False, "error": "Nie udało się wysłać zgłoszenia do poprawy."}, 500
            flash("Nie udało się wysłać zgłoszenia do poprawy. Spróbuj ponownie.", "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))

        current_app.logger.info(
            "submission_returned_for_correction public_submission_id=%s internal_submission_id=%s "
            "previous_status=%s new_status=%s cleared=%s superseded_files=%s email_requested=%s",
            submission.submission_id,
            submission.id,
            result.previous_status,
            result.new_status,
            result.cleared,
            result.superseded_files,
            send_email,
        )
        try:
            current_app.extensions["services"].audit_log_service.log_event(
                "RETURNED_FOR_CORRECTION",
                submission.submission_id,
                submission.form_slug,
                old_value=result.previous_status,
                new_value=result.new_status,
                actor=str(g.admin_user.email or g.admin_user.role),
                metadata={
                    "reason": reason,
                    "message_to_user": message_to_user,
                    "cleared": result.cleared,
                    "superseded_files": result.superseded_files,
                },
            )
        except Exception as exc:
            current_app.logger.exception(
                "returned_for_correction_audit_failed public_submission_id=%s error=%s",
                submission.submission_id,
                exc.__class__.__name__,
            )
        mail_status = "not_requested"
        if send_email:
            if not result.recipient_email:
                mail_status = "skipped_no_recipient"
            else:
                try:
                    mail_result = current_app.extensions["services"].mail_dispatch_service.dispatch_returned_for_correction(
                        submission.submission_id,
                        reason=reason,
                        message_to_user=message_to_user,
                        cleared=result.cleared,
                        recipient=result.recipient_email,
                    )
                    mail_status = mail_result.status
                except Exception as exc:
                    mail_status = "failed"
                    current_app.logger.exception(
                        "returned_for_correction_mail_failed public_submission_id=%s error=%s",
                        submission.submission_id,
                        exc.__class__.__name__,
                    )

        if request.is_json:
            return {
                "ok": True,
                "submission_id": submission.submission_id,
                "previous_status": result.previous_status,
                "process_status": result.new_status,
                "cleared": result.cleared,
                "superseded_files": result.superseded_files,
                "mail_status": mail_status,
            }
        if send_email and mail_status not in {"sent", "queued"}:
            flash("Zgłoszenie wysłano do poprawy, ale wiadomość e-mail nie została wysłana.", "warning")
        flash("Zgłoszenie zostało wysłane do ponownego uzupełnienia.", "success")
        return redirect(request.form.get("next") or url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))


def _request_bool(payload, key: str, *, default: bool) -> bool:
    if key not in payload:
        return default if request.is_json else False
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "tak", "yes", "on"}


def _send_stage_rollback_email(db, form, submission, reason: str, target_status: str):
    template = db.execute(
        select(MailTemplate)
        .where(
            MailTemplate.form_id == form.id,
            MailTemplate.template_type == "stage_rollback",
            MailTemplate.is_active.is_(True),
        )
        .order_by(MailTemplate.id.desc())
    ).scalars().first()
    if template is None:
        template = MailTemplate(
            form_id=form.id,
            name="Cofnięcie etapu zgłoszenia",
            template_type="stage_rollback",
            subject="Cofnięto etap zgłoszenia {{ submission_id }}",
            content_html=(
                "<p>Etap Twojego zgłoszenia został cofnięty.</p>"
                "<p><strong>Aktualny status:</strong> {{ rollback_status_label }}</p>"
                "<p><strong>Powód:</strong> {{ rollback_reason }}</p>"
                "<p>{{ rollback_next_action }}</p>"
            ),
            content_text=(
                "Etap Twojego zgłoszenia został cofnięty.\n"
                "Aktualny status: {{ rollback_status_label }}\n"
                "Powód: {{ rollback_reason_text }}\n"
                "{{ rollback_next_action_text }}"
            ),
            use_platform_layout=True,
            is_active=True,
        )

    footer = db.execute(
        select(MailFooter)
        .where(
            MailFooter.is_active.is_(True),
            (MailFooter.form_id == form.id) | (MailFooter.form_id.is_(None)),
        )
        .order_by(MailFooter.form_id.desc(), MailFooter.is_default.desc(), MailFooter.id.desc())
    ).scalars().first()
    instruction = build_process_instruction_view(
        target_status,
        instruction_config=form.user_instruction_config,
        legacy_description=form.user_instruction,
    )
    next_action = str(instruction.get("next_action") or "").strip()
    if not next_action:
        next_action = "Sprawdź aktualny status i dalsze instrukcje w aplikacji."
    services = current_app.extensions["services"]
    files = services.submission_document_service.list_documents(submission.submission_id)
    return services.mail_dispatch_service.dispatch_to_submission(
        db=db,
        form=form,
        submission=submission,
        template=template,
        footer=footer,
        to_email=submission.email,
        subject_template=template.subject,
        event_type="stage_rollback",
        sent_by_id=g.admin_user.id,
        files=files,
        context_builders={
            "documents_to_sign_url_builder": lambda item: url_for(
                "documents.documents_to_sign", submission_id=item.submission_id, _external=True
            ),
            "document_url_builder": lambda item, filename: services.document_service.build_download_url(
                {"form_slug": item.form_slug, "submission_id": item.submission_id, "access_token": item.access_token},
                filename,
            ),
        },
        extra_context={
            "rollback_reason": html.escape(reason),
            "rollback_reason_text": reason,
            "rollback_status": target_status,
            "rollback_status_label": admin_status_label(target_status, form),
            "rollback_next_action": html.escape(next_action),
            "rollback_next_action_text": next_action,
        },
    )


def _notify_beneficiary_agreement_decision(
    db, form, submission, decision_result, *, force_resend: bool = False
) -> None:
    decision_record = decision_result.decision_record

    if not str(submission.email or "").strip():
        flash("Decyzję zapisano, ale użytkownik nie ma adresu e-mail.", "warning")
        return

    confirmed = (
        decision_result.decision_status
        == ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value
    )

    template_type = (
        "agreement_signed_by_office"
        if confirmed
        else "agreement_rejected_by_office"
    )

    template = db.execute(
        select(MailTemplate)
        .where(
            MailTemplate.form_id == form.id,
            MailTemplate.is_active.is_(True),
            (
                (MailTemplate.template_type == template_type)
                | (MailTemplate.trigger_event == template_type)
                | (
                    MailTemplate.trigger_status
                    == ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value
                )
            ),
        )
        .order_by(
            MailTemplate.is_default_for_status.desc(),
            MailTemplate.id.desc(),
        )
    ).scalars().first()

    if template is None:
        template = MailTemplate(
            form_id=form.id,
            name=(
                "Umowa podpisana przez urząd"
                if confirmed
                else "Decyzja dotycząca podpisanej umowy"
            ),
            template_type=template_type,
            subject=(
                "Umowa została podpisana przez urząd"
                if confirmed
                else "Decyzja dotycząca umowy {{ submission_id }}"
            ),
            content_html=(
                (
                    "<p>Dzień dobry,</p>"
                    "<p>informujemy, że umowa dotycząca zgłoszenia "
                    "{{ public_submission_id }} została podpisana przez urząd.</p>"
                    "<p>Formularz: {{ form_title }}<br>"
                    "Numer zgłoszenia: {{ public_submission_id }}</p>"
                    "{{ signed_agreements_table }}"
                    "{% if signed_agreement_download_link %}"
                    "<p><a href=\"{{ signed_agreement_download_link }}\">"
                    "Pobierz podpisaną umowę</a></p>"
                    "{% endif %}"
                    "<p>Pozdrawiamy,<br>zespół platformy</p>"
                )
                if confirmed
                else (
                    "<p>Podpisana umowa wymaga poprawy.</p>"
                    "<p><strong>Powód:</strong> {{ agreement_decision_reason }}</p>"
                )
            ),
            content_text=(
                (
                    "Dzień dobry,\n\n"
                    "informujemy, że umowa dotycząca zgłoszenia "
                    "{{ public_submission_id }} została podpisana przez urząd.\n"
                    "Formularz: {{ form_title }}\n"
                    "Numer zgłoszenia: {{ public_submission_id }}\n"
                    "{{ signed_agreements_text }}\n"
                    "{{ signed_agreement_download_link }}\n\n"
                    "Pozdrawiamy,\n"
                    "zespół platformy"
                )
                if confirmed
                else (
                    "Podpisana umowa wymaga poprawy. "
                    "Powód: {{ agreement_decision_reason_text }}"
                )
            ),
            use_platform_layout=True,
            is_active=True,
        )

    footer = db.execute(
        select(MailFooter)
        .where(
            MailFooter.is_active.is_(True),
            (MailFooter.form_id == form.id) | (MailFooter.form_id.is_(None)),
        )
        .order_by(
            MailFooter.form_id.desc(),
            MailFooter.is_default.desc(),
            MailFooter.id.desc(),
        )
    ).scalars().first()

    services = current_app.extensions["services"]

    files = services.submission_document_service.list_documents(
        submission.submission_id
    )

    final_records = []
    attachments = []

    training_ids = {
        str(item)
        for item in (getattr(decision_result, "training_ids", ()) or ())
        if str(item).strip()
    }

    if confirmed:
        final_records = db.execute(
            select(SubmissionFile).where(
                SubmissionFile.submission_id == submission.id,
                SubmissionFile.document_type == "agreement_signed_by_office",
            )
        ).scalars().all()

        if training_ids:
            final_records = [
                item
                for item in final_records
                if str(getattr(item, "training_key", "") or "") in training_ids
            ]

        if not force_resend:
            final_records = [
                item
                for item in final_records
                if not bool(
                    (item.signature_validation_result or {}).get(
                        "office_signed_email_sent"
                    )
                )
            ]

        if not final_records:
            if training_ids:
                flash(
                    "Wiadomość dla tej finalnej umowy została już wysłana "
                    "albo nie znaleziono finalnego pliku.",
                    "info",
                )
            else:
                flash(
                    "Nie znaleziono finalnej umowy podpisanej przez urząd. "
                    "Mail nie został wysłany.",
                    "warning",
                )
            return

        final_names = {
            str(
                getattr(item, "filename", "")
                or getattr(item, "original_filename", "")
                or ""
            )
            for item in final_records
        }

        files = [
            item
            for item in files
            if str(item.get("document_type") or "") == "agreement_signed_by_office"
            and str(item.get("filename") or "") in final_names
        ]

        if not files:
            files = [
                {
                    "filename": (
                        getattr(item, "filename", "")
                        or getattr(item, "original_filename", "")
                    ),
                    "document_type": item.document_type,
                    "training_key": getattr(item, "training_key", ""),
                    "signed": True,
                }
                for item in final_records
            ]

        storage = (
            getattr(services, "storage", None)
            or getattr(services, "nextcloud_storage", None)
        )

        if storage is None:
            flash(
                "Brak skonfigurowanego storage. "
                "Mail z załącznikiem nie został wysłany.",
                "warning",
            )
            return

        for item in final_records:
            storage_path = (
                getattr(item, "storage_path", "")
                or getattr(item, "path", "")
                or getattr(item, "file_path", "")
                or ""
            )

            filename = (
                getattr(item, "original_filename", "")
                or getattr(item, "filename", "")
                or "umowa-podpisana-przez-urzad.pdf"
            )

            if not storage_path:
                current_app.logger.warning(
                    "office_signed_agreement_attachment_missing_path "
                    "submission_id=%s file_id=%s",
                    submission.id,
                    item.id,
                )
                continue

            try:
                content = storage.read_bytes(storage_path)
            except Exception as exc:
                current_app.logger.exception(
                    "office_signed_agreement_attachment_read_failed "
                    "submission_id=%s file_id=%s path=%s error=%s",
                    submission.id,
                    item.id,
                    storage_path,
                    exc,
                )
                continue

            attachments.append(
                {
                    "filename": filename,
                    "content": content,
                    "content_type": (
                        getattr(item, "mime_type", None) or "application/pdf"
                    ),
                }
            )

        if not attachments:
            flash(
                "Nie udało się pobrać finalnej umowy podpisanej przez urząd "
                "jako załącznika. Mail nie został wysłany.",
                "warning",
            )
            return

    signed_filename = str(
        (
            getattr(final_records[0], "original_filename", "")
            or getattr(final_records[0], "filename", "")
        )
        if final_records
        else ""
    ).strip()

    signed_link = ""

    if signed_filename:
        signed_link = services.document_service.build_download_url(
            {
                "form_slug": submission.form_slug,
                "submission_id": submission.submission_id,
                "access_token": submission.access_token,
            },
            signed_filename,
        )

    mail_result = services.mail_dispatch_service.dispatch_to_submission(
    db=db,
    form=form,
    submission=submission,
    template=template,
    footer=footer,
    to_email=submission.email,
    subject_template=template.subject,
    event_type=("agreement_signed_by_office" if confirmed else "agreement_rejected_by_office"),
    sent_by_id=g.admin_user.id,
    files=files,
    attachments=attachments,
    context_builders={
        "document_url_builder": lambda item, filename: services.document_service.build_download_url(
            {
                "form_slug": item.form_slug,
                "submission_id": item.submission_id,
                "access_token": item.access_token,
            },
            filename,
        ),
    },
    extra_context={
        "agreement_decision_reason": html.escape(decision_record.justification or ""),
        "agreement_decision_reason_text": decision_record.justification or "",
        "signed_agreement_download_link": signed_link,
        "signed_agreement_filename": signed_filename,
        "agreement_number": _get_primary_agreement_number(submission),
        "agreement_signed_by_office_at": decision_record.decided_at.isoformat() if decision_record.decided_at else "",
    },
)

    decision_record.email_sent = mail_result.sent
    decision_record.email_log_id = getattr(mail_result.log, "id", None)

    if confirmed and mail_result.sent:
        submission.office_agreement_signed_email_sent = "Tak"
        submission.office_agreement_signed_email_sent_for = (
            "agreement_signed_by_office"
        )

        sent_at = datetime.now(timezone.utc).isoformat()

        for item in final_records:
            metadata = dict(item.signature_validation_result or {})
            metadata.update(
                office_signed_email_sent=True,
                office_signed_email_sent_at=sent_at,
                office_signed_email_log_id=getattr(mail_result.log, "id", None),
            )
            item.signature_validation_result = metadata

    db.commit()

    if not mail_result.sent:
        flash(
            "Decyzję zapisano, ale e-mail nie został wysłany: "
            + (mail_result.error_message or "brak szczegółów błędu"),
            "warning",
        )

@bp.post("/forms/<int:form_id>/submissions/<int:submission_pk>/decision")
@login_required
@role_required(ROLE_ADMIN, ROLE_SUPER_ADMIN)
def submission_decision_update(form_id: int, submission_pk: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        if submission.form_slug != form.slug:
            abort(404)
        result = save_officer_decision(db, form, submission, request.form.get("officer_decision", ""), request.form.get("officer_decision_reason", ""))
        if result["invalid_stage"]:
            flash("Decyzja o wniosku jest dostępna tylko na etapie jego weryfikacji.", "error")
            return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))
        if result["missing_reason"]:
            flash("Podaj powod odrzucenia albo skierowania wniosku do poprawy.", "error")
            return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))
        if result["schema_warning"]:
            flash(
                "Decyzja zostala zapisana, ale audyt decyzji wymaga migracji schematu P4 (uruchom scripts/diagnostics/check_p4_schema.py i alembic upgrade head).",
                "warning",
            )
        flash("Decyzja urzednika zostala zapisana.", "success")
    return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))


@bp.post("/forms/<int:form_id>/submissions/<int:submission_pk>/beneficiary-agreement-decision")
@login_required
def beneficiary_agreement_decision_update(form_id: int, submission_pk: int):
    if g.admin_user.role not in AGREEMENT_DECISION_ROLES:
        abort(403)
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        if submission.form_slug != form.slug:
            abort(404)
        send_notification = request.form.get("send_notification") == "on"
        try:
            result = current_app.extensions["services"].beneficiary_agreement_service.decide(
                db,
                submission,
                decision=request.form.get("agreement_decision", ""),
                reason=request.form.get("agreement_decision_reason", ""),
                actor=g.admin_user,
                email_requested=send_notification,
            )
            db.commit()
        except BeneficiaryAgreementDecisionError as exc:
            db.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))

        current_app.logger.info(
            "beneficiary_agreement_decision public_submission_id=%s internal_submission_id=%s "
            "previous_status=%s decision_status=%s final_status=%s actor_role=%s",
            submission.submission_id,
            submission.id,
            result.previous_status,
            result.decision_status,
            result.final_status,
            g.admin_user.role,
        )
        if send_notification:
            _notify_beneficiary_agreement_decision(db, form, submission, result)
        flash("Potwierdzenie podpisania umowy przez urząd zostało zapisane.", "success")
        return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))


@bp.post("/forms/<int:form_id>/submissions/<int:submission_pk>/check-office-signed-agreement")
@login_required
@role_required(ROLE_ADMIN, ROLE_SUPER_ADMIN)
def check_office_signed_agreement(form_id: int, submission_pk: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        if submission.form_slug != form.slug:
            abort(404)
        try:
            result = current_app.extensions["services"].office_signed_agreement_service.check(
                db, form, submission, actor=g.admin_user,
                training_key=request.form.get("training_key") or None,
                email_requested=request.form.get("send_notification") == "on",
            )
        except OfficeSignedAgreementError as exc:
            db.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        if not result.found:
            details = f" Oczekiwane nazwy: {', '.join(result.expected_filenames)}. Folder: {result.folder}."
            flash(result.message + details, "warning" if result.missing_filenames or result.errors else "info")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        db.commit()
        if request.form.get("send_notification") == "on":
            _notify_beneficiary_agreement_decision(db, form, submission, result)
        flash(result.message, "success")
        return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))


@bp.post("/forms/<int:form_id>/submissions/check-office-signed-agreements")
@login_required
@role_required(ROLE_ADMIN, ROLE_SUPER_ADMIN)
def check_office_signed_agreements_bulk(form_id: int):
    checked_submissions = found = missing = already = checked_agreements = 0
    errors = []
    notifications = []
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        public_ids = list(dict.fromkeys(request.form.getlist("submission_ids")))
        submissions = db.execute(
            select(FormSubmission).where(
                FormSubmission.form_slug == form.slug,
                FormSubmission.submission_id.in_(public_ids),
            )
        ).scalars().all() if public_ids else []
        for submission in submissions:
            checked_submissions += 1
            try:
                result = current_app.extensions["services"].office_signed_agreement_service.check(
                    db,
                    form,
                    submission,
                    actor=g.admin_user,
                    email_requested=request.form.get("send_notification") == "on",
                )
            except OfficeSignedAgreementError as exc:
                errors.append(f"{submission.submission_id}: {exc}")
                continue
            checked_agreements += result.checked_count
            found += result.found_count
            missing += len(result.missing_filenames)
            already += len(result.already_confirmed)
            errors.extend(f"{submission.submission_id}: {error}" for error in result.errors)
            if result.found:
                notifications.append((submission, result))
        db.commit()
        if request.form.get("send_notification") == "on":
            for submission, result in notifications:
                _notify_beneficiary_agreement_decision(db, form, submission, result)
    flash(
        f"Sprawdzono zgłoszenia: {checked_submissions}; umowy: {checked_agreements}; "
        f"znalezione: {found}; brakujące: {missing}; już zatwierdzone: {already}; błędy: {len(errors)}.",
        "success" if found and not errors else "warning" if missing or errors else "info",
    )
    for error in errors[:10]:
        flash(error, "warning")
    return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))


@bp.post("/forms/<int:form_id>/submissions/<int:submission_pk>/send-office-signed-agreements")
@login_required
@role_required(ROLE_ADMIN, ROLE_SUPER_ADMIN)
def send_office_signed_agreements(form_id: int, submission_pk: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        if submission.form_slug != form.slug:
            abort(404)
        training_key = str(request.form.get("training_key") or "").strip()
        query = select(SubmissionFile).where(
            SubmissionFile.submission_id == submission.id,
            SubmissionFile.document_type == "agreement_signed_by_office",
        )
        final_files = db.execute(query).scalars().all()
        if training_key:
            final_files = [item for item in final_files if str(item.training_key or "") == training_key]
        if not final_files:
            flash("Nie znaleziono finalnej umowy podpisanej przez urząd.", "warning")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        now = datetime.now(timezone.utc)
        decision = SubmissionDecision(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug=submission.form_slug,
            decision="office_agreement_email_resend",
            justification="Administrator świadomie zlecił ponowną wysyłkę finalnej umowy.",
            officer_id=g.admin_user.id,
            officer_email=g.admin_user.email,
            previous_status=submission.process_status,
            target_status=submission.process_status,
            email_requested=True,
            email_sent=False,
            decided_at=now,
        )
        db.add(decision)
        db.flush()
        result = type("OfficeMailResult", (), {
            "decision_record": decision,
            "decision_status": ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value,
            "training_ids": tuple(str(item.training_key or "") for item in final_files),
        })()
        db.commit()
        _notify_beneficiary_agreement_decision(db, form, submission, result, force_resend=True)
        if decision.email_sent:
            flash("Finalne umowy zostały ponownie wysłane do uczestnika.", "success")
    return redirect(url_for("admin.submission_detail", form_id=form_id, submission_pk=submission_pk))


@bp.post("/forms/<int:form_id>/submissions/decisions")
@login_required
@role_required(ROLE_ADMIN, ROLE_SUPER_ADMIN)
def submissions_decisions_update(form_id: int):
    saved_count = 0
    skipped_count = 0
    missing_reason_count = 0
    invalid_stage_count = 0
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
            if result["invalid_stage"]:
                invalid_stage_count += 1
                continue
            if result["missing_reason"]:
                missing_reason_count += 1
                continue
            if result["skipped"]:
                skipped_count += 1
                continue
            saved_count += 1
            schema_warning = schema_warning or result["schema_warning"]
        if schema_warning:
            flash(
                "Czesc decyzji zostala zapisana bez audytu P4, bo schemat bazy wymaga migracji.",
                "warning",
            )
        if missing_reason_count:
            flash(f"Pominieto {missing_reason_count} decyzji: podaj powod odrzucenia albo poprawy.", "error")
        if invalid_stage_count:
            flash(f"Pominieto {invalid_stage_count} decyzji: etap weryfikacji wniosku jest juz zakonczony.", "warning")
        flash(f"Zapisano decyzje: {saved_count}. Bez zmian: {skipped_count}.", "success" if saved_count else "warning")
    return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))


@bp.post("/forms/<int:form_id>/submissions/delete-selected")
@login_required
def submissions_delete_selected(form_id: int):
    raw_ids = request.form.getlist("submission_pk_ids")
    submission_ids = [int(item) for item in raw_ids if str(item).isdigit()]
    if not submission_ids:
        flash("Zaznacz co najmniej jedno zgłoszenie.", "error")
        return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        submissions = db.execute(
            select(FormSubmission).where(FormSubmission.id.in_(submission_ids), FormSubmission.form_slug == form.slug)
        ).scalars().all()
        if not submissions:
            flash("Nie znaleziono zgłoszeń do usunięcia albo nie masz uprawnień.", "error")
            return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))
        try:
            delete_submissions_transactionally(db, submissions)
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            current_app.logger.exception("Nie udało się usunąć zaznaczonych zgłoszeń dla formularza %s.", form_id)
            flash("Nie udało się usunąć zaznaczonych zgłoszeń. Spróbuj ponownie.", "error")
            return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))
    flash(f"Usunięto zgłoszenia: {len(submissions)}.", "success")
    return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))


def delete_submissions_transactionally(db, submissions: list[FormSubmission]) -> None:
    ids = [submission.id for submission in submissions]
    public_ids = [submission.submission_id for submission in submissions]
    db.query(SubmissionFile).filter(SubmissionFile.submission_id.in_(ids)).delete(synchronize_session=False)
    db.query(SubmissionDecision).filter(
        (SubmissionDecision.submission_id.in_(ids)) | (SubmissionDecision.public_submission_id.in_(public_ids))
    ).delete(synchronize_session=False)
    db.query(SubmissionWorkflowEvent).filter(
        (SubmissionWorkflowEvent.submission_id.in_(ids)) | (SubmissionWorkflowEvent.public_submission_id.in_(public_ids))
    ).delete(synchronize_session=False)
    db.query(EmailLog).filter(
        (EmailLog.submission_id.in_(ids)) | (EmailLog.public_submission_id.in_(public_ids))
    ).delete(synchronize_session=False)
    for submission in submissions:
        db.delete(submission)


def save_officer_decision(db, form, submission, decision_value: str, reason_value: str, *, skip_unchanged: bool = False) -> dict:
    if not can_edit_application_decision(submission):
        return {
            "invalid_stage": True,
            "missing_reason": False,
            "skipped": False,
            "schema_warning": False,
            "send_mail": False,
        }
    decision = str(decision_value or "").strip()
    allowed_decisions = {value for value, _ in OFFICER_DECISIONS}
    if decision not in allowed_decisions:
        abort(400)

    reason = str(reason_value or "").strip() if decision in {"rejected", "correction"} else ""
    if decision in {"rejected", "correction"} and not reason:
        return {"invalid_stage": False, "missing_reason": True, "skipped": False, "schema_warning": False, "send_mail": False}

    previous_decision = submission.officer_decision or ""
    previous_reason = submission.officer_decision_reason or ""
    previous_status = submission.process_status
    accepted_after_correction = decision == "accepted" and bool(submission.correction_completed_at)
    public_submission_id = submission.submission_id
    if skip_unchanged and decision == previous_decision and reason == previous_reason:
        return {"invalid_stage": False, "missing_reason": False, "skipped": True, "schema_warning": False, "send_mail": False}

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
                email_requested=False,
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

    mail_result = None
    if accepted_after_correction:
        try:
            mail_result = current_app.extensions["services"].mail_dispatch_service.dispatch_correction_accepted(
                public_submission_id
            )
        except Exception as exc:
            current_app.logger.exception(
                "correction_accepted_mail_failed public_submission_id=%s error=%s",
                public_submission_id,
                exc.__class__.__name__,
            )

    return {
        "decision": decision,
        "invalid_stage": False,
        "missing_reason": False,
        "public_submission_id": public_submission_id,
        "schema_warning": schema_warning,
        "send_mail": bool(mail_result and mail_result.sent),
        "skipped": False,
    }
