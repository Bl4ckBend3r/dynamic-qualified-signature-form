from __future__ import annotations

import logging
import re
import json
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

from form_loader import build_consents_view, build_submission_view
from services.document_service import DocumentType, build_document_pdf_context
from services.documents.document_storage_service import DocumentStorageService
from services.documents.pdf_render_service import PdfRenderService
from services.process_service import ProcessStatus
from services.submission_document_service import SubmissionDocumentService, SubmissionDocumentType
from services.training_agreement_service import build_training_agreement_number

logger = logging.getLogger(__name__)


def normalize_training_id(value: Any) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())
    return normalized.strip("_") or "szkolenie"


def build_training_agreement_filename(pattern: str, row: Mapping[str, Any], training: Mapping[str, Any], sequence: int) -> str:
    first_name = row.get("first_name") or row.get("imiona") or row.get("imie") or "Imie"
    last_name = row.get("last_name") or row.get("nazwisko") or "Nazwisko"
    values = {
        "first_name": normalize_training_id(first_name),
        "last_name": normalize_training_id(last_name),
        "submission_id": normalize_training_id(row.get("submission_id", "")),
        "training_id": normalize_training_id(training.get("id", "")),
        "agreement_sequence": sequence,
    }
    try:
        filename = pattern.format(**values)
    except KeyError:
        filename = f"{values['first_name']}_{values['last_name']}-{values['training_id']}-umowa.pdf"
    filename = Path(filename).name
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    return filename


def build_training_agreement_record(
    *,
    training: Mapping[str, Any],
    sequence: int,
    agreement_number: str,
    generated_date: str,
    filename: str,
) -> dict:
    return {
        "id": training.get("id", f"training_{sequence}"),
        "training_id": training.get("id", ""),
        "training_name": training.get("name", ""),
        "training_price": training.get("price", ""),
        "sequence": sequence,
        "number": agreement_number,
        "generated_at": generated_date,
        "filename": filename,
        "signed": False,
        "signature_valid": False,
        "signed_filename": "",
        "signature_type": "",
        "signature_error": "",
    }


def build_document_number(
    document: Mapping[str, Any],
    *,
    submission_id: str,
    sequence: int,
    generated_date: str,
) -> str:
    numbering = document.get("numbering") or {}
    pattern = numbering.get("number_pattern") or "{submission_id}/{agreement_sequence}/{generated_date}"
    return pattern.format(
        submission_id=submission_id,
        agreement_sequence=sequence,
        generated_date=generated_date,
    )


def build_agreement_block_updates(declaration_data: Mapping[str, Any]) -> dict[str, str]:
    blocking_fields = {
        "deklaracja_18_lat",
        "deklaracja_lubuskie",
        "deklaracja_brak_dzialalnosci",
        "deklaracja_brak_ksztalcenia",
        "deklaracja_umiejetnosci_podstawowe",
    }
    blocked = any(
        str(declaration_data.get(field_name) or "").strip().lower() == "nie"
        for field_name in blocking_fields
    )
    if not blocked:
        return {"agreement_blocked": "", "agreement_block_reason": ""}
    return {
        "agreement_blocked": "Tak",
        "agreement_block_reason": "Warunki nie zostaly spelnione na podstawie deklaracji uczestnika.",
        "process_status": ProcessStatus.AGREEMENT_BLOCKED.value,
    }


def serialize_json_list(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)


def generate_training_agreements_for_submission(
    submission: dict,
    *,
    app,
    storage,
    get_form_definition: Callable[[str], dict | None],
    get_training_agreement_config: Callable[[dict], dict],
    parse_selected_trainings: Callable[[dict], list[dict]],
    resolve_template_html: Callable[[str], str | None],
    resolve_pdf_image_url: Callable[[dict], str | None],
    generated_date: str | None = None,
    submission_repository=None,
    submission_document_service: SubmissionDocumentService | None = None,
    pdf_render_service: PdfRenderService | None = None,
    document_storage_service: DocumentStorageService | None = None,
) -> list[dict]:
    row = submission["row"]
    slug = submission["form_slug"]
    submission_id = submission["submission_id"]
    form_definition = get_form_definition(slug)
    if not form_definition:
        raise RuntimeError("Nie znaleziono definicji formularza dla umowy.")

    selected_trainings = parse_selected_trainings(row)
    if not selected_trainings:
        raise RuntimeError("Nie wybrano szkolen do wygenerowania umow.")

    agreement_config = get_training_agreement_config(form_definition)
    if not agreement_config.get("enabled"):
        return []

    resolved_date = generated_date or date.today().isoformat()
    template_html = resolve_template_html(agreement_config.get("template", ""))
    renderer = pdf_render_service or PdfRenderService()
    storage_service = document_storage_service or DocumentStorageService()
    metadata_service = submission_document_service or SubmissionDocumentService(
        submission_repository=submission_repository,
        storage=storage,
    )
    agreements = []

    for index, training in enumerate(selected_trainings, start=1):
        agreement_number = build_training_agreement_number(
            submission_id,
            index,
            resolved_date,
            agreement_config,
        )
        filename = build_training_agreement_filename(
            agreement_config.get("filename_pattern", ""),
            row,
            training,
            index,
        )
        render_row = {
            **row,
            "training": training,
            "training_id": training.get("id", ""),
            "training_name": training.get("name", ""),
            "training_price": training.get("price", ""),
            "agreement_number": agreement_number,
            "agreement_generated_at": resolved_date,
        }
        context = build_document_pdf_context(
            form_definition=form_definition,
            submission_id=submission_id,
            row=render_row,
            submission_view=build_submission_view(form_definition, row),
            consents_view=build_consents_view(form_definition, row),
            pdf_image_url=resolve_pdf_image_url(form_definition),
            document_type=DocumentType.AGREEMENT,
        )
        context.update(
            {
                "training": training,
                "agreement_number": agreement_number,
                "agreement_generated_at": resolved_date,
            }
        )
        agreement_bytes = renderer.render_document_pdf_bytes(
            app=app,
            template_name="declaration_template.html",
            template_html=template_html,
            context=context,
        )
        storage_service.save_pdf(
            storage=storage,
            slug=slug,
            filename=filename,
            document_bytes=agreement_bytes,
            document_type=None,
            signed=False,
        )
        metadata_service.record_generated_document(
            submission_id=submission_id,
            form_slug=slug,
            filename=filename,
            file_bytes=agreement_bytes,
            document_id=DocumentType.TRAINING_AGREEMENT,
            document_type=SubmissionDocumentType.TRAINING_AGREEMENT,
            agreement_number=agreement_number,
            training_key=str(training.get("id") or f"training_{index}"),
            storage=storage,
        )
        agreements.append(
            build_training_agreement_record(
                training=training,
                sequence=index,
                agreement_number=agreement_number,
                generated_date=resolved_date,
                filename=filename,
            )
        )

    updates = {
        "agreement_generated": "Tak",
        "agreement_filename": agreements[0]["filename"] if agreements else "",
        "agreement_generated_at": resolved_date,
        "training_agreements": serialize_json_list(agreements),
        "process_status": ProcessStatus.AGREEMENT_WAITING_FOR_SIGNATURE.value,
    }
    storage.update_csv_row_by_submission_id(slug, submission_id, updates)
    row.update(updates)
    logger.info("Wygenerowano %s umow szkoleniowych dla wniosku %s", len(agreements), submission_id)
    return agreements
