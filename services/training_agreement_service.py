from __future__ import annotations

from typing import Any, Mapping

from services.document_service import DocumentType, get_document_config


def normalize_training_id(value: Any) -> str:
    return str(value or "").strip()


def get_training_selection_field(form_definition: Mapping[str, Any]) -> dict | None:
    declaration_config = get_document_config(form_definition, DocumentType.DECLARATION)
    for field in declaration_config.get("fields") or []:
        if isinstance(field, Mapping) and field.get("type") == "training_selection":
            return dict(field)
    return None


def extract_training_selection(field: Mapping[str, Any], request_form) -> tuple[list[dict], str | None]:
    selected_ids = {normalize_training_id(value) for value in request_form.getlist(field.get("name", ""))}
    catalog = field.get("catalog") or []
    trainings = []

    for item in catalog:
        training_id = normalize_training_id(item.get("id") or item.get("name"))
        if training_id not in selected_ids:
            continue
        price = float(item.get("price") or 0)
        trainings.append(
            {
                "id": training_id,
                "name": item.get("name") or training_id,
                "price": price,
            }
        )

    if field.get("required") and not trainings:
        return [], "Wybierz co najmniej jedno szkolenie."

    max_total = field.get("max_total_amount")
    total = sum(float(item.get("price") or 0) for item in trainings)
    if max_total is not None and total > float(max_total):
        return trainings, f"Łączna wartość szkoleń przekracza limit {max_total} {field.get('currency', 'PLN')}."

    return trainings, None


def build_training_agreement_number(
    submission_id: str,
    sequence: int,
    generated_date: str,
    config: Mapping[str, Any],
) -> str:
    numbering = config.get("numbering") or {}
    pattern = numbering.get("number_pattern") or "{submission_id}/{agreement_sequence}/{generated_date}"
    return pattern.format(
        submission_id=submission_id,
        agreement_sequence=sequence,
        generated_date=generated_date,
    )
