import pytest

from form_loader import (
    normalize_form_definition,
    validate_form_definition,
)


def test_repeatable_group_definition_is_valid():
    form_definition = {
        "title": "Test",
        "fields": [
            {
                "type": "repeatable_group",
                "name": "podrozni",
                "label": "Podróżni",
                "min_items": 1,
                "max_items": 20,
                "fields": [
                    {
                        "type": "text",
                        "name": "imie_nazwisko",
                        "label": "Imię i nazwisko",
                        "required": True,
                    },
                    {
                        "type": "email",
                        "name": "email",
                        "label": "Adres e-mail",
                    },
                    {
                        "type": "time",
                        "name": "godzina_wylotu",
                        "label": "Godzina wylotu",
                    },
                ],
            }
        ],
    }

    validate_form_definition(form_definition)


def test_repeatable_group_gets_default_configuration():
    form_definition = {
        "title": "Test",
        "fields": [
            {
                "type": "repeatable_group",
                "name": "podrozni",
                "fields": [
                    {
                        "type": "text",
                        "name": "imie",
                    }
                ],
            }
        ],
    }

    normalized = normalize_form_definition(form_definition)

    group = normalized["fields"][0]

    assert group["min_items"] == 1
    assert group["max_items"] == 20
    assert group["add_label"] == "Dodaj"
    assert group["item_label"] == "Element"

    child = group["fields"][0]

    assert child["name"] == "imie"
    assert child["type"] == "text"
    assert "required" in child
    assert "stage" in child


def test_repeatable_group_cannot_contain_repeatable_group():
    form_definition = {
        "title": "Test",
        "fields": [
            {
                "type": "repeatable_group",
                "name": "podrozni",
                "fields": [
                    {
                        "type": "repeatable_group",
                        "name": "bagaze",
                        "fields": [],
                    }
                ],
            }
        ],
    }

    with pytest.raises(
        ValueError,
        match="nie może być zagnieżdżone",
    ):
        validate_form_definition(form_definition)


def test_repeatable_group_rejects_max_items_lower_than_min_items():
    form_definition = {
        "title": "Test",
        "fields": [
            {
                "type": "repeatable_group",
                "name": "podrozni",
                "min_items": 10,
                "max_items": 2,
                "fields": [],
            }
        ],
    }

    with pytest.raises(
        ValueError,
        match="max_items",
    ):
        validate_form_definition(form_definition)


def test_repeatable_group_rejects_duplicate_nested_field_names():
    form_definition = {
        "title": "Test",
        "fields": [
            {
                "type": "repeatable_group",
                "name": "podrozni",
                "fields": [
                    {
                        "type": "text",
                        "name": "email",
                    },
                    {
                        "type": "email",
                        "name": "email",
                    },
                ],
            }
        ],
    }

    with pytest.raises(
        ValueError,
        match="zduplikowaną",
    ):
        validate_form_definition(form_definition)


def test_repeatable_group_rejects_unknown_nested_field_type():
    form_definition = {
        "title": "Test",
        "fields": [
            {
                "type": "repeatable_group",
                "name": "podrozni",
                "fields": [
                    {
                        "type": "unknown_type",
                        "name": "test",
                    }
                ],
            }
        ],
    }

    with pytest.raises(
        ValueError,
        match="Nieobsługiwany typ pola",
    ):
        validate_form_definition(form_definition)