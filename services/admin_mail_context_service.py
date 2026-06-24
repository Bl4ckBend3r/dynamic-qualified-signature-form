from __future__ import annotations

from typing import Any

from services.mail_template_service import build_mail_context as build_platform_mail_context
from services.mail_template_service import render_template_text


def build_mail_context(
    form,
    submission=None,
    files: list | None = None,
    *,
    documents_to_sign_url_builder=None,
    document_url_builder=None,
) -> dict[str, Any]:
    context = build_platform_mail_context(form, submission, files or [])
    if submission:
        if documents_to_sign_url_builder:
            context["podpisz_url"] = documents_to_sign_url_builder(submission)
        if submission.pdf_filename and document_url_builder:
            try:
                document_url = document_url_builder(submission, submission.pdf_filename)
            except Exception:
                document_url = ""
            context["document_url"] = document_url
            context["pobierz_url"] = document_url
    return context


def render_mail_text(raw_text: str, context: dict) -> str:
    return render_template_text(raw_text or "", context)


def preview_mail_context(form, submission=None, base_context: dict | None = None) -> dict[str, Any]:
    context = dict(base_context or build_mail_context(form, submission, []))
    context.setdefault("imiona", "Jan")
    context.setdefault("nazwisko", "Kowalski")
    context.setdefault("email", "jan.kowalski@example.com")
    context.setdefault("submission_id", "6ef64b28-530b-4f8e-9325-26ae83e7c11e")
    context.setdefault("form_name", form.name)
    context.setdefault("form_slug", form.slug)
    context.setdefault("process_status", "FORM_SUBMITTED")
    context.setdefault("status_label", "Wniosek zaakceptowany")
    context.setdefault("officer_decision", "accepted")
    context.setdefault("declaration_filename", "deklaracja.pdf")
    context.setdefault("agreement_filename", "umowa.pdf")
    context.setdefault("podpisz_url", "")
    context.setdefault("pobierz_url", "")
    context.setdefault("document_url", "")
    return context


def mail_template_type_score(template, submission) -> int:
    template_type = (template.template_type or "").strip()
    if template_type == "accepted" and submission.officer_decision == "accepted":
        return 3
    if template_type == "rejected" and submission.officer_decision == "rejected":
        return 3
    if template_type == "correction_required" and submission.process_status == "CORRECTION_REQUIRED":
        return 3
    if template_type == "confirmation" and submission.process_status == "FORM_SUBMITTED":
        return 3
    if template_type == "declaration_signed" and str(submission.declaration_signed).lower() == "tak":
        return 3
    if template_type == "agreement_ready" and submission.process_status == "AGREEMENT_READY":
        return 3
    if template_type == "agreement_signed_by_user" and str(submission.agreement_signed).lower() == "tak":
        return 3
    office_signed = (
        getattr(submission, "office_agreement_signed", "")
        or getattr(submission, "office_agreement_signed_email_sent", "")
        or getattr(submission, "office_agreement_signed_email_sent_for", "")
    )
    if template_type == "agreement_signed_by_office" and str(office_signed).lower() == "tak":
        return 3
    return 0
