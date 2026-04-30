import pytest

from form_loader import (
    build_consents_view,
    build_submission_view,
    extract_submission_data,
    normalize_form_definition,
    validate_form_definition,
    validate_pesel,
    validate_submission,
)


class RequestForm(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def make_valid_identifier():
    digits = [9, 0, 0, 1, 0, 1, 1, 2, 3, 4]
    weights = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    control = (10 - (sum(d * w for d, w in zip(digits, weights)) % 10)) % 10
    return "".join(map(str, [*digits, control]))


def make_invalid_identifier():
    value = make_valid_identifier()
    replacement = "0" if value[-1] != "0" else "1"
    return value[:-1] + replacement


def test_validate_pesel_accepts_valid_number():
    assert validate_pesel(make_valid_identifier()) is True


def test_validate_pesel_rejects_invalid_checksum():
    assert validate_pesel(make_invalid_identifier()) is False


def test_validate_pesel_rejects_non_numeric_value():
    assert validate_pesel("abcdefghijk") is False


def test_form_definition_requires_title():
    with pytest.raises(ValueError, match="title"):
        validate_form_definition({"fields": []})


def test_form_definition_rejects_unsupported_field_type():
    with pytest.raises(ValueError, match="Nieobsługiwany typ pola"):
        validate_form_definition({"title": "Test", "fields": [{"type": "unsupported", "name": "x"}]})


def test_normalize_form_definition_adds_defaults(form_definition):
    normalized = normalize_form_definition(form_definition)

    assert normalized["submit_label"] == "Generuj i wyślij"
    assert normalized["signature"]["mode"] == "none"
    assert all("width" in field for field in normalized["fields"])


def test_extract_submission_data_maps_checkboxes_to_tak(form_definition):
    request_form = RequestForm({"imie": " Jan ", "nazwisko": "Kowalski", "accept_regulamin": "Tak"})

    data = extract_submission_data(form_definition, request_form)

    assert data["imie"] == "Jan"
    assert data["nazwisko"] == "Kowalski"
    assert data["accept_regulamin"] == "Tak"
    assert data["accept_rodo"] == "Nie"


def test_validate_submission_accepts_valid_data(form_definition, valid_form_data):
    valid_form_data["pesel"] = make_valid_identifier()

    errors = validate_submission(form_definition, valid_form_data)

    assert errors == {}


def test_validate_submission_requires_required_fields(form_definition, valid_form_data):
    valid_form_data["imie"] = ""
    valid_form_data["accept_regulamin"] = "Nie"

    errors = validate_submission(form_definition, valid_form_data)

    assert "imie" in errors
    assert "accept_regulamin" in errors


def test_validate_submission_rejects_invalid_email(form_definition, valid_form_data):
    valid_form_data["email"] = "niepoprawny-email"

    errors = validate_submission(form_definition, valid_form_data)

    assert errors["email"] == "Podaj poprawny adres e-mail."


def test_validate_submission_rejects_invalid_phone(form_definition, valid_form_data):
    valid_form_data["telefon"] = "abc"

    errors = validate_submission(form_definition, valid_form_data)

    assert errors["telefon"] == "Podaj poprawny numer telefonu."


def test_validate_submission_rejects_invalid_date(form_definition, valid_form_data):
    valid_form_data["data_urodzenia"] = "01-01-1990"

    errors = validate_submission(form_definition, valid_form_data)

    assert errors["data_urodzenia"] == "Podaj poprawną datę w formacie RRRR-MM-DD."


def test_validate_submission_rejects_invalid_select_option(form_definition, valid_form_data):
    valid_form_data["wyksztalcenie"] = "Nieistniejąca opcja"

    errors = validate_submission(form_definition, valid_form_data)

    assert errors["wyksztalcenie"] == "Wybrano nieprawidłową wartość."


def test_build_submission_view_excludes_consents_section(form_definition, valid_form_data):
    valid_form_data["pesel"] = make_valid_identifier()
    view = build_submission_view(form_definition, valid_form_data)

    section_titles = [section["title"] for section in view]

    assert "Dane kandydata" in section_titles
    assert "Oświadczenia" not in section_titles


def test_build_consents_view_contains_required_consents(form_definition, valid_form_data):
    consents = build_consents_view(form_definition, valid_form_data)

    consent_names = {item["name"] for item in consents}

    assert "accept_regulamin" in consent_names
    assert "accept_rodo" in consent_names
    assert all(item["accepted"] is True for item in consents)
