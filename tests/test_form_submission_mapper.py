from datetime import date

from services.form_submission_mapper import (
    build_submission_from_form,
    validate_required_submission_fields,
)


def test_build_submission_from_form_maps_legacy_field_names(form_definition, valid_form_data):
    mapped = build_submission_from_form(valid_form_data, form_definition)

    assert mapped["imiona"] == valid_form_data["imie"]
    assert mapped["zamieszkuje_lubuskie"] == valid_form_data["zamieszkanie_lubuskie"]
    assert mapped["pracuje_lubuskie"] == valid_form_data["praca_lubuskie"]
    assert mapped["osoba_niepelnosprawna"] == valid_form_data["osoba_z_niepelnosprawnosciami"]
    assert mapped["osw_regulamin"] is True
    assert mapped["osw_rodo"] is True
    assert mapped["osw_prawdziwosc"] is True
    assert mapped["data_urodzenia"] == date(1990, 1, 1)
    assert mapped["wiek"] == 36


def test_build_submission_from_form_handles_json_booleans(form_definition, valid_form_data):
    payload = {**valid_form_data, "accept_rodo": False}

    mapped = build_submission_from_form(payload, form_definition)

    assert mapped["osw_rodo"] is False


def test_validate_required_submission_fields_requires_core_fields_and_consents(form_definition, valid_form_data):
    payload = {**valid_form_data, "imie": "", "accept_regulamin": "Nie"}
    mapped = build_submission_from_form(payload, form_definition)

    errors = validate_required_submission_fields(mapped, form_definition)

    assert "imiona" in errors
    assert "osw_regulamin" in errors


def test_dynamic_form_does_not_require_legacy_columns_absent_from_definition():
    form_definition = {
        "fields": [
            {"name": "imie", "type": "text", "required": True},
            {"name": "nazwisko", "type": "text", "required": True},
            {"name": "telefon", "type": "tel", "required": True},
            {"name": "wybrane_szkolenie", "type": "select", "required": True, "options": ["Kompetencje cyfrowe"]},
        ]
    }
    payload = {
        "imie": "Jan",
        "nazwisko": "Kowalski",
        "telefon": "600700800",
        "wybrane_szkolenie": "Kompetencje cyfrowe",
    }

    mapped = build_submission_from_form(payload, form_definition)
    errors = validate_required_submission_fields(mapped, form_definition)

    assert errors == {}
