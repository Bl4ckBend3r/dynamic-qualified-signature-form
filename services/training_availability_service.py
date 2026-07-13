from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from services.training_service import build_training_availability, normalize_trainings_config, parse_training_snapshots


EXCLUDED_STATUS_FRAGMENTS = ("REJECT", "CANCEL", "ANUL", "ODRZUC")


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
        catalog = normalize_trainings_config(field, active_only=True)
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
            if not occupies_training_seat(row):
                continue
            for training in parse_training_snapshots(row.get("selected_trainings")):
                training_id = str(training.get("id") or "").strip()
                if training_id:
                    counts[training_id] += 1
        return counts


def occupies_training_seat(row: Mapping[str, Any]) -> bool:
    if not parse_training_snapshots(row.get("selected_trainings")):
        return False
    status_values = [
        row.get("process_status"),
        row.get("officer_decision"),
        row.get("acceptance_required"),
        row.get("akceptacja"),
    ]
    status_text = " ".join(str(value or "").upper() for value in status_values)
    return not any(fragment in status_text for fragment in EXCLUDED_STATUS_FRAGMENTS)
