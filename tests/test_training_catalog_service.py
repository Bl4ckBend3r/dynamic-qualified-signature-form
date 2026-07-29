from types import SimpleNamespace

from services.training_catalog_service import TrainingCatalogService


def training_field(**overrides):
    field = {
        "type": "training_selection",
        "name": "selected_trainings",
        "enabled": True,
        "currency": "PLN",
        "max_total_amount": "7000",
        "catalog": [
            {
                "id": "active",
                "name": "Analiza danych",
                "description": "Pełny opis z panelu administratora.",
                "price": "6200",
                "capacity": 10,
                "admin_comment": "Przynieś własny laptop.",
                "active": True,
                "sort_order": 2,
                "dates": [
                    {
                        "start_date": "2026-09-01",
                        "start_time": "09:00",
                        "end_time": "15:00",
                        "location": "Zielona Góra",
                    }
                ],
            },
            {
                "id": "inactive",
                "name": "Szkolenie archiwalne",
                "price": "12",
                "capacity": 5,
                "active": False,
                "sort_order": 1,
                "dates": [],
            },
        ],
    }
    return {**field, **overrides}


def test_catalog_reads_definition_json_and_returns_full_admin_data():
    field = training_field()
    form = SimpleNamespace(
        definition_json={
            "documents": [
                {
                    "id": "declaration",
                    "fields": [field],
                }
            ]
        }
    )

    catalog = TrainingCatalogService().get_available_trainings_for_form(
        form,
        {
            "active": {
                "occupied_seats": 3,
                "available_seats": 7,
                "is_available": True,
            }
        },
    )

    assert [item["id"] for item in catalog] == ["active"]
    assert catalog[0]["description"] == "Pełny opis z panelu administratora."
    assert catalog[0]["price_formatted"] == "6 200,00 zł"
    assert catalog[0]["admin_comment"] == "Przynieś własny laptop."
    assert catalog[0]["dates"][0]["location"] == "Zielona Góra"
    assert catalog[0]["occupied_seats"] == 3
    assert catalog[0]["available_seats"] == 7
    assert catalog[0]["has_no_dates"] is False
    assert catalog[0]["sort_order"] == 2
    assert TrainingCatalogService.financial_limit(field) == 7000


def test_catalog_marks_missing_dates_and_can_include_inactive_history():
    catalog = TrainingCatalogService.get_trainings_for_field(
        training_field(),
        active_only=False,
    )

    inactive = next(item for item in catalog if item["id"] == "inactive")
    assert inactive["active"] is False
    assert inactive["has_no_dates"] is True


def test_catalog_validation_rejects_invalid_limit_price_and_active_name():
    service = TrainingCatalogService()
    field = training_field(
        max_total_amount="0",
        catalog=[
            {"id": "missing", "name": "", "price": "20", "active": True},
            {"id": "negative", "name": "Ujemne", "price": "-1", "active": True},
            {"id": "invalid", "name": "Błędne", "price": "abc", "active": True},
        ],
    )

    errors = service.validate_field(field)

    assert "Limit finansowania szkoleń musi być liczbą większą od zera." in errors
    assert "Aktywne szkolenie 1 musi mieć nazwę." in errors
    assert any("nie może być ujemna" in error for error in errors)
    assert any("musi być liczbą nieujemną" in error for error in errors)


def test_catalog_validation_requires_an_active_training():
    errors = TrainingCatalogService().validate_field(
        training_field(
            catalog=[
                {
                    "id": "inactive",
                    "name": "Archiwalne",
                    "price": "10",
                    "active": False,
                }
            ]
        )
    )

    assert (
        "Skonfiguruj co najmniej jedno aktywne szkolenie dla publicznego etapu wyboru."
        in errors
    )
