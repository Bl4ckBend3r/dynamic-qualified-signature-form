from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping


FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ_-]+")
PDF_DOCUMENT_TYPES = {"declaration", "deklaracja", "agreement", "training_agreement", "umowa", "umowy"}


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


def build_signed_filename(filename: str) -> str:
    source = Path(filename)
    return f"{source.stem or 'dokument'}-signed{source.suffix or '.pdf'}"


def get_first_existing_value(row: Mapping[str, Any], field_names: list[str]) -> str:
    for field_name in field_names:
        value = normalize_text(row.get(field_name))
        if value:
            return value
    return ""


def get_participant_first_name(row: Mapping[str, Any]) -> str:
    return get_first_existing_value(row, ["first_name", "imie", "imiona", "imię", "Imię", "Imię (imiona)"])


def get_participant_last_name(row: Mapping[str, Any]) -> str:
    return get_first_existing_value(row, ["last_name", "nazwisko", "Nazwisko"])


def build_participant_name(row: Mapping[str, Any]) -> str:
    first_name = get_participant_first_name(row)
    last_name = get_participant_last_name(row)
    full_name = " ".join(part for part in [first_name, last_name] if part)
    return full_name or "Uczestnik"


def build_filename_from_pattern(pattern: str, row: Mapping[str, Any], fallback: str) -> str:
    if not pattern:
        return fallback
    values = {
        "first_name": sanitize_filename_part(get_participant_first_name(row), "Imie"),
        "last_name": sanitize_filename_part(get_participant_last_name(row), "Nazwisko"),
        "participant_name": sanitize_filename_part(build_participant_name(row), "Uczestnik"),
        "submission_id": sanitize_filename_part(row.get("submission_id"), "wniosek"),
        "training_id": sanitize_filename_part(row.get("training_id"), "szkolenie"),
        "agreement_sequence": sanitize_filename_part(row.get("agreement_sequence"), "1"),
        "generated_date": sanitize_filename_part(row.get("generated_date") or row.get("agreement_generated_at"), "data"),
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


def build_submission_pdf_filename(slug: str, submission_id: str) -> str:
    return f"{sanitize_filename_part(slug, 'formularz')}-{sanitize_filename_part(submission_id, 'wniosek')}.pdf"


def build_signed_submission_pdf_filename(slug: str, submission_id: str) -> str:
    return build_signed_filename(build_submission_pdf_filename(slug, submission_id))


def normalize_output_dir(output_dir: str | None) -> str:
    normalized = str(output_dir or "output").replace("\\", "/").strip("/")
    if not normalized:
        return "output"
    if ".." in Path(normalized).parts:
        raise ValueError("Niepoprawna ścieżka katalogu wyjściowego.")
    return normalized


def document_type_directory(document_type: str | None, signed: bool | None = None) -> str:
    signature_dir = "podpisane" if signed else "niepodpisane"
    if document_type in {"declaration", "deklaracja"}:
        return f"deklaracja/{signature_dir}"
    if document_type in {"agreement", "training_agreement", "umowa", "umowy"}:
        return f"umowy/{signature_dir}"
    return ""


def resolve_pdf_storage_path(
    *,
    output_dir: str = "output",
    slug: str,
    filename: str,
    document_type: str | None = None,
    signed: bool | None = None,
) -> str:
    raw_filename = str(filename or "")
    if "/" in raw_filename or "\\" in raw_filename:
        raise ValueError("Niepoprawna nazwa pliku PDF.")
    clean_filename = Path(filename).name
    if clean_filename in {"", ".", ".."}:
        raise ValueError("Niepoprawna nazwa pliku PDF.")
    clean_slug = sanitize_filename_part(slug, "formularz")
    normalized_output_dir = normalize_output_dir(output_dir)
    type_dir = document_type_directory(document_type, signed)
    if type_dir:
        return f"{normalized_output_dir}/{clean_slug}/pdf/{type_dir}/{clean_filename}"
    return f"{normalized_output_dir}/{clean_slug}/pdf/{clean_filename}"
