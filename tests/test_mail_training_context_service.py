from types import SimpleNamespace

from services.mail_training_context_service import (
    NO_AVAILABLE_TRAININGS,
    build_training_mail_context,
    find_training_field,
)
from services.training_availability_service import TrainingAvailabilityService


class FakeRepository:
    def __init__(self, rows=None):
        self.rows = rows or []

    def list_by_form(self, form_slug):
        return list(self.rows)


def training_form(catalog):
    return SimpleNamespace(
        slug="training-form",
        definition_json={
            "documents": [
                {
                    "id": "declaration",
                    "fields": [
                        {
                            "type": "training_selection",
                            "name": "selected_trainings",
                            "currency": "PLN",
                            "catalog": catalog,
                        }
                    ],
                }
            ]
        },
    )


def test_available_trainings_context_for_form_without_catalog_has_clear_message():
    context = build_training_mail_context(SimpleNamespace(slug="plain", definition_json={"fields": []}))

    assert context["available_trainings"] == f'<p style="margin:0;">{NO_AVAILABLE_TRAININGS}</p>'
    assert context["available_trainings_table"] == context["available_trainings"]
    assert context["available_trainings_list"] == context["available_trainings"]
    assert context["available_trainings_text"] == NO_AVAILABLE_TRAININGS
    assert "<table" not in context["available_trainings"]


def test_available_trainings_table_list_and_text_render_one_training_with_all_details():
    form = training_form(
        [
            {
                "id": "excel",
                "name": "Excel zaawansowany",
                "description": "Arkusze i raporty",
                "price": "1234.5",
                "capacity": 5,
                "low_seats_comment": "Ostatnie miejsca.",
                "dates": [
                    {
                        "start_date": "2026-08-01",
                        "start_time": "09:00",
                        "end_time": "12:00",
                        "location": "Zielona Góra",
                    }
                ],
            }
        ]
    )
    service = TrainingAvailabilityService(
        FakeRepository(
            [
                {
                    "submission_id": "occupied",
                    "process_status": "FORM_SUBMITTED",
                    "selected_trainings": '[{"id":"excel","name":"Excel zaawansowany"}]',
                }
            ]
        )
    )

    context = build_training_mail_context(form, availability_service=service)

    assert find_training_field(form)["name"] == "selected_trainings"
    assert context["available_trainings"] == context["available_trainings_table"]
    for rendered in (context["available_trainings_table"], context["available_trainings_list"]):
        assert "Excel zaawansowany" in rendered
        assert "1 234,50 zł" in rendered
        assert "Arkusze i raporty" in rendered
        assert "Zielona Góra" in rendered
        assert "Ostatnie miejsca." in rendered
        assert "01.08.2026, 09:00–12:00" in rendered
        assert "Dostępne" in rendered
    assert "Limit: 5" in context["available_trainings_table"]
    assert "Zajęte: 0" in context["available_trainings_table"]
    assert "Dostępne: 5" in context["available_trainings_table"]
    assert "Dostępne miejsca: 5" in context["available_trainings_text"]
    assert "Terminy: 01.08.2026, 09:00–12:00" in context["available_trainings_text"]


def test_available_trainings_support_multiple_items_and_escape_configuration_html():
    form = training_form(
        [
            {
                "id": "unsafe",
                "name": '<script>alert("x")</script>',
                "description": "<b>Nieufny opis</b>",
                "price": "1000",
                "capacity": 1,
            },
            {"id": "python", "name": "Python", "price": "2000", "capacity": 3},
        ]
    )

    context = build_training_mail_context(form)

    assert context["available_trainings_table"].count("<tr>") == 3
    assert context["available_trainings_list"].count("<li") == 2
    assert '<script>alert("x")</script>' not in context["available_trainings_table"]
    assert "&lt;script&gt;alert" in context["available_trainings_table"]
    assert "<b>Nieufny opis</b>" not in context["available_trainings_list"]
    assert "&lt;b&gt;Nieufny opis&lt;/b&gt;" in context["available_trainings_list"]
    assert "1 000,00 zł" in context["available_trainings_text"]
    assert "2. Python" in context["available_trainings_text"]


def test_selected_training_formats_use_submission_snapshot_and_catalog_availability():
    form = training_form(
        [{"id": "excel", "name": "Excel", "price": "1500", "capacity": 2}]
    )
    submission = SimpleNamespace(
        selected_trainings='[{"id":"excel","name":"Excel","price":"1500"}]'
    )

    context = build_training_mail_context(form, submission)

    assert "Excel" in context["selected_trainings_table"]
    assert "Excel" in context["selected_trainings_list"]
    assert context["selected_trainings_text"].startswith("Wybrane szkolenia:")
    assert context["selected_trainings_count"] == 1
    assert context["trainings_total_price"] == "1 500,00 zł"
