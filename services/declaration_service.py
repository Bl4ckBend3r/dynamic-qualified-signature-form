from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from form_loader import build_consents_view, build_submission_view
from services.document_service import (
    DocumentType,
    build_declaration_filename,
    build_document_pdf_context,
    get_document_config,
    is_document_enabled,
)
from services.documents.document_storage_service import DocumentStorageService
from services.documents.pdf_render_service import PdfRenderService
from services.process_service import ProcessStatus
from services.submission_document_service import SubmissionDocumentService, SubmissionDocumentType

logger = logging.getLogger(__name__)


def build_declaration_form_definition(declaration_config: Mapping[str, Any]) -> dict:
    return {
        "title": declaration_config.get("form_title") or "Uzupelnienie deklaracji uczestnictwa",
        "description": declaration_config.get("form_description") or "",
        "submit_label": declaration_config.get("form_submit_label") or "Wygeneruj deklaracje PDF",
        "fields": declaration_config.get("fields") or [],
    }


def build_declaration_not_required_updates(*, agreement_enabled: bool) -> dict[str, str]:
    return {
        "declaration_required": "Nie",
        "declaration_generated": "Nie",
        "process_status": (
            ProcessStatus.AGREEMENT_READY.value
            if agreement_enabled
            else ProcessStatus.PARTICIPANT_ACCEPTED.value
        ),
    }


def build_declaration_generated_updates(filename: str) -> dict[str, str]:
    return {
        "declaration_required": "Tak",
        "declaration_generated": "Tak",
        "declaration_filename": filename,
        "process_status": ProcessStatus.DECLARATION_WAITING_FOR_SIGNATURE.value,
    }


def ensure_declaration_generated(
    submission: dict,
    *,
    app,
    storage,
    get_form_definition: Callable[[str], dict | None],
    resolve_template_html: Callable[[str], str | None],
    resolve_pdf_image_url: Callable[[dict], str | None],
    force: bool = False,
    submission_repository=None,
    submission_document_service: SubmissionDocumentService | None = None,
    pdf_render_service: PdfRenderService | None = None,
    document_storage_service: DocumentStorageService | None = None,
    log: logging.Logger | None = None,
) -> dict:
    row = submission["row"]
    slug = submission["form_slug"]
    submission_id = submission["submission_id"]
    existing_filename = str(row.get("declaration_filename") or "").strip()
    active_logger = log or logger

    form_definition = get_form_definition(slug)
    if not form_definition:
        raise RuntimeError("Nie znaleziono definicji formularza dla deklaracji.")

    declaration_config = get_document_config(form_definition, DocumentType.DECLARATION)
    agreement_enabled = is_document_enabled(form_definition, DocumentType.AGREEMENT)

    if not declaration_config.get("enabled"):
        updates = build_declaration_not_required_updates(agreement_enabled=agreement_enabled)
        storage.update_csv_row_by_submission_id(slug, submission_id, updates)
        row.update(updates)
        return {"enabled": False, "filename": "", "created": False}

    if not force and str(row.get("declaration_generated") or "").strip().lower() == "tak" and existing_filename:
        try:
            storage.get_pdf_bytes(slug, existing_filename)
        except Exception:
            active_logger.warning(
                "Deklaracja %s dla wniosku %s jest w CSV, ale nie ma jej w storage. Regeneruje PDF.",
                existing_filename,
                submission_id,
            )
        else:
            return {"enabled": True, "filename": existing_filename, "created": False}

    declaration_filename = (
        existing_filename
        if str(row.get("declaration_generated") or "").strip().lower() == "tak" and existing_filename
        else build_declaration_filename(row, declaration_config)
    )

    declaration_context = build_document_pdf_context(
        form_definition=form_definition,
        submission_id=submission_id,
        row=row,
        submission_view=build_submission_view(form_definition, row),
        consents_view=build_consents_view(form_definition, row),
        pdf_image_url=resolve_pdf_image_url(form_definition),
        document_type=DocumentType.DECLARATION,
    )
    template_html = resolve_template_html(declaration_config.get("template", ""))
    renderer = pdf_render_service or PdfRenderService()
    document_bytes = renderer.render_document_pdf_bytes(
        app=app,
        template_name="declaration_template.html",
        template_html=template_html,
        context=declaration_context,
    )

    storage_service = document_storage_service or DocumentStorageService()
    storage_service.save_pdf(
        storage=storage,
        slug=slug,
        filename=declaration_filename,
        document_bytes=document_bytes,
        document_type=None,
        signed=False,
    )
    metadata_service = submission_document_service or SubmissionDocumentService(
        submission_repository=submission_repository,
        storage=storage,
    )
    metadata_service.record_generated_document(
        submission_id=submission_id,
        form_slug=slug,
        filename=declaration_filename,
        file_bytes=document_bytes,
        document_id=DocumentType.DECLARATION,
        document_type=SubmissionDocumentType.DECLARATION,
        storage=storage,
    )

    updates = build_declaration_generated_updates(declaration_filename)
    storage.update_csv_row_by_submission_id(slug, submission_id, updates)
    row.update(updates)

    active_logger.info("Wygenerowano deklaracje %s dla wniosku %s", declaration_filename, submission_id)
    return {"enabled": True, "filename": declaration_filename, "created": True}
