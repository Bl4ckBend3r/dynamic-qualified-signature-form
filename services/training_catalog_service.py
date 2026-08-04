from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any, Mapping

from services.training_service import (
    LOW_SEATS_THRESHOLD,
    build_training_catalog_view,
    format_price_pln,
    normalize_training_dates,
    normalize_training_id,
    parse_decimal_price,
)


class TrainingCatalogService:
    """Canonical view of the training catalog stored in a form definition."""

    @staticmethod
    def get_training_field(form_or_definition: Any) -> dict | None:
        definition = (
            getattr(form_or_definition, "definition_json", None)
            if not isinstance(form_or_definition, Mapping)
            else form_or_definition
        )
        if not isinstance(definition, Mapping):
            return None
        if definition.get("id") == "declaration":
            declaration = definition
        else:
            documents = definition.get("documents") or {}
            if isinstance(documents, list):
                declaration = next(
                    (
                        document
                        for document in documents
                        if isinstance(document, Mapping)
                        and document.get("id") == "declaration"
                    ),
                    {},
                )
            elif isinstance(documents, Mapping):
                declaration = documents.get("declaration") or {}
            else:
                declaration = {}
        for field in declaration.get("fields") or []:
            if (
                isinstance(field, Mapping)
                and field.get("type") == "training_selection"
            ):
                return dict(field)
        return None

    def get_available_trainings_for_form(
        self,
        form,
        availability: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        active_only: bool = True,
    ) -> list[dict]:
        field = self.get_training_field(form)
        if not field or not field.get("enabled", True):
            return []
        return self.get_trainings_for_field(
            field,
            availability,
            active_only=active_only,
        )

    @staticmethod
    def get_trainings_for_field(
        field: Mapping[str, Any],
        availability: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        active_only: bool = True,
    ) -> list[dict]:
        catalog = build_training_catalog_view(
            field,
            availability,
            active_only=active_only,
        )
        result = []
        for training in catalog:
            available = training.get("available_seats")
            result.append(
                {
                    **training,
                    "has_no_dates": not bool(training.get("dates")),
                    "is_low_availability": (
                        available is not None
                        and 0 < int(available) <= LOW_SEATS_THRESHOLD
                    ),
                }
            )
        return result

    @staticmethod
    def financial_limit(field: Mapping[str, Any]) -> Decimal | None:
        return parse_decimal_price(field.get("max_total_amount"))

    def select_trainings(
        self,
        field: Mapping[str, Any],
        selected_ids: set[str],
        availability: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> tuple[list[dict], str | None]:
        selected = []
        for training in self.get_trainings_for_field(
            field,
            availability,
            active_only=True,
        ):
            if training["id"] not in selected_ids:
                continue
            if training.get("available_seats") == 0:
                return [], (
                    f"Brak wolnych miejsc dla szkolenia: {training['name']}."
                )
            selected.append(training)

        if field.get("required") and not selected:
            return [], "Wybierz co najmniej jedno szkolenie."

        maximum = self.financial_limit(field)
        total = sum(
            (
                parse_decimal_price(training.get("price")) or Decimal("0.00")
                for training in selected
            ),
            Decimal("0.00"),
        )
        if maximum is not None and total > maximum:
            return selected, (
                "Łączna wartość szkoleń przekracza limit "
                f"{format_price_pln(maximum, field.get('currency'))}."
            )
        return selected, None

    def validate_field(self, field: Mapping[str, Any] | None) -> list[str]:
        if not field or not field.get("enabled", True):
            return []
        errors: list[str] = []
        maximum = self.financial_limit(field)
        if maximum is None or maximum <= 0:
            errors.append(
                "Limit finansowania szkoleń musi być liczbą większą od zera."
            )
        has_active_training = False
        training_ids: set[str] = set()
        for index, training in enumerate(field.get("catalog") or [], start=1):
            if not isinstance(training, Mapping):
                errors.append(f"Szkolenie {index} ma niepoprawną konfigurację.")
                continue
            active = training.get("active", True)
            is_active = (
                active
                if isinstance(active, bool)
                else str(active).strip().lower()
                not in {"0", "false", "nie", "no", "off", "inactive"}
            )
            if training.get("archived"):
                is_active = False
            has_active_training = has_active_training or is_active
            name = str(training.get("name") or "").strip()
            training_id = str(training.get("id") or "").strip()
            if not training_id:
                errors.append(
                    f"Szkolenie „{name or index}” musi mieć stabilne techniczne ID."
                )
            elif training_id in training_ids:
                errors.append(
                    f"Techniczne ID szkolenia „{training_id}” jest zduplikowane."
                )
            training_ids.add(training_id)
            if is_active and not name:
                errors.append(f"Aktywne szkolenie {index} musi mieć nazwę.")
            price = parse_decimal_price(training.get("price"))
            if price is None:
                errors.append(
                    f"Cena szkolenia „{name or index}” musi być liczbą nieujemną."
                )
            elif price < 0:
                errors.append(
                    f"Cena szkolenia „{name or index}” nie może być ujemna."
                )
        if not has_active_training:
            errors.append(
                "Skonfiguruj co najmniej jedno aktywne szkolenie dla publicznego etapu wyboru."
            )
        return errors

    def reconcile_definition(
        self,
        current_definition: Mapping[str, Any],
        updated_definition: Mapping[str, Any],
        *,
        used_training_ids: set[str],
        removal_reasons: Mapping[str, str] | None = None,
        actor_id: int | None = None,
        now: datetime | None = None,
    ) -> tuple[dict, list[dict]]:
        """Apply safe deletion/versioning rules to an edited embedded catalog."""
        current_field = self.get_training_field(current_definition)
        if current_field is None:
            return dict(updated_definition), []

        reconciled_definition = deepcopy(dict(updated_definition))
        updated_field = self.get_training_field(reconciled_definition)
        if updated_field is None:
            updated_field = {
                **current_field,
                "enabled": False,
                "catalog": [],
            }

        timestamp = (now or datetime.now(timezone.utc)).isoformat()
        reasons = {
            str(key): str(value or "").strip()
            for key, value in (removal_reasons or {}).items()
        }
        current_items: dict[str, dict] = {}
        for raw_item in current_field.get("catalog") or []:
            if not isinstance(raw_item, Mapping):
                continue
            item = dict(raw_item)
            training_id = normalize_training_id(
                item.get("id") or item.get("name")
            )
            if not training_id:
                continue
            item["id"] = training_id
            current_items[training_id] = item
        submitted_items = [
            dict(item)
            for item in updated_field.get("catalog") or []
            if isinstance(item, Mapping)
        ]
        submitted_by_id = {
            str(item.get("id") or "").strip(): item
            for item in submitted_items
            if str(item.get("id") or "").strip()
        }
        actions: list[dict] = []
        result: list[dict] = []

        for item in submitted_items:
            training_id = str(item.get("id") or "").strip()
            old = current_items.get(training_id)
            if old and old.get("archived"):
                result.append(dict(old))
                continue
            if old:
                old_version = self._version(old)
                item["version"] = (
                    old_version + 1
                    if self._snapshot_fingerprint(old)
                    != self._snapshot_fingerprint(item)
                    else old_version
                )
                item["created_at"] = str(old.get("created_at") or timestamp)
                if self._is_active(old) and not self._is_active(item):
                    reason = reasons.get(
                        training_id,
                        "Dezaktywacja szkolenia w panelu administratora.",
                    )
                    actions.append(
                        self._lifecycle_action(
                            "deactivated",
                            old,
                            item,
                            reason,
                            training_id in used_training_ids,
                            actor_id,
                            timestamp,
                        )
                    )
            else:
                item["version"] = self._version(item)
                item["created_at"] = str(item.get("created_at") or timestamp)
            result.append(item)

        for training_id, old in current_items.items():
            if training_id in submitted_by_id:
                continue
            if old.get("archived"):
                result.append(dict(old))
                continue
            reason = reasons.get(
                training_id,
                "Usunięcie szkolenia w panelu administratora.",
            )
            was_used = training_id in used_training_ids
            if was_used:
                archived = {
                    **old,
                    "active": False,
                    "archived": True,
                    "archived_at": timestamp,
                    "archived_by_id": actor_id,
                    "archive_reason": reason,
                    "was_used": True,
                    "version": self._version(old),
                }
                result.append(archived)
                actions.append(
                    self._lifecycle_action(
                        "archived",
                        old,
                        archived,
                        reason,
                        True,
                        actor_id,
                        timestamp,
                    )
                )
            else:
                actions.append(
                    self._lifecycle_action(
                        "deleted",
                        old,
                        None,
                        reason,
                        False,
                        actor_id,
                        timestamp,
                    )
                )

        updated_field["catalog"] = sorted(
            result,
            key=lambda item: (
                bool(item.get("archived")),
                int(item.get("sort_order") or 0),
                str(item.get("name") or "").lower(),
            ),
        )
        if not any(
            self._is_active(training)
            for training in updated_field["catalog"]
        ):
            updated_field["enabled"] = False
        self._replace_training_field(reconciled_definition, updated_field)
        return reconciled_definition, actions

    @staticmethod
    def build_snapshot(training: Mapping[str, Any]) -> dict:
        dates = normalize_training_dates(training.get("dates"))
        locations = [
            location
            for location in dict.fromkeys(
                [
                    str(training.get("location") or "").strip(),
                    *[
                        str(date.get("location") or "").strip()
                        for date in dates
                        if isinstance(date, Mapping)
                    ],
                ]
            )
            if location
        ]
        return {
            "id": str(
                training.get("id")
                or training.get("training_id")
                or training.get("value")
                or ""
            ).strip(),
            "name": str(
                training.get("name")
                or training.get("training_name")
                or training.get("label")
                or ""
            ).strip(),
            "price": str(
                training.get("price")
                or training.get("training_price")
                or "0.00"
            ),
            "currency": str(training.get("currency") or "PLN").strip() or "PLN",
            "capacity": training.get("capacity"),
            "description": str(training.get("description") or "").strip(),
            "dates": dates,
            "location": locations[0] if locations else "",
            "locations": locations,
            "version": TrainingCatalogService._version(training),
        }

    @staticmethod
    def _replace_training_field(definition: dict, field: Mapping[str, Any]) -> None:
        documents = definition.get("documents")
        if isinstance(documents, list):
            declaration = next(
                (
                    document
                    for document in documents
                    if isinstance(document, dict)
                    and document.get("id") == "declaration"
                ),
                None,
            )
            if declaration is None:
                declaration = {
                    "id": "declaration",
                    "enabled": True,
                    "fields": [],
                }
                documents.append(declaration)
        else:
            if not isinstance(documents, dict):
                documents = {}
                definition["documents"] = documents
            declaration = documents.setdefault(
                "declaration",
                {"enabled": True, "fields": []},
            )
        fields = [
            dict(item)
            for item in declaration.get("fields") or []
            if isinstance(item, Mapping)
        ]
        index = next(
            (
                position
                for position, item in enumerate(fields)
                if item.get("type") == "training_selection"
            ),
            len(fields),
        )
        fields = [
            item for item in fields if item.get("type") != "training_selection"
        ]
        fields.insert(min(index, len(fields)), dict(field))
        declaration["fields"] = fields

    @staticmethod
    def _snapshot_fingerprint(training: Mapping[str, Any]) -> str:
        snapshot = TrainingCatalogService.build_snapshot(training)
        snapshot.pop("id", None)
        snapshot.pop("version", None)
        return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _version(training: Mapping[str, Any]) -> int:
        try:
            return max(int(training.get("version") or 1), 1)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _is_active(training: Mapping[str, Any]) -> bool:
        if training.get("archived"):
            return False
        active = training.get("active", True)
        if isinstance(active, bool):
            return active
        return str(active).strip().lower() not in {
            "0",
            "false",
            "nie",
            "no",
            "off",
            "inactive",
        }

    @staticmethod
    def _lifecycle_action(
        action: str,
        old_value: Mapping[str, Any],
        new_value: Mapping[str, Any] | None,
        reason: str,
        was_used: bool,
        actor_id: int | None,
        timestamp: str,
    ) -> dict:
        return {
            "action": action,
            "training_id": str(old_value.get("id") or ""),
            "training_name": str(old_value.get("name") or ""),
            "old_value": dict(old_value),
            "new_value": dict(new_value) if new_value is not None else None,
            "reason": reason,
            "was_used": was_used,
            "actor_id": actor_id,
            "created_at": timestamp,
        }
