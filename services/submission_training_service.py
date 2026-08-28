from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from flask import current_app, has_app_context

from models import Form, FormSubmission, SubmissionFile, SubmissionTraining, SubmissionWorkflowEvent
from services.process_service import ProcessStatus
from services.training_catalog_service import TrainingCatalogService
from services.training_service import (
    format_price_pln,
    parse_decimal_price,
    parse_training_snapshots,
)


ACTIVE_STATUSES = {
    "selected",
    "agreement_generated",
    "agreement_downloaded",
    "agreement_waiting_for_beneficiary_signature",
    "agreement_uploaded_by_beneficiary",
    "agreement_waiting_for_office_signature",
    "agreement_signed_by_office",
    "locked",
}
LOCKING_STATUSES = {
    "agreement_uploaded_by_beneficiary",
    "agreement_waiting_for_office_signature",
    "agreement_signed_by_office",
    "locked",
}
PARTICIPANT_STATUS_LABELS = {
    "selected": "Wybrane",
    "agreement_generated": "Umowa wygenerowana",
    "agreement_downloaded": "Umowa pobrana",
    "agreement_waiting_for_beneficiary_signature": "Umowa oczekuje na podpis beneficjenta",
    "agreement_uploaded_by_beneficiary": "Podpisana umowa wgrana",
    "agreement_waiting_for_office_signature": "Umowa oczekuje na podpis urzędu",
    "agreement_signed_by_office": "Umowa podpisana przez urząd",
    "locked": "Zablokowane",
    "cancelled": "Anulowane",
    "removed": "Usunięte operacyjnie",
    "unselected": "Odznaczone przed podpisaniem umowy",
    "cancelled_before_signed_agreement": "Odznaczone przed podpisaniem umowy",
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
        rows = {
            row.training_id: row
            for row in db.query(SubmissionTraining)
            .filter(SubmissionTraining.submission_id == submission.id)
            .all()
        }
        for snapshot in parse_training_snapshots(submission.selected_trainings):
            training_id = str(snapshot.get("id") or "").strip()
            if training_id in rows:
                continue
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
            if row is None or row.is_locked:
                continue
            row.status = "agreement_uploaded_by_beneficiary"
            row.is_locked = True
            row.locked_at = now
            row.signed_agreement_uploaded_at = now
            row.locked_by_event = "legacy_signed_agreement"
            row.agreement_id = str(agreement.get("id") or training_id)
        if str(submission.process_status or "") == ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value:
            for row in rows.values():
                if row.is_locked:
                    row.status = "agreement_signed_by_office"
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
        try:
            raw_agreements = json.loads(str(submission.training_agreements or "[]"))
        except (TypeError, json.JSONDecodeError):
            raw_agreements = []
        agreements = {
            str(item.get("id") or item.get("training_id") or ""): item
            for item in raw_agreements
            if isinstance(item, Mapping)
        }
        office_signed_filenames = {
            filename for (filename,) in db.query(SubmissionFile.filename).filter(
                SubmissionFile.submission_id == submission.id,
                SubmissionFile.document_type == "agreement_signed_by_office",
                SubmissionFile.status == "signed",
            ).all()
        }
        items = []
        history_items = []
        for row in rows:
            price = parse_decimal_price(row.training_price_snapshot) or Decimal("0.00")
            source = {
                **catalog.get(row.training_id, {}),
                **dict(row.training_snapshot or {}),
            }
            currency = str(
                source.get("currency")
                or field.get("currency")
                or "PLN"
            )
            agreement = agreements.get(row.agreement_id) or agreements.get(row.training_id) or {}
            signed_file = db.get(SubmissionFile, row.agreement_file_id) if row.agreement_file_id else None
            signed_filename = str(agreement.get("signed_filename") or getattr(signed_file, "filename", "") or "")
            item = {
                **source, "id": row.training_id, "name": row.training_name_snapshot or source.get("name"),
                "price": row.training_price_snapshot, "price_formatted": format_price_pln(price, currency),
                "currency": currency,
                "status": row.status, "status_label": self.status_label(row.status, row.is_locked),
                "is_locked": row.is_locked,
                "agreement_id": row.agreement_id,
                "agreement_file_id": row.agreement_file_id,
                "agreement_generated": bool(
                    row.agreement_generated_at
                    or agreement.get("filename")
                    or row.status in ACTIVE_STATUSES - {"selected"}
                ),
                "agreement_downloaded": bool(row.agreement_downloaded_at or row.status in {
                    "agreement_downloaded", "agreement_waiting_for_beneficiary_signature",
                    "agreement_uploaded_by_beneficiary", "agreement_waiting_for_office_signature",
                    "agreement_signed_by_office", "locked",
                }),
                "signed_agreement_uploaded": bool(row.signed_agreement_uploaded_at or row.agreement_file_id or row.is_locked),
                "locked_at": row.locked_at,
                "seat_occupied": bool(row.is_locked or row.status in LOCKING_STATUSES),
                "agreement_filename": str(agreement.get("filename") or ""),
                "signed_agreement_filename": signed_filename,
                "office_signed_agreement_filename": (
                    signed_filename
                    if signed_filename in office_signed_filenames
                    else ""
                ),
                "unselected_at": row.unselected_at,
            }
            if row.status in ACTIVE_STATUSES or row.is_locked:
                items.append(item)
            else:
                history_items.append(item)
        maximum = TrainingCatalogService.financial_limit(field)
        locked_total = sum(
            (
                parse_decimal_price(row.training_price_snapshot) or Decimal("0.00")
                for row in rows
                if row.is_locked
            ),
            Decimal("0.00"),
        )
        pending_total = sum(
            (parse_decimal_price(row.training_price_snapshot) or Decimal("0.00") for row in rows if row.status in ACTIVE_STATUSES and not row.is_locked),
            Decimal("0.00"),
        )
        return {
            "items": items, "history_items": history_items, "limit_total": maximum, "limit_used": locked_total,
            "limit_remaining": max(Decimal("0.00"), maximum - locked_total) if maximum is not None else None,
            "limit_locked": locked_total,
            "limit_pending": pending_total,
            "limit_remaining_after_selection": max(Decimal("0.00"), maximum - locked_total - pending_total) if maximum is not None else None,
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
            seat_occupied = bool(row and (row.is_locked or row.status in LOCKING_STATUSES))
            occupied = int(item.get("occupied_seats") or 0) + (1 if seat_occupied else 0)
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
                    "seat_occupied": seat_occupied,
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
        rows = {row.training_id: row for row in db.query(SubmissionTraining).filter(SubmissionTraining.submission_id == submission.id).all()}
        locked = {key for key, row in rows.items() if row.is_locked}
        historical = {
            key
            for key, row in rows.items()
            if key not in catalog
            and (row.status in ACTIVE_STATUSES or row.is_locked)
        }
        if selected - set(catalog) - historical:
            raise TrainingSelectionError(
                "Wybrano szkolenie, które nie jest już dostępne."
            )
        protected = locked | historical
        selected |= protected
        if field.get("required") and not selected:
            raise TrainingSelectionError("Wybierz co najmniej jedno szkolenie.")
        availability = availability or {}
        unavailable = [
            catalog[key]["name"]
            for key in selected - protected
            if availability.get(key, {}).get("available_seats") == 0
        ]
        if unavailable:
            raise TrainingSelectionError(f"Brak wolnych miejsc dla szkolenia: {unavailable[0]}.")
        total = sum(
            (
                parse_decimal_price(
                    rows[key].training_price_snapshot
                    if key in protected
                    else catalog[key].get("price")
                )
                or Decimal("0.00")
            )
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
                elif not row.is_locked and row.status in {"cancelled", "unselected"}:
                    row.status, row.unselected_at = "selected", None
                row.updated_at = now
            elif row is not None and not row.is_locked:
                row.status, row.unselected_at, row.updated_at = "unselected", now, now
        db.flush()
        submission.selected_trainings = json.dumps(
            [
                self._row_snapshot(row, field)
                for row in rows.values()
                if row.status in ACTIVE_STATUSES or row.is_locked
            ],
            ensure_ascii=False,
        )
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
        if has_app_context():
            current_app.logger.info(
                "training_selection_saved",
                extra={
                    "event": "training_selection_saved",
                    "operation": "training_selection",
                    "submission_pk": submission.id,
                    "selected_count": len(selected),
                },
            )
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
            if not row.is_locked and row.status == "selected":
                row.status = "agreement_generated"
                row.agreement_generated_at = row.agreement_generated_at or now
            row.updated_at = now
        if any(not row.is_locked and row.status == "agreement_generated" for row in rows.values()):
            submission.process_status = ProcessStatus.AGREEMENT_READY.value
            submission.workflow_step = submission.process_status
        db.flush()

    def mark_agreement_downloaded(
        self,
        db,
        submission,
        agreement_key: str,
        *,
        filename: str = "",
    ) -> bool:
        self.synchronize_legacy(db, submission)
        key = str(agreement_key or "").strip()
        wanted_filename = str(filename or "").strip()
        rows = db.query(SubmissionTraining).filter(SubmissionTraining.submission_id == submission.id).all()
        row = next((item for item in rows if key and key in {item.agreement_id, item.training_id}), None)
        if row is None and wanted_filename:
            try:
                agreements = json.loads(str(submission.training_agreements or "[]"))
            except (TypeError, json.JSONDecodeError):
                agreements = []
            agreement = next((item for item in agreements if isinstance(item, Mapping) and str(item.get("filename") or "") == wanted_filename), None)
            if agreement:
                resolved = str(agreement.get("id") or agreement.get("training_id") or "").strip()
                row = next((item for item in rows if resolved in {item.agreement_id, item.training_id}), None)
        if row is None or row.is_locked or row.status in LOCKING_STATUSES:
            return False
        if row.status == "agreement_waiting_for_beneficiary_signature" and row.agreement_downloaded_at:
            return False
        now = datetime.now(timezone.utc)
        previous_status = str(submission.process_status or "")
        previous_step = str(submission.workflow_step or "")
        row.status = "agreement_waiting_for_beneficiary_signature"
        row.agreement_downloaded_at = row.agreement_downloaded_at or now
        row.updated_at = now
        submission.process_status = ProcessStatus.AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE.value
        submission.workflow_step = submission.process_status
        db.add(SubmissionWorkflowEvent(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug=submission.form_slug,
            previous_status=previous_status,
            new_status=submission.process_status,
            previous_step=previous_step,
            new_step=submission.workflow_step,
            actor_role="participant",
            reason="Umowa została pobrana przez beneficjenta.",
            source="agreement_downloaded_by_beneficiary",
            side_effects={"submission_training_id": row.id, "training_id": row.training_id, "agreement_id": row.agreement_id},
        ))
        db.flush()
        return True

    def lock_for_agreement(
        self,
        db,
        submission,
        agreement_id: str,
        *,
        agreement_file_id: int | None = None,
    ) -> bool:
        db.query(Form).filter(Form.slug == submission.form_slug).with_for_update().one_or_none()
        try:
            agreements = json.loads(str(submission.training_agreements or "[]"))
        except (TypeError, json.JSONDecodeError):
            agreements = []
        agreement = next((item for item in agreements if isinstance(item, dict) and str(item.get("id") or "") == str(agreement_id)), None)
        if not agreement:
            return False
        nested_training = agreement.get("training") if isinstance(agreement.get("training"), dict) else None
        training_id = str(
            (nested_training or {}).get("id")
            or agreement.get("training_id")
            or agreement.get("id")
            or ""
        ).strip()
        if not training_id:
            return False
        training = {**agreement, **(nested_training or {}), "id": training_id}
        row = db.query(SubmissionTraining).filter(
            SubmissionTraining.submission_id == submission.id, SubmissionTraining.training_id == training_id
        ).one_or_none()
        if row is None:
            row = self._new_row(submission.id, training)
            db.add(row)
        elif not row.training_snapshot:
            row.training_snapshot = TrainingCatalogService.build_snapshot(
                training
            )
        if row.is_locked:
            return True
        capacity = (row.training_snapshot or {}).get("capacity")
        if capacity not in (None, ""):
            occupied = (
                db.query(SubmissionTraining.id)
                .join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id)
                .filter(
                    FormSubmission.form_slug == submission.form_slug,
                    SubmissionTraining.training_id == training_id,
                    SubmissionTraining.id != row.id,
                    (SubmissionTraining.is_locked.is_(True) | SubmissionTraining.status.in_(LOCKING_STATUSES)),
                )
                .count()
            )
            if occupied >= int(capacity):
                if has_app_context():
                    metrics = current_app.extensions.get("observability_metrics")
                    if metrics is not None:
                        metrics.training_capacity_rejections.inc()
                    current_app.logger.warning(
                        "training_capacity_rejected",
                        extra={"event": "training_capacity_rejected", "operation": "training_capacity_lock"},
                    )
                raise TrainingSelectionError("Brak dostępnych miejsc dla tego szkolenia. Skontaktuj się z administratorem.")
        now = datetime.now(timezone.utc)
        previous_status = str(submission.process_status or "")
        previous_step = str(submission.workflow_step or "")
        row.status, row.is_locked, row.locked_at = "agreement_uploaded_by_beneficiary", True, now
        row.locked_by_event, row.agreement_id, row.updated_at = "agreement_uploaded_by_beneficiary", str(agreement_id), now
        row.agreement_file_id = agreement_file_id
        row.signed_agreement_uploaded_at = now
        submission.process_status = ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value
        submission.workflow_step = submission.process_status
        db.add(SubmissionWorkflowEvent(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug=submission.form_slug,
            previous_status=previous_status,
            new_status=submission.process_status,
            previous_step=previous_step,
            new_step=submission.workflow_step,
            actor_role="participant",
            reason="Podpisana umowa zostaĹ‚a wgrana przez beneficjenta.",
            source="agreement_uploaded_by_beneficiary",
            side_effects={
                "submission_training_id": row.id,
                "training_id": row.training_id,
                "agreement_id": row.agreement_id,
                "agreement_file_id": row.agreement_file_id,
            },
        ))
        db.flush()
        if has_app_context():
            current_app.logger.info(
                "training_seat_locked",
                extra={"event": "training_seat_locked", "operation": "training_capacity_lock", "submission_pk": submission.id},
            )
        return True

    @staticmethod
    def _new_row(submission_id: int, training: Mapping[str, Any]) -> SubmissionTraining:
        now = datetime.now(timezone.utc)
        snapshot = TrainingCatalogService.build_snapshot(training)
        return SubmissionTraining(
            submission_id=submission_id,
            training_id=str(snapshot.get("id") or training.get("training_id") or ""),
            training_name_snapshot=str(snapshot.get("name") or training.get("training_name") or ""),
            training_price_snapshot=str(snapshot.get("price") or training.get("training_price") or "0.00"),
            training_snapshot=snapshot,
            status="selected", selected_at=now, created_at=now, updated_at=now,
        )

    @staticmethod
    def _row_snapshot(
        row: SubmissionTraining,
        field: Mapping[str, Any],
    ) -> dict:
        snapshot = dict(row.training_snapshot or {})
        return {
            **snapshot,
            "id": row.training_id,
            "name": row.training_name_snapshot
            or snapshot.get("name")
            or row.training_id,
            "price": row.training_price_snapshot,
            "currency": str(
                snapshot.get("currency")
                or field.get("currency")
                or "PLN"
            ),
            "dates": list(snapshot.get("dates") or []),
            "location": str(snapshot.get("location") or ""),
            "locations": list(snapshot.get("locations") or []),
            "version": TrainingCatalogService._version(snapshot),
        }
