from __future__ import annotations

from typing import Any
from datetime import datetime

from services.mail_template_service import build_mail_context as build_platform_mail_context
from services.mail_template_service import render_template_text
from services.mail_training_context_service import build_training_mail_context


def resolve_mail_links(form, submission, *, definition=None, can_access_submission=True,
                       documents_to_sign_url_builder=None, document_url_builder=None):
    """Resolve the existing mail URL aliases before rendering, for this recipient."""
    from flask import current_app, has_app_context, has_request_context, url_for
    from werkzeug.routing import BuildError
    from services.documents.document_workflow_service import is_document_step
    links = {key: '' for key in ('status_url', 'public_status_url', 'participant_action_url',
                                 'podpisz_url', 'pobierz_url', 'document_url', 'correction_url')}
    if not submission:
        return links
    if has_request_context():
        try:
            links['public_status_url'] = url_for('public_forms.public_status_page',
                submission_id=submission.submission_id, _external=True)
        except BuildError:
            # Standalone service consumers can provide their own URL builders
            # without registering the application's public blueprints.
            pass
    links['status_url'] = links['public_status_url']
    if not can_access_submission or not getattr(submission, 'access_token', None):
        return links
    row = {key: getattr(submission, key, None) for key in
           ('submission_id', 'form_slug', 'access_token', 'workflow_stage', 'workflow_step', 'document_states')}
    if documents_to_sign_url_builder:
        action_url = documents_to_sign_url_builder(submission)
    elif has_request_context():
        try:
            action_url = url_for('documents.documents_to_sign', submission_id=submission.submission_id,
                                 token=submission.access_token, _external=True)
        except BuildError:
            action_url = ''
    else:
        action_url = ''
    links.update(participant_action_url=action_url, podpisz_url=action_url)
    definition = definition if definition is not None else (
        getattr(getattr(submission, 'form_version', None), 'definition_json', None)
        or getattr(form, 'definition_json', {}) or {})
    step = next((s for s in definition.get('workflow', {}).get('steps', [])
                 if s.get('id') == (row['workflow_stage'] or row['workflow_step'])), {})
    services = current_app.extensions.get('services') if has_app_context() else None
    documents = getattr(services, 'document_service', None)
    if is_document_step(step):
        links['podpisz_url'] = ''
        view = documents.document_workflow.view(row, definition) if documents else None
        if view:
            if any(f['can_upload'] for f in view['files']) and view['substate'] not in {
                'verifying', 'awaiting_office_signature', 'participant_signed', 'office_signed', 'signed', 'completed'}:
                links['podpisz_url'] = action_url
            files = [f for f in view['files'] if f.get('filename')]
            if files and view['substate'] not in {'generating', 'failed'} and has_request_context():
                links['document_url'] = (url_for('documents.download_document_step', slug=submission.form_slug,
                    submission_id=submission.submission_id, step_id=step['id'], filename=files[0]['filename'],
                    token=submission.access_token, _external=True) if len(files) == 1 else action_url)
    elif getattr(submission, 'pdf_filename', None):
        if document_url_builder:
            links['document_url'] = document_url_builder(submission, submission.pdf_filename)
        elif documents and has_request_context():
            links['document_url'] = documents.build_download_url(row, submission.pdf_filename)
    links['pobierz_url'] = links['document_url']
    return links

def _get_primary_agreement_number(submission) -> str:
    agreements = getattr(submission, "training_agreements", None) or []

    if isinstance(agreements, dict):
        agreements = agreements.get("agreements", [])

    if not isinstance(agreements, list):
        return ""

    for agreement in agreements:
        if not isinstance(agreement, dict):
            continue

        number = agreement.get("agreement_number") or agreement.get("number") or ""

        if number:
            return str(number)

    return ""

def build_mail_context(
    form,
    submission=None,
    files: list | None = None,
    *,
    documents_to_sign_url_builder=None,
    document_url_builder=None,
    training_availability_service=None,
    participant=None,
    can_access_submission=True,
    definition=None,
    **context_extra,
) -> dict[str, Any]:
    context = build_platform_mail_context(form, submission, files or [], participant=participant)
    context.update(resolve_mail_links(form, submission, definition=definition,
        can_access_submission=can_access_submission, documents_to_sign_url_builder=documents_to_sign_url_builder,
        document_url_builder=document_url_builder))
    if submission and participant is None:
        signed_files = [
            item for item in (files or [])
            if isinstance(item, dict)
            and item.get("signed")
            and str(item.get("document_type") or "") in {
                "signed_agreement",
                "signed_training_agreement",
                "agreement_signed_by_office",
            }
        ]
        signed_rows = []
        signed_text = []
        for item in signed_files:
            filename = str(item.get("filename") or "").strip()
            if not filename:
                continue
            link = ""
            if document_url_builder and can_access_submission and submission.access_token:
                try:
                    link = str(document_url_builder(submission, filename) or "")
                except Exception:
                    link = ""
            signed_rows.append(f'<li><a href="{link}">{filename}</a></li>' if link else f"<li>{filename}</li>")
            signed_text.append(f"- {filename}" + (f": {link}" if link else ""))
        context["signed_agreements_list"] = "<ul>" + "".join(signed_rows) + "</ul>" if signed_rows else ""
        context["signed_agreements_table"] = context["signed_agreements_list"]
        context["signed_agreements_text"] = "\n".join(signed_text)
        context.setdefault("signed_agreement_filename", str(getattr(submission, "agreement_signed_filename", "") or ""))
        context.setdefault("agreement_number", _get_primary_agreement_number(submission))
        context.setdefault("signed_agreement_download_link", "")
        context.setdefault("agreement_signed_by_office_at", "")
    context.update(context_extra)
    if participant is None:
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
    context.setdefault("participant", {"record_uuid": "6ef64b28-530b-4df0-b421-53e6c8dd2735", "first_name": "Jan", "last_name": "Kowalski", "full_name": "Jan Kowalski", "email": "jan@example.org"})
    context.setdefault("submitted_at", "02.09.2026")
    context.setdefault("current_status", "Wniosek oczekuje na weryfikację")
    context.setdefault("public_status_url", "https://formularze.example.com/sprawdz-status")
    context.setdefault("participant_action_url", "")
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
