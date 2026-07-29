from types import SimpleNamespace

from werkzeug.datastructures import MultiDict

from services.admin_form_service import parse_training_catalog
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


def definition_with_catalog(catalog):
    return {
        "documents": [
            {
                "id": "declaration",
                "fields": [
                    {
                        "type": "training_selection",
                        "name": "selected_trainings",
                        "enabled": True,
                        "max_total_amount": "7000",
                        "catalog": catalog,
                    }
                ],
            }
        ]
    }


def test_used_training_is_archived_instead_of_deleted_with_audit_data():
    old = {
        "id": "stable-old",
        "name": "Ta sama nazwa",
        "price": "100.00",
        "active": True,
        "version": 3,
    }

    definition, actions = TrainingCatalogService().reconcile_definition(
        definition_with_catalog([old]),
        definition_with_catalog([]),
        used_training_ids={"stable-old"},
        removal_reasons={"stable-old": "Zakończenie naboru."},
        actor_id=42,
    )

    field = TrainingCatalogService.get_training_field(definition)
    archived = field["catalog"][0]
    assert archived["id"] == "stable-old"
    assert archived["active"] is False
    assert archived["archived"] is True
    assert archived["archive_reason"] == "Zakończenie naboru."
    assert archived["archived_by_id"] == 42
    assert archived["version"] == 3
    assert field["enabled"] is False
    assert actions[0]["action"] == "archived"
    assert actions[0]["was_used"] is True
    assert actions[0]["reason"] == "Zakończenie naboru."


def test_unused_training_can_be_physically_deleted():
    old = {
        "id": "unused",
        "name": "Nieużywane",
        "price": "10.00",
        "active": True,
    }

    definition, actions = TrainingCatalogService().reconcile_definition(
        definition_with_catalog([old]),
        definition_with_catalog([]),
        used_training_ids=set(),
        removal_reasons={"unused": "Błędnie dodane."},
        actor_id=7,
    )

    assert TrainingCatalogService.get_training_field(definition)["catalog"] == []
    assert actions[0]["action"] == "deleted"
    assert actions[0]["was_used"] is False


def test_same_name_added_again_gets_new_id_and_does_not_replace_archive():
    old = {
        "id": "old-id",
        "name": "Excel",
        "price": "100.00",
        "active": True,
        "version": 1,
    }
    new_catalog = parse_training_catalog(
        MultiDict(
            [
                ("training_item_id", ""),
                ("training_item_name", "Excel"),
                ("training_item_price", "200"),
                ("training_item_capacity", "10"),
                ("training_item_description", "Nowa edycja"),
                ("training_item_admin_comment", ""),
                ("training_item_low_seats_comment", ""),
                ("training_item_sort_order", "1"),
                ("training_item_active", "0"),
                ("training_active_present", "1"),
            ]
        )
    )

    definition, _ = TrainingCatalogService().reconcile_definition(
        definition_with_catalog([old]),
        definition_with_catalog(new_catalog),
        used_training_ids={"old-id"},
        removal_reasons={"old-id": "Zastąpione nową edycją."},
    )

    catalog = TrainingCatalogService.get_training_field(definition)["catalog"]
    assert len(catalog) == 2
    archived = next(item for item in catalog if item["id"] == "old-id")
    replacement = next(item for item in catalog if item["id"] != "old-id")
    assert archived["archived"] is True
    assert replacement["name"] == archived["name"] == "Excel"
    assert replacement["id"].startswith("trn_")
    assert replacement["price"] == "200.00"


def test_editing_training_increments_version_but_keeps_stable_id():
    old = {
        "id": "stable",
        "name": "Excel",
        "price": "100.00",
        "active": True,
        "version": 2,
    }
    changed = {**old, "price": "150.00"}

    definition, _ = TrainingCatalogService().reconcile_definition(
        definition_with_catalog([old]),
        definition_with_catalog([changed]),
        used_training_ids={"stable"},
    )

    training = TrainingCatalogService.get_training_field(definition)["catalog"][0]
    assert training["id"] == "stable"
    assert training["version"] == 3


def test_archived_training_is_history_only_and_not_a_public_choice():
    archived = {
        "id": "archived",
        "name": "Historyczne szkolenie",
        "price": "100.00",
        "active": True,
        "archived": True,
        "archive_reason": "Zakończona edycja.",
    }
    field = training_field(catalog=[archived])

    public_catalog = TrainingCatalogService.get_trainings_for_field(
        field,
        active_only=True,
    )
    history_catalog = TrainingCatalogService.get_trainings_for_field(
        field,
        active_only=False,
    )
    errors = TrainingCatalogService().validate_field(field)

    assert public_catalog == []
    assert history_catalog[0]["id"] == "archived"
    assert history_catalog[0]["active"] is False
    assert history_catalog[0]["archive_reason"] == "Zakończona edycja."
    assert any("co najmniej jedno aktywne szkolenie" in error for error in errors)


def test_deactivation_creates_audit_action_with_reason_and_usage_flag():
    old = {
        "id": "stable",
        "name": "Python",
        "price": "1000.00",
        "active": True,
    }
    deactivated = {**old, "active": False}

    _, actions = TrainingCatalogService().reconcile_definition(
        definition_with_catalog([old]),
        definition_with_catalog([deactivated]),
        used_training_ids={"stable"},
        removal_reasons={"stable": "Edycja zakończona."},
        actor_id=12,
    )

    assert actions == [
        {
            "action": "deactivated",
            "training_id": "stable",
            "training_name": "Python",
            "old_value": old,
            "new_value": {
                **deactivated,
                "version": 1,
                "created_at": actions[0]["created_at"],
            },
            "reason": "Edycja zakończona.",
            "was_used": True,
            "actor_id": 12,
            "created_at": actions[0]["created_at"],
        }
    ]


def test_used_legacy_training_without_id_is_upgraded_and_archived_safely():
    legacy = {
        "name": "Analiza danych",
        "price": "900.00",
        "active": True,
    }

    definition, actions = TrainingCatalogService().reconcile_definition(
        definition_with_catalog([legacy]),
        definition_with_catalog([]),
        used_training_ids={"Analiza danych"},
        removal_reasons={"Analiza danych": "Migracja starego katalogu."},
    )

    archived = TrainingCatalogService.get_training_field(definition)["catalog"][0]
    assert archived["id"] == "Analiza danych"
    assert archived["archived"] is True
    assert actions[0]["action"] == "archived"
    assert actions[0]["was_used"] is True
