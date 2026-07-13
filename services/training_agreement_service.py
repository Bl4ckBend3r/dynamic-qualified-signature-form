from __future__ import annotations

from typing import Mapping

from services.document_service import DocumentType, get_document_config
from services.training_service import normalize_training_id, selected_training_snapshots


def get_training_selection_field(form_definition: Mapping[str, object]) -> dict | None:
    if form_definition.get("id") == DocumentType.DECLARATION:
        declaration_config = dict(form_definition)
    else:
        declaration_config = get_document_config(form_definition, DocumentType.DECLARATION)
    for field in declaration_config.get("fields") or []:
        if isinstance(field, Mapping) and field.get("type") == "training_selection":
            return dict(field)
    return None


def extract_training_selection(
    field: Mapping[str, object],
    request_form,
    availability: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[list[dict], str | None]:
    return selected_training_snapshots(field, request_form, availability=availability)


def build_training_agreement_number(
    submission_id: str,
    sequence: int,
    generated_date: str,
    config: Mapping[str, object],
) -> str:
    numbering = config.get("numbering") or {}
    pattern = numbering.get("number_pattern") or "{submission_id}/{agreement_sequence}/{generated_date}"
    return pattern.format(
        submission_id=submission_id,
        agreement_sequence=sequence,
        generated_date=generated_date,
    )
