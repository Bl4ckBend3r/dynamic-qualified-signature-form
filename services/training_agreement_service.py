from __future__ import annotations

from typing import Mapping

from services.training_catalog_service import TrainingCatalogService
from services.training_service import normalize_training_id


def get_training_selection_field(form_definition: Mapping[str, object]) -> dict | None:
    return TrainingCatalogService.get_training_field(form_definition)


def extract_training_selection(
    field: Mapping[str, object],
    request_form,
    availability: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[list[dict], str | None]:
    selected_ids = {
        normalize_training_id(value)
        for value in request_form.getlist(str(field.get("name") or ""))
    }
    return TrainingCatalogService().select_trainings(
        field,
        selected_ids,
        availability,
    )


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
