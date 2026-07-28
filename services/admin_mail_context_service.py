from __future__ import annotations

from typing import Any
from datetime import datetime

from services.mail_template_service import build_mail_context as build_platform_mail_context
from services.mail_template_service import render_template_text
from services.mail_training_context_service import build_training_mail_context


def build_mail_context(
    form,
    submission=None,
    files: list | None = None,
    *,
    documents_to_sign_url_builder=None,
    document_url_builder=None,
    training_availability_service=None,
    **context_extra,
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
    context.update(context_extra)
    context.update(
        build_training_mail_context(
            form,
            submission,
            availability_service=training_availability_service,
        )
    )
    return context


def render_mail_text(raw_text: str, context: dict) -> str:
    return render_template_text(raw_text or "", context)


def preview_mail_context(
    form,
    submission=None,
    base_context: dict | None = None,
    *,
    training_availability_service=None,
) -> dict[str, Any]:
    context = dict(
        base_context
        or build_mail_context(
            form,
            submission,
            [],
            training_availability_service=training_availability_service,
        )
    )
    preview_training_context = build_training_mail_context(
        form,
        submission,
        availability_service=training_availability_service,
        use_available_as_selected_fallback=submission is None,
    )
    if base_context:
        context.update(
            {
                key: value
                for key, value in preview_training_context.items()
                if key.startswith("selected_trainings") or key == "trainings_total_price"
            }
        )
    else:
        context.update(preview_training_context)
    now = datetime.now()
    context.setdefault("app_name", "Portal formularzy")
    context.setdefault("current_year", now.year)
    context.setdefault("base_url", "https://formularze.example.com")
    context.setdefault("contact_email", "kontakt@example.com")
    context.setdefault("imiona", "Jan")
    context.setdefault("imie", context.get("imiona", "Jan"))
    context.setdefault("nazwisko", "Kowalski")
    context.setdefault("email", "jan.kowalski@example.com")
    context.setdefault("submission_id", "6ef64b28-530b-4f8e-9325-26ae83e7c11e")
    context.setdefault("public_submission_id", context["submission_id"])
    context["form_name"] = form.name
    context["form_title"] = getattr(form, "title", "") or form.name
    context["form_slug"] = form.slug
    context.setdefault("form_description", getattr(form, "description", "") or "Przykładowy opis formularza")
    context.setdefault("created_at", now.strftime("%d.%m.%Y %H:%M"))
    context.setdefault("updated_at", now.strftime("%d.%m.%Y %H:%M"))
    context.setdefault("process_status", "FORM_SUBMITTED")
    context.setdefault("status_label", "Wniosek zaakceptowany")
    context.setdefault("process_status_label", context["status_label"])
    context.setdefault("officer_decision", "accepted")
    context.setdefault("officer_decision_reason", "Wniosek spełnia wymagania.")
    context.setdefault("correction_message", "Uzupełnij brakujące dane.")
    context.setdefault("user_instruction", getattr(form, "user_instruction", "") or "Postępuj zgodnie z instrukcją w wiadomości.")
    context.setdefault("acceptance_required", "Tak")
    context.setdefault("declaration_required", "Tak")
    context.setdefault("agreement_required", "Nie")
    context.setdefault("pdf_filename", "zgloszenie.pdf")
    context.setdefault("declaration_filename", "deklaracja.pdf")
    context.setdefault("agreement_filename", "umowa.pdf")
    context.setdefault("signed_pdf_filename", "zgloszenie-podpisane.pdf")
    context.setdefault("declaration_signed_filename", "deklaracja-podpisana.pdf")
    context.setdefault("agreement_signed_filename", "umowa-podpisana.pdf")
    context.setdefault("selected_trainings", "Excel, Zarządzanie projektem")
    context.setdefault("selected_trainings_count", 2)
    context.setdefault("trainings_total_price", "1 200,00 zł")
    context.setdefault("all_selected_trainings_total_formatted", "1 200,00 zł")
    context.setdefault("status_url", "https://formularze.example.com/status/przyklad")
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
