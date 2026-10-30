from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from services.training_catalog_service import TrainingCatalogService
from services.training_service import build_training_availability
from services.submission_training_service import LOCKING_STATUSES


class TrainingAvailabilityService:
    def __init__(self, submission_repository) -> None:
        self.submission_repository = submission_repository

    def availability_for_field(
        self,
        *,
        form_slug: str,
        field: Mapping[str, Any],
        current_submission_id: str | None = None,
    ) -> dict[str, dict]:
        catalog = TrainingCatalogService.get_trainings_for_field(
            field,
            active_only=True,
        )
        counts = self.occupied_counts(form_slug=form_slug, current_submission_id=current_submission_id)
        return build_training_availability(catalog, counts)

    def occupied_counts(self, *, form_slug: str, current_submission_id: str | None = None) -> Counter:
        counts: Counter = Counter()
        if not self.submission_repository or not hasattr(self.submission_repository, "list_by_form"):
            return counts
        current = str(current_submission_id or "").strip()
        for row in self.submission_repository.list_by_form(form_slug):
            if current and str(row.get("submission_id") or "").strip() == current:
                continue
            normalized = row.get("_submission_trainings")
            if isinstance(normalized, list):
                occupied = [
                    item for item in normalized
                    if isinstance(item, Mapping)
                    and (bool(item.get("is_locked")) or str(item.get("status") or "") in LOCKING_STATUSES)
                ]
            else:
                occupied = _legacy_locked_trainings(row)
            for training in occupied:
                nested = training.get("training") if isinstance(training.get("training"), Mapping) else {}
                training_id = str(training.get("training_id") or training.get("id") or nested.get("id") or "").strip()
                if training_id:
                    counts[training_id] += 1
        return counts


def occupies_training_seat(row: Mapping[str, Any]) -> bool:
    normalized = row.get("_submission_trainings")
    if isinstance(normalized, list):
        return any(
            isinstance(item, Mapping)
            and (bool(item.get("is_locked")) or str(item.get("status") or "") in LOCKING_STATUSES)
            for item in normalized
        )
    return bool(_legacy_locked_trainings(row))


def _legacy_locked_trainings(row: Mapping[str, Any]) -> list[dict]:
    raw = row.get("training_agreements")
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    if not isinstance(raw, list):
        return []
    return [
        item for item in raw
        if isinstance(item, dict)
        and bool(item.get("signature_valid") or item.get("signed"))
        and str(item.get("status") or "") not in {"cancelled", "unselected"}
    ]
