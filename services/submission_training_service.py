from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from models import SubmissionTraining
from services.process_service import ProcessStatus
from services.training_catalog_service import TrainingCatalogService
from services.training_service import (
    format_price_pln,
    parse_decimal_price,
    parse_training_snapshots,
)


ACTIVE_STATUSES = {"selected", "agreement_generated", "agreement_uploaded_by_beneficiary", "agreement_signed_by_office", "locked"}
PARTICIPANT_STATUS_LABELS = {
    "selected": "Wybrane",
    "agreement_generated": "Umowa wygenerowana",
    "agreement_uploaded_by_beneficiary": "Podpisana umowa wgrana",
    "agreement_signed_by_office": "Podpisane przez urząd",
    "locked": "Zablokowane",
    "cancelled": "Dostępne",
}


class TrainingSelectionError(ValueError):
    pass


class SubmissionTrainingService:
    @staticmethod
    def open_selection_stage(db, form, submission) -> bool:
        field = TrainingCatalogService.get_training_field(form)
        if not field or not field.get("enabled", True):
            return False
        submission.process_status = ProcessStatus.TRAINING_SELECTION_OPEN.value
        submission.workflow_step = ProcessStatus.TRAINING_SELECTION_OPEN.value
        db.flush()
        return True

    @staticmethod
    def can_select(form, submission) -> bool:
        decision = str(
            getattr(submission, "officer_decision", "")
            or getattr(submission, "acceptance_required", "")
            or ""
        ).strip().lower()
        return (
            decision in {"tak", "accepted"}
            and
            str(getattr(submission, "declaration_generated", "") or "").lower() == "tak"
            and str(getattr(submission, "declaration_signed", "") or "").lower() == "tak"
            and
            str(getattr(submission, "declaration_signature_valid", "") or "").lower() == "tak"
            and bool(getattr(form, "training_selection_open", True))
        )

    def synchronize_legacy(self, db, submission) -> None:
        if db.query(SubmissionTraining.id).filter(SubmissionTraining.submission_id == submission.id).first():
            return
        rows = {}
        for snapshot in parse_training_snapshots(submission.selected_trainings):
            row = self._new_row(submission.id, snapshot)
            rows[row.training_id] = row
            db.add(row)
        try:
            agreements = json.loads(str(submission.training_agreements or "[]"))
        except (TypeError, json.JSONDecodeError):
            agreements = []
        now = datetime.now(timezone.utc)
        for agreement in agreements:
            if not isinstance(agreement, Mapping) or not agreement.get("signature_valid"):
                continue
            training_id = str(
                agreement.get("training_id") or agreement.get("id") or ""
            ).strip()
            row = rows.get(training_id)
            if row is None:
                continue
            row.status = "agreement_uploaded_by_beneficiary"
            row.is_locked = True
            row.locked_at = now
            row.locked_by_event = "legacy_signed_agreement"
            row.agreement_id = str(agreement.get("id") or training_id)
        db.flush()

    def summary(self, db, submission, field: Mapping[str, Any]) -> dict[str, Any]:
        self.synchronize_legacy(db, submission)
        rows = db.query(SubmissionTraining).filter(SubmissionTraining.submission_id == submission.id).order_by(SubmissionTraining.id).all()
        catalog = {
            item["id"]: item
            for item in TrainingCatalogService.get_trainings_for_field(
                field,
                active_only=False,
            )
        }
        items, used = [], Decimal("0.00")
        for row in rows:
            if row.status not in ACTIVE_STATUSES and not row.is_locked:
                continue
            price = parse_decimal_price(row.training_price_snapshot) or Decimal("0.00")
            used += price
            source = catalog.get(row.training_id, {})
            items.append({
                **source, "id": row.training_id, "name": row.training_name_snapshot or source.get("name"),
                "price": row.training_price_snapshot, "price_formatted": format_price_pln(price, field.get("currency")),
                "status": row.status, "status_label": self.status_label(row.status, row.is_locked),
                "is_locked": row.is_locked,
            })
        maximum = TrainingCatalogService.financial_limit(field)
        locked_total = sum(
            (
                parse_decimal_price(row.training_price_snapshot) or Decimal("0.00")
                for row in rows
                if row.is_locked
            ),
            Decimal("0.00"),
        )
        return {
            "items": items, "limit_total": maximum, "limit_used": used,
            "limit_remaining": max(Decimal("0.00"), maximum - used) if maximum is not None else None,
            "limit_locked": locked_total,
        }

    def selection_view(
        self,
        db,
        submission,
        field: Mapping[str, Any],
        availability: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        form=None,
    ) -> dict[str, Any]:
        catalog_service = TrainingCatalogService()
        configured_field = (
            catalog_service.get_training_field(form) if form is not None else None
        )
        if configured_field is not None:
            field = configured_field
        summary = self.summary(db, submission, field)
        rows = {
            row.training_id: row
            for row in db.query(SubmissionTraining)
            .filter(SubmissionTraining.submission_id == submission.id)
            .all()
        }
        catalog = (
            catalog_service.get_available_trainings_for_form(
                form,
                availability,
                active_only=True,
            )
            if form is not None
            else catalog_service.get_trainings_for_field(
                field,
                availability,
                active_only=True,
            )
        )
        catalog_ids = {item["id"] for item in catalog}
        for historical in summary["items"]:
            if historical["id"] not in catalog_ids:
                catalog.append({**historical, "active": False})

        items = []
        for item in catalog:
            row = rows.get(item["id"])
            is_selected = bool(
                row and (row.status in ACTIVE_STATUSES or row.is_locked)
            )
            is_locked = bool(row and row.is_locked)
            occupied = int(item.get("occupied_seats") or 0) + (1 if is_selected else 0)
            capacity = item.get("capacity")
            display_available = (
                max(int(capacity) - occupied, 0) if capacity is not None else None
            )
            status = row.status if row else "available"
            items.append(
                {
                    **item,
                    "is_selected": is_selected,
                    "is_locked": is_locked,
                    "participant_status": status,
                    "participant_status_label": self.status_label(status, is_locked),
                    "display_occupied_seats": occupied,
                    "display_available_seats": display_available,
                    "is_low_availability": (
                        display_available is not None and 0 < display_available <= 5
                    ),
                }
            )
        return {"catalog": items, "summary": summary}

    @staticmethod
    def status_label(status: str, is_locked: bool = False) -> str:
        if status == "agreement_signed_by_office":
            return PARTICIPANT_STATUS_LABELS[status]
        if is_locked and status not in PARTICIPANT_STATUS_LABELS:
            return PARTICIPANT_STATUS_LABELS["locked"]
        return PARTICIPANT_STATUS_LABELS.get(status, "Dostępne")

    def save(
        self,
        db,
        submission,
        field: Mapping[str, Any],
        selected_ids: list[str],
        availability: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.synchronize_legacy(db, submission)
        catalog = {
            item["id"]: item
            for item in TrainingCatalogService.get_trainings_for_field(
                field,
                active_only=True,
            )
        }
        selected = {str(item).strip() for item in selected_ids if str(item).strip()}
        if selected - set(catalog):
            raise TrainingSelectionError("Wybrano szkolenie, które nie jest już dostępne.")
        rows = {row.training_id: row for row in db.query(SubmissionTraining).filter(SubmissionTraining.submission_id == submission.id).all()}
        locked = {key for key, row in rows.items() if row.is_locked}
        selected |= locked
        if field.get("required") and not selected:
            raise TrainingSelectionError("Wybierz co najmniej jedno szkolenie.")
        availability = availability or {}
        unavailable = [
            catalog[key]["name"]
            for key in selected - locked
            if availability.get(key, {}).get("available_seats") == 0
        ]
        if unavailable:
            raise TrainingSelectionError(f"Brak wolnych miejsc dla szkolenia: {unavailable[0]}.")
        total = sum(
            (parse_decimal_price(rows[key].training_price_snapshot if key in locked else catalog[key].get("price")) or Decimal("0.00"))
            for key in selected
        )
        maximum = TrainingCatalogService.financial_limit(field)
        if maximum is not None and total > maximum:
            raise TrainingSelectionError(f"Łączna wartość szkoleń przekracza limit {format_price_pln(maximum, field.get('currency'))}.")
        now = datetime.now(timezone.utc)
        for training_id, training in catalog.items():
            row = rows.get(training_id)
            if training_id in selected:
                if row is None:
                    row = self._new_row(submission.id, training)
                    db.add(row)
                    rows[training_id] = row
                elif not row.is_locked:
                    row.status, row.unselected_at = "selected", None
                row.updated_at = now
            elif row is not None and not row.is_locked:
                row.status, row.unselected_at, row.updated_at = "cancelled", now, now
        db.flush()
        submission.selected_trainings = json.dumps([
            {"id": row.training_id, "name": row.training_name_snapshot, "price": row.training_price_snapshot, "currency": field.get("currency", "PLN")}
            for row in rows.values() if row.status in ACTIVE_STATUSES or row.is_locked
        ], ensure_ascii=False)
        has_selection = any(
            row.status in ACTIVE_STATUSES or row.is_locked for row in rows.values()
        )
        agreement_required = str(
            getattr(submission, "agreement_required", "") or ""
        ).strip().lower() == "tak"
        submission.process_status = (
            ProcessStatus.AGREEMENT_READY.value
            if has_selection and agreement_required
            else ProcessStatus.PARTICIPANT_ACCEPTED.value
            if has_selection
            else ProcessStatus.TRAINING_SELECTION_OPEN.value
        )
        submission.workflow_step = submission.process_status
        return self.summary(db, submission, field)

    def associate_generated_agreements(
        self, db, submission, agreements: list[Mapping[str, Any]]
    ) -> None:
        self.synchronize_legacy(db, submission)
        rows = {
            row.training_id: row
            for row in db.query(SubmissionTraining)
            .filter(SubmissionTraining.submission_id == submission.id)
            .all()
        }
        now = datetime.now(timezone.utc)
        for agreement in agreements:
            training_id = str(
                agreement.get("training_id") or agreement.get("id") or ""
            ).strip()
            row = rows.get(training_id)
            if row is None:
                continue
            row.agreement_id = str(agreement.get("id") or training_id)
            if not row.is_locked:
                row.status = "agreement_generated"
            row.updated_at = now
        db.flush()

    def lock_for_agreement(
        self,
        db,
        submission,
        agreement_id: str,
        *,
        agreement_file_id: int | None = None,
    ) -> bool:
        try:
            agreements = json.loads(str(submission.training_agreements or "[]"))
        except (TypeError, json.JSONDecodeError):
            agreements = []
        agreement = next((item for item in agreements if isinstance(item, dict) and str(item.get("id") or "") == str(agreement_id)), None)
        if not agreement:
            return False
        training = agreement.get("training") if isinstance(agreement.get("training"), dict) else agreement
        training_id = str(training.get("id") or training.get("training_id") or agreement.get("training_id") or "").strip()
        if not training_id:
            return False
        row = db.query(SubmissionTraining).filter(
            SubmissionTraining.submission_id == submission.id, SubmissionTraining.training_id == training_id
        ).one_or_none()
        if row is None:
            row = self._new_row(submission.id, training)
            db.add(row)
        now = datetime.now(timezone.utc)
        row.status, row.is_locked, row.locked_at = "agreement_uploaded_by_beneficiary", True, now
        row.locked_by_event, row.agreement_id, row.updated_at = "agreement_uploaded_by_beneficiary", str(agreement_id), now
        row.agreement_file_id = agreement_file_id
        db.flush()
        return True

    @staticmethod
    def _new_row(submission_id: int, training: Mapping[str, Any]) -> SubmissionTraining:
        now = datetime.now(timezone.utc)
        return SubmissionTraining(
            submission_id=submission_id,
            training_id=str(training.get("id") or training.get("training_id") or training.get("name") or ""),
            training_name_snapshot=str(training.get("name") or training.get("training_name") or training.get("id") or ""),
            training_price_snapshot=str(training.get("price") or training.get("training_price") or "0.00"),
            status="selected", selected_at=now, created_at=now, updated_at=now,
        )
