from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from flask import Flask

from pdf_generator import generate_pdf


FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ_-]+")


class DocumentType:
    DECLARATION = "declaration"
    AGREEMENT = "agreement"


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


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


def build_declaration_filename(row: Mapping[str, Any]) -> str:
    first_name = sanitize_filename_part(get_participant_first_name(row), "Imie")
    last_name = sanitize_filename_part(get_participant_last_name(row), "Nazwisko")

    return f"{first_name}_{last_name}-deklaracja.pdf"


def build_agreement_filename(row: Mapping[str, Any]) -> str:
    first_name = sanitize_filename_part(get_participant_first_name(row), "Imie")
    last_name = sanitize_filename_part(get_participant_last_name(row), "Nazwisko")

    return f"{first_name}_{last_name}-umowa.pdf"


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
        "submission_view": submission_view,
        "consents_view": consents_view,
        "pdf_image_url": pdf_image_url,
        "pdf_image_alt": form_definition.get("title", ""),
        "document_type": document_type,
    }


def generate_document_pdf_bytes(
    *,
    app: Flask,
    template_name: str,
    context: dict,
) -> bytes:
    with tempfile.NamedTemporaryFile(
        suffix=".pdf",
        delete=False,
        dir=app.config["TEMP_DIR"],
    ) as tmp_pdf:
        tmp_pdf_path = Path(tmp_pdf.name)

    try:
        generate_pdf(
            app=app,
            template_name=template_name,
            context=context,
            output_path=tmp_pdf_path,
        )
        return tmp_pdf_path.read_bytes()
    finally:
        tmp_pdf_path.unlink(missing_ok=True)
