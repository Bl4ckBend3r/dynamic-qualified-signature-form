from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping


_STANDARD_VARIABLES = (
    ("Dane uczestnika", "first_name", "Imię uczestnika", "Jan"),
    ("Dane uczestnika", "last_name", "Nazwisko uczestnika", "Kowalski"),
    ("Dane uczestnika", "participant_name", "Imię i nazwisko", "Jan Kowalski"),
    ("Dane uczestnika", "email", "Adres e-mail", "jan.kowalski@example.org"),
    ("Dane uczestnika", "phone", "Numer telefonu", "+48 500 600 700"),
    ("Dane uczestnika", "pesel", "Numer PESEL", "90010112345"),
    ("Zgłoszenie i formularz", "submission_id", "Publiczny identyfikator zgłoszenia", "ABC-123"),
    ("Zgłoszenie i formularz", "submission_date", "Data złożenia zgłoszenia", "10.08.2026"),
    ("Zgłoszenie i formularz", "form_title", "Tytuł formularza", "Nabór szkoleniowy"),
    ("Zgłoszenie i formularz", "form_slug", "Identyfikator formularza", "nabor-szkoleniowy"),
    ("Bieżąca umowa", "agreement_number", "Numer bieżącej umowy", "ABC-123/1/2026-08-10"),
    ("Bieżąca umowa", "agreement_sequence", "Numer porządkowy umowy w zgłoszeniu", "1"),
    ("Bieżąca umowa", "generated_date", "Data wygenerowania umowy", "2026-08-10"),
    ("Bieżąca umowa", "agreement_training_price_formatted", "Cena szkolenia przypisanego do umowy", "1 500,00 PLN"),
    ("Bieżące szkolenie", "training_name", "Nazwa szkolenia tej umowy", "Zarządzanie projektem"),
    ("Bieżące szkolenie", "training_id", "Identyfikator szkolenia tej umowy", "training-1"),
    ("Bieżące szkolenie", "training_price_formatted", "Cena szkolenia tej umowy", "1 500,00 PLN"),
    ("Bieżące szkolenie", "training_location", "Miejsce szkolenia", "Warszawa"),
    ("Bieżące szkolenie", "training_date", "Termin szkolenia", "20–21.08.2026"),
    ("Wszystkie szkolenia", "all_selected_trainings_total_formatted", "Łączna wartość wszystkich szkoleń", "3 000,00 PLN"),
    ("Wszystkie szkolenia", "selected_trainings_total_formatted", "Łączna wartość wybranych szkoleń", "3 000,00 PLN"),
)


def build_agreement_template_context(
    context: Mapping[str, Any],
    *,
    form_definition: Mapping[str, Any] | None = None,
    training: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(context)
    definition = dict(form_definition or result.get("form_definition") or {})
    current_training = dict(training or result.get("training") or {})
    result.setdefault("form_title", definition.get("title") or definition.get("name") or "")
    result.setdefault("form_slug", definition.get("slug") or result.get("form_slug") or "")
    result.setdefault("training_name", current_training.get("name") or current_training.get("title") or "")
    result.setdefault("training_id", current_training.get("id") or current_training.get("training_id") or "")
    result.setdefault("training_location", current_training.get("location") or "")
    result.setdefault("training_date", current_training.get("date") or current_training.get("date_label") or "")
    result.setdefault("phone", result.get("telefon") or result.get("phone_number") or "")
    result.setdefault("first_name", result.get("imiona") or result.get("imie") or "")
    result.setdefault("last_name", result.get("nazwisko") or "")
    return result


def agreement_variable_catalog(fields: Iterable[Any] = ()) -> list[dict[str, str]]:
    catalog = [
        {"category": category, "name": name, "label": label, "example": example, "placeholder": "{{ " + name + " }}"}
        for category, name, label, example in _STANDARD_VARIABLES
    ]
    known = {item["name"] for item in catalog}
    for field in fields:
        name = str(getattr(field, "name", None) or (field.get("name") if isinstance(field, Mapping) else "") or "").strip()
        if not name or name in known:
            continue
        label = str(getattr(field, "label", None) or (field.get("label") if isinstance(field, Mapping) else "") or name)
        catalog.append({
            "category": "Pola formularza",
            "name": name,
            "label": label,
            "example": f"Przykładowa wartość: {label}",
            "placeholder": "{{ " + name + " }}",
        })
        known.add(name)
    return catalog


def agreement_sample_context(fields: Iterable[Any] = (), *, form_definition: Mapping[str, Any] | None = None) -> dict[str, Any]:
    sample = {item["name"]: item["example"] for item in agreement_variable_catalog(fields)}
    sample["generated_date"] = date.today().isoformat()
    return build_agreement_template_context(sample, form_definition=form_definition)
