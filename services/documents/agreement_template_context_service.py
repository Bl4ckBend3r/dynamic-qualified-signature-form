from __future__ import annotations

import html
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

from markupsafe import Markup

from services.status_catalog import WORKFLOW_STATUS_LABELS, get_status_label
from services.training_service import format_price_pln, normalize_training_dates, parse_decimal_price, parse_training_snapshots


def _dynamic_field_example(name: str, label: str, field_type: str) -> str:
    normalized_name = name.strip().casefold()
    common = {
        "stanowisko": "Specjalista ds. projektów",
        "nazwa_pracodawcy": "Przykład Sp. z o.o.",
        "ulica": "Przykładowa",
        "nr_budynku": "12",
        "nr_lokalu": "3",
        "kod_pocztowy": "65-001",
        "miejscowosc": "Zielona Góra",
        "wojewodztwo": "lubuskie",
    }
    if normalized_name in common:
        return common[normalized_name]
    normalized_type = field_type.strip().casefold()
    if normalized_type in {"email"}:
        return "jan.kowalski@example.org"
    if normalized_type in {"date"}:
        return "15.09.2026"
    if normalized_type in {"number", "integer", "decimal"}:
        return "1"
    if normalized_type in {"checkbox", "boolean"}:
        return "Tak"
    if normalized_type in {"textarea", "multiline_text"}:
        return "Przykładowy opis uczestnika projektu."
    if normalized_type in {"select", "radio"}:
        return "Wybrana odpowiedź"
    return label or "Dane przykładowe"


class AgreementVariableCatalog:
    """Single source of truth for variables exposed by every agreement editor."""

    _INTERNAL_CONTEXT_NAMES = frozenset({
        "submission", "training", "form_definition", "submission_view", "consents_view",
        "pdf_image_url", "pdf_image_alt", "pdf_image_alignment", "pdf_image_width",
        "pdf_image_is_logo", "document_type", "selected_trainings_normalized",
        "training_agreement", "agreement", "training_agreements", "generated_at",
    })

    _STANDARD: tuple[dict[str, str], ...] = (
        # Participant and compatible aliases.
        {"category": "Uczestnik", "name": "participant_name", "label": "Imię i nazwisko uczestnika", "type": "text", "example": "Jan Kowalski", "description": "Pełna nazwa uczestnika; zgodny alias używany przez istniejące szablony."},
        {"category": "Uczestnik", "name": "participant_full_name", "label": "Pełne imię i nazwisko", "type": "text", "example": "Jan Kowalski", "description": "Preferowana nazwa pełnych danych uczestnika."},
        {"category": "Uczestnik", "name": "first_name", "label": "Imię uczestnika", "type": "text", "example": "Jan", "description": "Kompatybilny angielski alias pierwszego imienia."},
        {"category": "Uczestnik", "name": "imiona", "label": "Imię lub imiona", "type": "text", "example": "Jan", "description": "Imię lub imiona zapisane w zgłoszeniu."},
        {"category": "Uczestnik", "name": "last_name", "label": "Nazwisko uczestnika", "type": "text", "example": "Kowalski", "description": "Kompatybilny angielski alias nazwiska."},
        {"category": "Uczestnik", "name": "nazwisko", "label": "Nazwisko", "type": "text", "example": "Kowalski", "description": "Nazwisko zapisane w zgłoszeniu."},
        {"category": "Uczestnik", "name": "pesel", "label": "PESEL", "type": "text", "example": "90010112345", "description": "Numer PESEL uczestnika."},
        {"category": "Uczestnik", "name": "email", "label": "Adres e-mail", "type": "email", "example": "jan.kowalski@example.org", "description": "Adres e-mail uczestnika."},
        {"category": "Uczestnik", "name": "telefon", "label": "Numer telefonu", "type": "text", "example": "+48 500 600 700", "description": "Numer telefonu zapisany w zgłoszeniu."},
        {"category": "Uczestnik", "name": "phone", "label": "Numer telefonu (alias)", "type": "text", "example": "+48 500 600 700", "description": "Kompatybilny angielski alias numeru telefonu."},
        {"category": "Uczestnik", "name": "data_urodzenia", "label": "Data urodzenia", "type": "date", "example": "01.01.1990", "description": "Data urodzenia uczestnika, jeśli jest dostępna w zgłoszeniu."},
        {"category": "Uczestnik", "name": "plec", "label": "Płeć", "type": "text", "example": "kobieta", "description": "Płeć uczestnika, jeśli jest dostępna w zgłoszeniu."},
        {"category": "Uczestnik", "name": "wiek", "label": "Wiek", "type": "number", "example": "36", "description": "Wiek uczestnika zapisany lub wyliczony przez formularz."},
        # Address.
        {"category": "Adres", "name": "address_street", "label": "Ulica", "type": "text", "example": "Przykładowa", "description": "Ulica bez numeru budynku."},
        {"category": "Adres", "name": "address_building_number", "label": "Numer budynku", "type": "text", "example": "12", "description": "Numer budynku."},
        {"category": "Adres", "name": "address_apartment_number", "label": "Numer lokalu", "type": "text", "example": "3", "description": "Numer lokalu; może być pusty."},
        {"category": "Adres", "name": "address_postal_code", "label": "Kod pocztowy", "type": "text", "example": "65-001", "description": "Kod pocztowy uczestnika."},
        {"category": "Adres", "name": "address_city", "label": "Miejscowość", "type": "text", "example": "Zielona Góra", "description": "Miejscowość uczestnika."},
        {"category": "Adres", "name": "address_voivodeship", "label": "Województwo", "type": "text", "example": "lubuskie", "description": "Województwo uczestnika."},
        {"category": "Adres", "name": "participant_address", "label": "Adres uczestnika", "type": "multiline_text", "example": "ul. Przykładowa 12/3\n65-001 Zielona Góra\nwoj. lubuskie", "description": "Gotowy adres w układzie wielowierszowym."},
        {"category": "Adres", "name": "participant_address_inline", "label": "Adres uczestnika w jednym wierszu", "type": "text", "example": "ul. Przykładowa 12/3, 65-001 Zielona Góra, woj. lubuskie", "description": "Gotowy adres do użycia w akapicie."},
        # Submission.
        {"category": "Zgłoszenie", "name": "submission_id", "label": "Identyfikator zgłoszenia", "type": "text", "example": "EXAMPLE-0001", "description": "Publiczny identyfikator zachowany dla zgodności."},
        {"category": "Zgłoszenie", "name": "public_submission_id", "label": "Publiczny identyfikator zgłoszenia", "type": "text", "example": "EXAMPLE-0001", "description": "Identyfikator widoczny dla uczestnika."},
        {"category": "Zgłoszenie", "name": "submission_created_at", "label": "Data i czas utworzenia", "type": "datetime", "example": "10.08.2026 10:30", "description": "Data i czas zapisania zgłoszenia."},
        {"category": "Zgłoszenie", "name": "submission_created_date", "label": "Data utworzenia", "type": "date", "example": "10.08.2026", "description": "Data utworzenia bez godziny."},
        {"category": "Zgłoszenie", "name": "submission_date", "label": "Data złożenia (alias)", "type": "date", "example": "10.08.2026", "description": "Kompatybilny alias daty zgłoszenia."},
        {"category": "Zgłoszenie", "name": "submission_status", "label": "Status zgłoszenia", "type": "text", "example": "OFFICER_ACCEPTED", "description": "Techniczny status procesu."},
        {"category": "Zgłoszenie", "name": "submission_status_label", "label": "Nazwa statusu zgłoszenia", "type": "text", "example": "Wniosek zaakceptowany", "description": "Czytelna etykieta statusu."},
        {"category": "Zgłoszenie", "name": "workflow_stage", "label": "Etap workflow", "type": "text", "example": "agreement", "description": "Techniczny identyfikator etapu workflow."},
        {"category": "Zgłoszenie", "name": "workflow_stage_label", "label": "Nazwa etapu workflow", "type": "text", "example": "Umowa", "description": "Czytelna etykieta aktualnego etapu."},
        # Form and configurable project data.
        {"category": "Formularz / projekt", "name": "form_name", "label": "Nazwa formularza", "type": "text", "example": "Nabór szkoleniowy", "description": "Wewnętrzna nazwa formularza."},
        {"category": "Formularz / projekt", "name": "form_title", "label": "Tytuł formularza", "type": "text", "example": "Nabór do projektu", "description": "Publiczny tytuł formularza."},
        {"category": "Formularz / projekt", "name": "form_slug", "label": "Slug formularza", "type": "text", "example": "nabor-szkoleniowy", "description": "Techniczny identyfikator formularza."},
        {"category": "Formularz / projekt", "name": "project_label", "label": "Etykieta projektu", "type": "text", "example": "Projekt UE", "description": "Konfigurowalna etykieta projektu lub formularza."},
        {"category": "Formularz / projekt", "name": "project_name", "label": "Nazwa projektu", "type": "text", "example": "Rozwój kompetencji", "description": "Nazwa projektu z konfiguracji formularza."},
        {"category": "Formularz / projekt", "name": "project_number", "label": "Numer projektu", "type": "text", "example": "FELB.06.03-IZ.00-0001/26", "description": "Numer projektu z konfiguracji formularza."},
        {"category": "Formularz / projekt", "name": "project_program", "label": "Program", "type": "text", "example": "Fundusze Europejskie", "description": "Nazwa programu z konfiguracji."},
        {"category": "Formularz / projekt", "name": "project_action", "label": "Działanie", "type": "text", "example": "6.3 Aktywizacja zawodowa", "description": "Nazwa działania z konfiguracji."},
        {"category": "Formularz / projekt", "name": "funding_source", "label": "Źródło finansowania", "type": "text", "example": "Europejski Fundusz Społeczny Plus", "description": "Źródło finansowania projektu."},
        {"category": "Formularz / projekt", "name": "institution_name", "label": "Nazwa instytucji", "type": "text", "example": "Urząd Marszałkowski", "description": "Nazwa instytucji skonfigurowanej dla projektu."},
        {"category": "Formularz / projekt", "name": "institution_address", "label": "Adres instytucji", "type": "text", "example": "ul. Urzędowa 1, Zielona Góra", "description": "Adres instytucji skonfigurowanej dla projektu."},
        {"category": "Formularz / projekt", "name": "project_logo_url", "label": "Logo projektu", "type": "image_url", "example": "/logo.png", "description": "Adres opcjonalnego logo z konfiguracji formularza."},
        # Agreement.
        {"category": "Umowa", "name": "agreement_number", "label": "Numer umowy", "type": "text", "example": "EXAMPLE-0001/1/2026-08-10", "description": "Numer bieżącej umowy przypisanej do konkretnego szkolenia."},
        {"category": "Umowa", "name": "agreement_sequence", "label": "Numer kolejny umowy", "type": "number", "example": "1", "description": "Numer porządkowy umowy w zgłoszeniu."},
        {"category": "Umowa", "name": "agreement_generated_at", "label": "Czas wygenerowania umowy", "type": "datetime", "example": "10.08.2026 10:30", "description": "Data i czas przygotowania dokumentu."},
        {"category": "Umowa", "name": "generated_date", "label": "Data wygenerowania", "type": "date", "example": "10.08.2026", "description": "Zgodny alias daty wygenerowania."},
        {"category": "Umowa", "name": "agreement_date", "label": "Data umowy", "type": "date", "example": "10.08.2026", "description": "Data zawarcia umowy; domyślnie data wygenerowania."},
        {"category": "Umowa", "name": "agreement_filename", "label": "Nazwa pliku umowy", "type": "text", "example": "Jan_Kowalski-szkolenie-umowa.pdf", "description": "Nazwa pliku bieżącej umowy."},
        {"category": "Umowa", "name": "agreement_valid_from", "label": "Umowa ważna od", "type": "date", "example": "10.08.2026", "description": "Opcjonalna data początku obowiązywania z konfiguracji."},
        {"category": "Umowa", "name": "agreement_valid_to", "label": "Umowa ważna do", "type": "date", "example": "31.12.2026", "description": "Opcjonalna data końca obowiązywania z konfiguracji."},
        # Current training snapshot.
        {"category": "Szkolenie", "name": "training_id", "label": "Identyfikator szkolenia", "type": "text", "example": "training-1", "description": "Identyfikator szkolenia bieżącej umowy."},
        {"category": "Szkolenie", "name": "training_name", "label": "Nazwa szkolenia", "type": "text", "example": "Zarządzanie projektem", "description": "Nazwa ze snapshotu szkolenia w zgłoszeniu."},
        {"category": "Szkolenie", "name": "training_price", "label": "Cena szkolenia", "type": "money", "example": "1500", "description": "Surowa cena ze snapshotu."},
        {"category": "Szkolenie", "name": "training_price_formatted", "label": "Cena szkolenia — sformatowana", "type": "text", "example": "1 500,00 zł", "description": "Cena gotowa do wstawienia w dokumencie."},
        {"category": "Szkolenie", "name": "agreement_training_price", "label": "Cena szkolenia umowy", "type": "money", "example": "1500", "description": "Kompatybilna cena szkolenia przypisanego do umowy."},
        {"category": "Szkolenie", "name": "agreement_training_price_formatted", "label": "Cena szkolenia umowy — sformatowana", "type": "text", "example": "1 500,00 zł", "description": "Kompatybilna sformatowana cena szkolenia."},
        {"category": "Szkolenie", "name": "training_currency", "label": "Waluta szkolenia", "type": "text", "example": "PLN", "description": "Waluta ze snapshotu szkolenia."},
        {"category": "Szkolenie", "name": "training_location", "label": "Lokalizacja szkolenia", "type": "text", "example": "Zielona Góra", "description": "Lokalizacja ze snapshotu."},
        {"category": "Szkolenie", "name": "training_date", "label": "Termin szkolenia", "type": "text", "example": "20–21.08.2026", "description": "Gotowy opis terminu ze snapshotu."},
        {"category": "Szkolenie", "name": "training_start_date", "label": "Data rozpoczęcia", "type": "date", "example": "20.08.2026", "description": "Data rozpoczęcia szkolenia."},
        {"category": "Szkolenie", "name": "training_end_date", "label": "Data zakończenia", "type": "date", "example": "21.08.2026", "description": "Data zakończenia szkolenia."},
        {"category": "Szkolenie", "name": "training_start_time", "label": "Godzina rozpoczęcia", "type": "time", "example": "09:00", "description": "Godzina rozpoczęcia ze snapshotu."},
        {"category": "Szkolenie", "name": "training_end_time", "label": "Godzina zakończenia", "type": "time", "example": "16:00", "description": "Godzina zakończenia ze snapshotu."},
        {"category": "Szkolenie", "name": "training_description", "label": "Opis szkolenia", "type": "multiline_text", "example": "Dwudniowe szkolenie praktyczne", "description": "Opis zapisany w snapshotcie."},
        # Collections and ready fragments.
        {"category": "Wszystkie szkolenia", "name": "selected_trainings", "label": "Szkolenia bieżącej umowy", "type": "collection", "example": "[szkolenie]", "description": "Kolekcja zgodna z dotychczasowym flow; dla umowy per szkolenie zawiera bieżącą pozycję."},
        {"category": "Wszystkie szkolenia", "name": "selected_trainings_count", "label": "Liczba szkoleń bieżącej umowy", "type": "number", "example": "1", "description": "Liczba elementów selected_trainings."},
        {"category": "Wszystkie szkolenia", "name": "selected_trainings_total", "label": "Suma szkoleń bieżącej umowy", "type": "money", "example": "1500", "description": "Suma kolekcji selected_trainings."},
        {"category": "Wszystkie szkolenia", "name": "selected_trainings_total_formatted", "label": "Suma bieżącej umowy — sformatowana", "type": "text", "example": "1 500,00 zł", "description": "Suma gotowa do dokumentu."},
        {"category": "Wszystkie szkolenia", "name": "all_selected_trainings", "label": "Wszystkie wybrane szkolenia", "type": "collection", "example": "[szkolenie 1, szkolenie 2]", "description": "Snapshot wszystkich szkoleń wybranych w zgłoszeniu."},
        {"category": "Wszystkie szkolenia", "name": "all_selected_trainings_total", "label": "Suma wszystkich szkoleń", "type": "money", "example": "3000", "description": "Łączna wartość wszystkich wybranych szkoleń."},
        {"category": "Wszystkie szkolenia", "name": "all_selected_trainings_total_formatted", "label": "Suma wszystkich szkoleń — sformatowana", "type": "text", "example": "3 000,00 zł", "description": "Łączna wartość gotowa do dokumentu."},
        {"category": "Wszystkie szkolenia", "name": "locked_trainings", "label": "Zablokowane szkolenia", "type": "collection", "example": "[szkolenie 1]", "description": "Wybrane szkolenia oznaczone jako zablokowane."},
        {"category": "Wszystkie szkolenia", "name": "locked_trainings_total", "label": "Suma zablokowanych szkoleń", "type": "money", "example": "1500", "description": "Łączna wartość zablokowanych szkoleń."},
        {"category": "Wszystkie szkolenia", "name": "locked_trainings_total_formatted", "label": "Suma zablokowanych — sformatowana", "type": "text", "example": "1 500,00 zł", "description": "Suma gotowa do dokumentu."},
        {"category": "Wszystkie szkolenia", "name": "selected_trainings_table", "label": "Tabela wybranych szkoleń", "type": "html", "example": "Tabela: Lp., nazwa, cena", "description": "Bezpieczny, gotowy fragment HTML bez ręcznej pętli Jinja."},
        {"category": "Wszystkie szkolenia", "name": "selected_trainings_list", "label": "Lista wybranych szkoleń", "type": "html", "example": "1. Zarządzanie projektem", "description": "Bezpieczna, gotowa lista HTML."},
        {"category": "Wszystkie szkolenia", "name": "selected_trainings_text", "label": "Wybrane szkolenia jako tekst", "type": "text", "example": "Zarządzanie projektem — 1 500,00 zł", "description": "Lista tekstowa do użycia w akapicie."},
        # System.
        {"category": "Systemowe", "name": "current_date", "label": "Dzisiejsza data", "type": "date", "example": "10.08.2026", "description": "Bieżąca data w momencie renderowania."},
        {"category": "Systemowe", "name": "current_year", "label": "Bieżący rok", "type": "number", "example": "2026", "description": "Bieżący rok."},
    )

    @classmethod
    def variables(cls, fields: Iterable[Any] = ()) -> list[dict[str, str]]:
        catalog = [dict(item, placeholder="{{ " + item["name"] + " }}") for item in cls._STANDARD]
        known = {item["name"] for item in catalog}
        for field in fields:
            name = _field_value(field, "name").strip()
            if not name or name in known:
                continue
            label = _field_value(field, "label").strip() or name
            field_type = _field_value(field, "field_type").strip() or _field_value(field, "type").strip() or "text"
            catalog.append({
                "category": "Pola formularza",
                "name": name,
                "label": label,
                "type": field_type,
                "example": _dynamic_field_example(name, label, field_type),
                "description": f"Dynamiczne pole formularza „{label}”; jest dostępne automatycznie bez zmiany kodu.",
                "placeholder": "{{ " + name + " }}",
            })
            known.add(name)
        return catalog

    @classmethod
    def names(cls, fields: Iterable[Any] = ()) -> set[str]:
        return {item["name"] for item in cls.variables(fields)}

    @classmethod
    def context_names(cls, fields: Iterable[Any] = ()) -> set[str]:
        return cls.names(fields) | set(cls._INTERNAL_CONTEXT_NAMES)


def agreement_variable_catalog(fields: Iterable[Any] = ()) -> list[dict[str, str]]:
    return AgreementVariableCatalog.variables(fields)


def build_agreement_template_context(
    context: Mapping[str, Any],
    *,
    form_definition: Mapping[str, Any] | None = None,
    training: Mapping[str, Any] | None = None,
    fields: Iterable[Any] = (),
) -> dict[str, Any]:
    result = dict(context)
    nested_data = _mapping(result.get("data_json"))
    submission = _mapping(result.get("submission"))
    nested_submission_data = _mapping(submission.get("data_json"))
    # Dynamic fields remain top-level for Jinja while the original submission
    # mapping stays available for compatible submission.get(...) templates.
    for source in (nested_submission_data, nested_data, submission):
        for key, value in source.items():
            result.setdefault(str(key), value)

    definition = dict(form_definition or result.get("form_definition") or {})
    project = _project_config(definition)
    current_training = _enrich_training_snapshot(training or result.get("training") or {})
    now = datetime.now()

    first_name = _first_nonempty(result, "imiona", "imie", "first_name")
    last_name = _first_nonempty(result, "nazwisko", "last_name")
    participant_name = _first_nonempty(result, "participant_full_name", "participant_name") or " ".join(filter(None, (first_name, last_name)))
    phone = _first_nonempty(result, "telefon", "phone", "phone_number")
    result.update({
        "imiona": first_name,
        "first_name": first_name,
        "nazwisko": last_name,
        "last_name": last_name,
        "participant_name": participant_name,
        "participant_full_name": participant_name,
        "telefon": phone,
        "phone": phone,
        "data_urodzenia": _first_nonempty(result, "data_urodzenia", "birth_date"),
        "plec": _first_nonempty(result, "plec", "gender"),
        "wiek": _first_nonempty(result, "wiek", "age"),
    })

    street = _first_nonempty(result, "address_street", "ulica", "street")
    building = _first_nonempty(result, "address_building_number", "nr_budynku", "building_number")
    apartment = _first_nonempty(result, "address_apartment_number", "nr_lokalu", "apartment_number")
    postal_code = _first_nonempty(result, "address_postal_code", "kod_pocztowy", "postal_code")
    city = _first_nonempty(result, "address_city", "miejscowosc", "city")
    voivodeship = _first_nonempty(result, "address_voivodeship", "wojewodztwo", "voivodeship")
    address_lines = _address_lines(street, building, apartment, postal_code, city, voivodeship)
    result.update({
        "address_street": street,
        "address_building_number": building,
        "address_apartment_number": apartment,
        "address_postal_code": postal_code,
        "address_city": city,
        "address_voivodeship": voivodeship,
        "participant_address": "\n".join(address_lines),
        "participant_address_inline": ", ".join(address_lines),
    })

    public_id = _first_nonempty(result, "public_submission_id", "submission_id")
    created_at = result.get("submission_created_at") or result.get("created_at") or result.get("submission_date") or ""
    raw_status = _first_nonempty(result, "submission_status", "process_status", "status")
    stage = _first_nonempty(result, "workflow_stage", "workflow_step")
    workflow_labels = {
        str(item.get("id") or ""): str(item.get("user_label") or item.get("label") or item.get("admin_label") or item.get("id") or "")
        for item in _list_of_mappings(_mapping(definition.get("workflow")).get("steps"))
    }
    result.update({
        "submission_id": public_id,
        "public_submission_id": public_id,
        "submission_created_at": _format_datetime(created_at),
        "submission_created_date": _format_date(created_at),
        "submission_date": _format_date(created_at),
        "submission_status": raw_status,
        "submission_status_label": get_status_label(raw_status) if raw_status else "",
        "workflow_stage": stage,
        "workflow_stage_label": workflow_labels.get(stage) or WORKFLOW_STATUS_LABELS.get(stage, stage),
    })

    title = str(definition.get("title") or result.get("form_title") or result.get("form_name") or "")
    form_name = str(definition.get("name") or result.get("form_name") or title)
    form_slug = str(definition.get("slug") or result.get("form_slug") or "")
    result.update({
        "form_name": form_name,
        "form_title": title,
        "form_slug": form_slug,
        "project_label": _first_nonempty(project, "label", "project_label") or str(definition.get("label_text") or ""),
        "project_name": _first_nonempty(project, "name", "project_name") or title,
        "project_number": _first_nonempty(project, "number", "project_number"),
        "project_program": _first_nonempty(project, "program", "program_name", "project_program"),
        "project_action": _first_nonempty(project, "action", "action_name", "project_action"),
        "funding_source": _first_nonempty(project, "funding_source", "funding"),
        "institution_name": _first_nonempty(project, "institution_name", "institution"),
        "institution_address": _first_nonempty(project, "institution_address"),
        "project_logo_url": _first_nonempty(project, "logo_url") or str(definition.get("logo_url") or ""),
    })

    generated_at = result.get("agreement_generated_at") or result.get("generated_at") or result.get("generated_date") or now
    generated_date = _format_date(result.get("generated_date") or generated_at)
    result.update({
        "agreement_generated_at": _format_datetime(generated_at),
        "generated_date": generated_date,
        "agreement_date": _format_date(result.get("agreement_date") or generated_date),
        "agreement_filename": str(result.get("agreement_filename") or ""),
        "agreement_valid_from": _format_date(result.get("agreement_valid_from") or project.get("agreement_valid_from") or ""),
        "agreement_valid_to": _format_date(result.get("agreement_valid_to") or project.get("agreement_valid_to") or ""),
        "current_date": now.strftime("%d.%m.%Y"),
        "current_year": now.year,
    })

    result.update(_training_scalar_context(current_training))
    result.setdefault("submission", submission or dict(context))
    catalog_fields = list(fields) or _list_of_mappings(definition.get("fields"))
    for variable in AgreementVariableCatalog.variables(catalog_fields):
        result.setdefault(variable["name"], _empty_catalog_value(variable["type"]))
    return result


def build_agreement_render_context(
    context: Mapping[str, Any],
    *,
    form_definition: Mapping[str, Any] | None = None,
    training: Mapping[str, Any] | None = None,
    all_trainings: Iterable[Mapping[str, Any]] | None = None,
    fields: Iterable[Any] = (),
) -> dict[str, Any]:
    """Complete the shared context used by builder, DOCX, HTML and previews."""
    from services.agreement_context_service import build_training_agreement_value_context

    result = dict(context)
    current_training = _enrich_training_snapshot(training or result.get("training") or {})
    collection_source = all_trainings if all_trainings is not None else result.get("all_selected_trainings")
    training_collection = [_enrich_training_snapshot(item) for item in (collection_source or []) if isinstance(item, Mapping)]
    if not training_collection:
        training_collection = [_enrich_training_snapshot(item) for item in parse_training_snapshots(result.get("selected_trainings"))]
    if current_training and not training_collection:
        training_collection = [current_training]
    if not current_training and training_collection:
        current_training = dict(training_collection[0])

    selected = [_enrich_training_snapshot(item) for item in (result.get("selected_trainings") or []) if isinstance(item, Mapping)]
    if not selected and current_training:
        selected = [current_training]
    result["selected_trainings"] = selected
    result["all_selected_trainings"] = training_collection
    if current_training:
        result.update(build_training_agreement_value_context(training_collection, current_training))

    selected_total = _training_total(selected)
    locked = [item for item in training_collection if bool(item.get("is_locked") or item.get("locked"))]
    locked_total = _training_total(locked)
    currency = str(current_training.get("currency") or (training_collection[0].get("currency") if training_collection else "PLN") or "PLN")
    result.update({
        "selected_trainings_count": len(selected),
        "selected_trainings_total": selected_total,
        "selected_trainings_total_formatted": format_price_pln(selected_total, currency),
        "locked_trainings": locked,
        "locked_trainings_total": locked_total,
        "locked_trainings_total_formatted": format_price_pln(locked_total, currency),
        "selected_trainings_table": _training_table(selected),
        "selected_trainings_list": _training_list(selected),
        "selected_trainings_text": _training_text(selected),
    })
    return build_agreement_template_context(
        result,
        form_definition=form_definition,
        training=current_training,
        fields=fields,
    )


def build_example_agreement_context(fields: Iterable[Any] = (), *, form_definition: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a complete, persistence-free context for agreement previews and example PDFs."""
    fields = tuple(fields)
    catalog = AgreementVariableCatalog.variables(fields)
    sample = {item["name"]: item["example"] for item in catalog}
    sample_trainings = [
        {"id": "example-training", "name": "Przykładowe szkolenie", "price": "1500", "currency": "PLN", "location": "Zielona Góra", "date": "15.09.2026", "start_date": "15.09.2026", "end_date": "16.09.2026", "start_time": "09:00", "end_time": "16:00", "description": "Praktyczne szkolenie", "is_locked": True},
        {"id": "example-training-2", "name": "Drugie szkolenie", "price": "1200", "currency": "PLN", "location": "Gorzów Wielkopolski", "date": "20.10.2026"},
    ]
    sample.update({
        "form_definition": dict(form_definition or {}),
        "submission_id": "EXAMPLE-0001",
        "public_submission_id": "EXAMPLE-0001",
        "created_at": "2026-08-10T10:30:00",
        "imiona": "Jan",
        "nazwisko": "Kowalski",
        "email": "jan.kowalski@example.org",
        "telefon": "+48 500 600 700",
        "ulica": "Przykładowa",
        "nr_budynku": "12",
        "nr_lokalu": "3",
        "kod_pocztowy": "65-001",
        "miejscowosc": "Zielona Góra",
        "wojewodztwo": "lubuskie",
        "process_status": "OFFICER_ACCEPTED",
        "workflow_stage": "agreement",
        "agreement_number": "UM/PRZYKLAD/2026",
        "agreement_sequence": 1,
        "generated_date": date.today(),
        "agreement_filename": "Jan_Kowalski-example-training-umowa.pdf",
        "selected_trainings": [sample_trainings[0]],
        "all_selected_trainings": sample_trainings,
        "training": sample_trainings[0],
        "submission": {},
    })
    for field in fields:
        name = _field_value(field, "name").strip()
        if name:
            sample[name] = next((item["example"] for item in catalog if item["name"] == name), "Dane przykładowe")
    sample["submission"] = dict(sample)
    return build_agreement_render_context(
        sample,
        form_definition=form_definition,
        training=sample_trainings[0],
        all_trainings=sample_trainings,
        fields=fields,
    )


def agreement_sample_context(fields: Iterable[Any] = (), *, form_definition: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Backward-compatible alias for the shared example context provider."""
    return build_example_agreement_context(fields, form_definition=form_definition)


def agreement_preview_context(
    fields: Iterable[Any] = (),
    *,
    form_definition: Mapping[str, Any] | None = None,
    submission: Mapping[str, Any] | None = None,
    training: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if submission is None:
        return build_example_agreement_context(fields, form_definition=form_definition)

    row = _flatten_submission(submission)
    trainings = parse_training_snapshots(row.get("selected_trainings"))
    current_training = dict(training or (trainings[0] if trainings else {}))
    context = {
        **row,
        "form_definition": dict(form_definition or {}),
        "submission_id": str(row.get("submission_id") or "PREVIEW"),
        "public_submission_id": str(row.get("public_submission_id") or row.get("submission_id") or "PREVIEW"),
        "submission": row,
        "submission_view": [],
        "consents_view": [],
        "document_type": "training_agreement",
        "training": current_training,
        "selected_trainings": [current_training] if current_training else [],
        "all_selected_trainings": trainings,
        "agreement_number": str(row.get("agreement_number") or "UM/PODGLAD/2026"),
        "agreement_sequence": 1,
        "generated_date": row.get("generated_date") or date.today(),
    }
    return build_agreement_render_context(
        context,
        form_definition=form_definition,
        training=current_training,
        all_trainings=trainings,
        fields=fields,
    )


def _field_value(field: Any, name: str) -> str:
    value = field.get(name) if isinstance(field, Mapping) else getattr(field, name, None)
    return str(value or "")


def _empty_catalog_value(variable_type: str) -> Any:
    if variable_type == "collection":
        return []
    if variable_type == "html":
        return Markup("")
    if variable_type in {"number", "money"}:
        return 0
    return ""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value] if isinstance(value, list) else []


def _flatten_submission(submission: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(submission)
    for key, value in _mapping(submission.get("data_json")).items():
        result.setdefault(str(key), value)
    return result


def _first_nonempty(source: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = source.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _project_config(definition: Mapping[str, Any]) -> dict[str, Any]:
    project: dict[str, Any] = {}
    for key in ("settings", "project", "project_config"):
        project.update(_mapping(definition.get(key)))
    for name in ("project_label", "project_name", "project_number", "program_name", "action_name", "funding_source", "institution_name", "institution_address", "agreement_valid_from", "agreement_valid_to"):
        if definition.get(name) not in (None, ""):
            project.setdefault(name, definition.get(name))
    return project


def _address_lines(street: str, building: str, apartment: str, postal_code: str, city: str, voivodeship: str) -> list[str]:
    first = ""
    if street:
        first = ("ul. " + street) if not street.casefold().startswith(("ul.", "aleja", "al.", "plac", "pl.")) else street
    number = building + ("/" + apartment if apartment else "")
    first = " ".join(filter(None, (first, number)))
    second = " ".join(filter(None, (postal_code, city)))
    third = ("woj. " + voivodeship) if voivodeship and not voivodeship.casefold().startswith("woj.") else voivodeship
    return [item for item in (first, second, third) if item]


def _format_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except ValueError:
        return text


def _format_datetime(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return text


def _training_scalar_context(training: Mapping[str, Any]) -> dict[str, Any]:
    price = parse_decimal_price(training.get("price")) or Decimal("0")
    currency = str(training.get("currency") or "PLN")
    return {
        "training_id": training.get("id") or training.get("training_id") or "",
        "training_name": training.get("name") or training.get("label") or training.get("title") or "",
        "training_price": price,
        "training_price_formatted": training.get("price_formatted") or format_price_pln(price, currency),
        "training_currency": currency,
        "training_location": training.get("location") or "",
        "training_date": training.get("date") or training.get("date_label") or "",
        "training_start_date": training.get("start_date") or training.get("date_from") or "",
        "training_end_date": training.get("end_date") or training.get("date_to") or "",
        "training_start_time": training.get("start_time") or "",
        "training_end_time": training.get("end_time") or "",
        "training_description": training.get("description") or "",
    }


def _enrich_training_snapshot(training: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(training or {})
    dates = normalize_training_dates(result.get("dates"))
    if dates:
        first = dates[0]
        last = dates[-1]
        derived = {
            "date": "; ".join(str(item.get("label") or item.get("start_date") or "") for item in dates),
            "start_date": first.get("start_date") or "",
            "end_date": last.get("end_date") or last.get("start_date") or "",
            "start_time": first.get("start_time") or "",
            "end_time": last.get("end_time") or "",
            "location": first.get("location") or "",
            "description": first.get("description") or "",
        }
        for key, value in derived.items():
            if not result.get(key):
                result[key] = value
        if not result.get("date_label"):
            result["date_label"] = result["date"]
    return result


def _training_total(trainings: Iterable[Mapping[str, Any]]) -> Decimal:
    return sum((parse_decimal_price(item.get("price")) or Decimal("0") for item in trainings), Decimal("0"))


def _training_table(trainings: list[Mapping[str, Any]]) -> Markup:
    rows = []
    for index, training in enumerate(trainings, start=1):
        name = html.escape(str(training.get("name") or training.get("label") or ""))
        formatted = html.escape(str(training.get("price_formatted") or format_price_pln(training.get("price"), training.get("currency"))))
        rows.append(f"<tr><td>{index}</td><td>{name}</td><td>{formatted}</td></tr>")
    return Markup('<table class="document-table document-training-table"><thead><tr><th>Lp.</th><th>Nazwa szkolenia</th><th>Cena</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def _training_list(trainings: list[Mapping[str, Any]]) -> Markup:
    items = [f"<li>{html.escape(str(item.get('name') or item.get('label') or ''))}</li>" for item in trainings]
    return Markup('<ol class="document-list document-training-list">' + "".join(items) + "</ol>")


def _training_text(trainings: list[Mapping[str, Any]]) -> str:
    return "; ".join(
        f"{item.get('name') or item.get('label') or ''} — {item.get('price_formatted') or format_price_pln(item.get('price'), item.get('currency'))}"
        for item in trainings
    )
