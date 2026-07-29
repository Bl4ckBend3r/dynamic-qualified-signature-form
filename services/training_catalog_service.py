from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from services.training_service import (
    LOW_SEATS_THRESHOLD,
    build_training_catalog_view,
    format_price_pln,
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
            has_active_training = has_active_training or is_active
            name = str(training.get("name") or "").strip()
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
