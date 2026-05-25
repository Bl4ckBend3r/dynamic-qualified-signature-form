from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Mapping
from datetime import datetime
from flask import Flask

from pdf_generator import generate_pdf, generate_pdf_from_html


FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ_-]+")
PDF_LOGO_FOOTER_PATTERN = re.compile(
    r"<footer\b[^>]*class=[\"'][^\"']*pdf-logo-footer[^\"']*[\"'][^>]*>.*?</footer>",
    re.IGNORECASE | re.DOTALL,
)
PDF_LOGO_HEADER_PATTERN = re.compile(
    r"<header\b[^>]*class=[\"'][^\"']*pdf-logo-header[^\"']*[\"'][^>]*>.*?</header>",
    re.IGNORECASE | re.DOTALL,
)
DOCUMENT_LOGO_HEADER_PATTERN = re.compile(
    r"<header\b[^>]*class=[\"'][^\"']*document-logo-header[^\"']*[\"'][^>]*>.*?</header>",
    re.IGNORECASE | re.DOTALL,
)
FORM_HEADER_IMAGE_PATTERN = re.compile(
    r"<div\b[^>]*class=[\"'][^\"']*form-header-image[^\"']*[\"'][^>]*>.*?</div>",
    re.IGNORECASE | re.DOTALL,
)
BODY_OPEN_PATTERN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


class DocumentType:
    DECLARATION = "declaration"
    AGREEMENT = "agreement"


DEFAULT_DOCUMENT_CONFIG = {
    "enabled": False,
    "template": "",
    "filename_pattern": "",
    "signature_required": True,
}


class DocumentService:
    def get_documents_config(self, form_config: dict) -> list[dict]:
        from services.form_config_service import FormConfigService

        return FormConfigService().normalize_documents_config(form_config)

    def generate_document(
        self,
        submission: dict,
        form_config: dict,
        document_id: str,
    ) -> dict:
        document = self.get_document_by_id(form_config, document_id)
        if not document:
            raise ValueError(f"Unknown document_id: {document_id}")
        return {
            "document_id": document_id,
            "kind": document.get("kind"),
            "filename": self.build_filename(document.get("filename_pattern", ""), submission),
            "generated": False,
            "document": document,
        }

    def build_filename(self, pattern: str, submission: dict) -> str:
        fallback = f"{sanitize_filename_part(submission.get('submission_id'), 'dokument')}.pdf"
        return build_filename_from_pattern(pattern, submission, fallback)

    def get_document_by_id(self, form_config: dict, document_id: str) -> dict | None:
        for document in self.get_documents_config(form_config):
            if document.get("id") == document_id:
                return document
        return None


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def is_enabled(value: Any) -> bool:
    normalized = normalize_text(value).lower()
    return normalized in {"true", "1", "yes", "tak"}


def sanitize_filename_part(value: Any, fallback: str = "dokument") -> str:
    text = normalize_text(value)

    if not text:
        text = fallback

    text = text.replace(" ", "_")
    text = FILENAME_SAFE_PATTERN.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("_")

    return text or fallback


def get_first_existing_value(row: Mapping[str, Any], field_names: list[str]) -> str:
    for field_name in field_names:
        value = normalize_text(row.get(field_name))

        if value:
            return value

    return ""


def get_participant_first_name(row: Mapping[str, Any]) -> str:
    return get_first_existing_value(
        row,
        [
            "first_name",
            "imie",
            "imiona",
            "imię",
            "Imię",
            "Imię (imiona)",
        ],
    )


def get_participant_last_name(row: Mapping[str, Any]) -> str:
    return get_first_existing_value(
        row,
        [
            "last_name",
            "nazwisko",
            "Nazwisko",
        ],
    )


def build_participant_name(row: Mapping[str, Any]) -> str:
    first_name = get_participant_first_name(row)
    last_name = get_participant_last_name(row)
    full_name = " ".join(part for part in [first_name, last_name] if part)

    return full_name or "Uczestnik"


def get_project_documents_config(form_definition: Mapping[str, Any]) -> dict:
    process = form_definition.get("process") or {}

    if not isinstance(process, Mapping):
        process = {}

    documents = process.get("documents") or form_definition.get("documents") or {}

    if not isinstance(documents, Mapping):
        documents = {}

    return dict(documents)


def get_document_config(form_definition: Mapping[str, Any], document_type: str) -> dict:
    documents = get_project_documents_config(form_definition)
    raw_config = documents.get(document_type) or {}

    if not isinstance(raw_config, Mapping):
        raw_config = {}

    config = {**DEFAULT_DOCUMENT_CONFIG, **dict(raw_config)}
    config["enabled"] = bool(config.get("enabled"))
    config["signature_required"] = bool(config.get("signature_required", True))

    return config


def is_document_enabled(form_definition: Mapping[str, Any], document_type: str) -> bool:
    return bool(get_document_config(form_definition, document_type).get("enabled"))


def build_filename_from_pattern(pattern: str, row: Mapping[str, Any], fallback: str) -> str:
    if not pattern:
        return fallback

    values = {
        "first_name": sanitize_filename_part(get_participant_first_name(row), "Imie"),
        "last_name": sanitize_filename_part(get_participant_last_name(row), "Nazwisko"),
        "participant_name": sanitize_filename_part(build_participant_name(row), "Uczestnik"),
        "submission_id": sanitize_filename_part(row.get("submission_id"), "wniosek"),
    }

    try:
        filename = pattern.format(**values)
    except KeyError:
        return fallback

    filename = sanitize_filename_part(filename.replace(".pdf", ""), Path(fallback).stem)

    return f"{filename}.pdf"


def build_declaration_filename(row: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> str:
    first_name = sanitize_filename_part(get_participant_first_name(row), "Imie")
    last_name = sanitize_filename_part(get_participant_last_name(row), "Nazwisko")
    fallback = f"{first_name}_{last_name}-deklaracja.pdf"

    return build_filename_from_pattern(normalize_text((config or {}).get("filename_pattern")), row, fallback)


def build_agreement_filename(row: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> str:
    first_name = sanitize_filename_part(get_participant_first_name(row), "Imie")
    last_name = sanitize_filename_part(get_participant_last_name(row), "Nazwisko")
    fallback = f"{first_name}_{last_name}-umowa.pdf"

    return build_filename_from_pattern(normalize_text((config or {}).get("filename_pattern")), row, fallback)


def build_document_pdf_context(
    *,
    form_definition: dict,
    submission_id: str,
    row: Mapping[str, Any],
    submission_view: list[dict],
    consents_view: list[dict],
    pdf_image_url: str | None,
    document_type: str,
) -> dict:
    return {
        "form_definition": form_definition,
        "submission_id": submission_id,
        "participant_name": build_participant_name(row),
        "submission": row,
        "submission_view": submission_view,
        "consents_view": consents_view,
        "pdf_image_url": pdf_image_url,
        "pdf_image_alt": form_definition.get("title", ""),
        "document_type": document_type,
        "generated_at": datetime.now().strftime("%d.%m.%Y"),
    }


def remove_inline_logo_markup(template_html: str) -> str:
    """Remove logo blocks from the document body.

    Repeated logos are now rendered only by Playwright's PDF footer_template.
    Keeping HTML logo blocks causes an additional logo on the first page header.
    """

    cleaned = PDF_LOGO_FOOTER_PATTERN.sub("", template_html)
    cleaned = PDF_LOGO_HEADER_PATTERN.sub("", cleaned)
    cleaned = DOCUMENT_LOGO_HEADER_PATTERN.sub("", cleaned)
    cleaned = FORM_HEADER_IMAGE_PATTERN.sub("", cleaned)

    return cleaned


def prepare_document_template_html(template_html: str, context: Mapping[str, Any]) -> str:
    if normalize_text(context.get("pdf_image_url")):
        return remove_inline_logo_markup(template_html)

    return template_html


def generate_document_pdf_bytes(
    *,
    app: Flask,
    template_name: str,
    context: dict,
    template_html: str | None = None,
) -> bytes:
    with tempfile.NamedTemporaryFile(
        suffix=".pdf",
        delete=False,
        dir=app.config["TEMP_DIR"],
    ) as tmp_pdf:
        tmp_pdf_path = Path(tmp_pdf.name)

    try:
        if template_html:
            template_html = prepare_document_template_html(template_html, context)
            generate_pdf_from_html(
                app=app,
                template_html=template_html,
                context=context,
                output_path=tmp_pdf_path,
            )
        else:
            generate_pdf(
                app=app,
                template_name=template_name,
                context=context,
                output_path=tmp_pdf_path,
            )

        return tmp_pdf_path.read_bytes()
    finally:
        tmp_pdf_path.unlink(missing_ok=True)
