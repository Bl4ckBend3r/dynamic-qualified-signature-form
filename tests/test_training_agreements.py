import json
from decimal import Decimal

from flask import Flask

from services.agreement_context_service import upgrade_training_agreement_total_placeholder
from services.training_service import format_price_pln


def test_extract_training_selection_validates_limit():
    from werkzeug.datastructures import MultiDict

    from legacy_app import extract_training_selection

    field = {
        "name": "selected_trainings",
        "required": True,
        "max_total_amount": 2000,
        "currency": "PLN",
        "catalog": [
            {"id": "excel", "name": "Excel", "price": 1200},
            {"id": "angielski", "name": "Angielski", "price": 1000},
        ],
    }

    trainings, error = extract_training_selection(
        field,
        MultiDict([("selected_trainings", "excel"), ("selected_trainings", "angielski")]),
    )

    assert [item["id"] for item in trainings] == ["excel", "angielski"]
    assert "przekracza limit" in error


def test_build_training_agreement_number():
    from legacy_app import build_training_agreement_number

    number = build_training_agreement_number(
        "abc",
        2,
        "2026-05-25",
        {"numbering": {"number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"}},
    )

    assert number == "abc/2/2026-05-25"


def test_generate_training_agreements_creates_one_agreement_per_training(app, monkeypatch):
    import legacy_app

    form_definition = {
        "title": "Form",
        "fields": [],
        "process": {
            "documents": {
                "training_agreement": {
                    "enabled": True,
                    "template": "Template/umowa.html",
                    "filename_pattern": "{first_name}_{last_name}-{training_id}-umowa.pdf",
                    "numbering": {
                        "number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"
                    },
                }
            }
        },
    }
    row = {
        "submission_id": "abc",
        "form_slug": "sample_form",
        "imiona": "Jan",
        "nazwisko": "Kowalski",
        "selected_trainings": json.dumps(
            [
                {"id": "digital-a", "name": "Kompetencje cyfrowe", "price": 1000},
                {"id": "personal", "name": "Kompetencje osobiste", "price": 750},
                {"id": "digital-b", "name": "Kompetencje cyfrowe", "price": 1000},
            ]
        ),
    }
    app.testing_storage.csv_rows = [row]

    monkeypatch.setattr(legacy_app, "get_form_definition", lambda slug: form_definition)
    monkeypatch.setattr(
        legacy_app,
        "resolve_nextcloud_template_html",
        lambda path: (
            "<table>{% for item in selected_trainings %}<tr><td>{{ item.name }}</td>"
            "<td>{{ item.price_formatted }}</td></tr>{% endfor %}</table>"
            "<strong>{{ selected_trainings_total_formatted }}</strong>"
        ),
    )
    rendered_contexts = []
    rendered_templates = []

    def capture_pdf_context(**kwargs):
        rendered_contexts.append(kwargs["context"])
        rendered_templates.append(kwargs["template_html"])
        return b"%PDF-1.4\n"

    monkeypatch.setattr(legacy_app, "generate_document_pdf_bytes", capture_pdf_context)

    agreements = legacy_app.generate_training_agreements_for_submission(
        {
            "submission_id": "abc",
            "form_slug": "sample_form",
            "form_title": "Form",
            "row": row,
        },
        "2026-05-25",
    )

    assert [item["number"] for item in agreements] == [
        "abc/1/2026-05-25",
        "abc/2/2026-05-25",
        "abc/3/2026-05-25",
    ]
    assert [item["filename"] for item in agreements] == [
        "Jan_Kowalski-digital-a-umowa.pdf",
        "Jan_Kowalski-personal-umowa.pdf",
        "Jan_Kowalski-digital-b-umowa.pdf",
    ]
    updated = app.testing_storage.csv_rows[0]
    assert updated["agreement_generated"] == "Tak"
    assert len(json.loads(updated["training_agreements"])) == 3
    assert len(rendered_contexts) == 3
    for index, context in enumerate(rendered_contexts):
        assert context["selected_trainings"] == [context["training"]]
        assert context["selected_trainings_normalized"] == [context["training"]]
        assert context["submission"]["selected_trainings"] == [context["training"]]
        assert len(context["training_agreements"]) == 1
        assert context["agreement_sequence"] == index + 1
        assert context["training_agreement"] == context["agreement"]
        assert context["selected_trainings_total_formatted"] == context["training"]["price_formatted"]
        assert context["agreement_training_price"] == Decimal(str(context["training"]["price"]))
        assert context["agreement_training_price_formatted"] == context["training"]["price_formatted"]
        assert len(context["all_selected_trainings"]) == 3
        assert context["all_selected_trainings_total"] == Decimal("2750")
        assert context["all_selected_trainings_total_formatted"] == format_price_pln(Decimal("2750"))
        rendered = app.jinja_env.from_string(rendered_templates[index]).render(**context)
        assert rendered.count("<tr>") == 1
        assert context["training"]["name"] in rendered
        assert context["training"]["price_formatted"] in rendered
        assert context["all_selected_trainings_total_formatted"] in rendered


def test_legacy_training_total_placeholder_uses_full_total_with_backward_fallback():
    app = Flask(__name__)
    upgraded = upgrade_training_agreement_total_placeholder(
        "<strong>{{ selected_trainings_total_formatted }}</strong>"
    )
    template = app.jinja_env.from_string(upgraded)

    assert template.render(
        all_selected_trainings_total_formatted="2 750,00 zł",
        selected_trainings_total_formatted="1 000,00 zł",
    ) == "<strong>2 750,00 zł</strong>"
    assert template.render(
        selected_trainings_total_formatted="1 000,00 zł",
    ) == "<strong>1 000,00 zł</strong>"


def test_force_regenerates_existing_declaration(app, monkeypatch):
    import legacy_app

    calls = []
    form_definition = {
        "title": "Form",
        "fields": [],
        "process": {
            "documents": {
                "declaration": {
                    "enabled": True,
                    "template": "Template/deklaracja.html",
                    "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
                }
            }
        },
    }
    row = {
        "submission_id": "abc",
        "form_slug": "sample_form",
        "imiona": "Jan",
        "nazwisko": "Kowalski",
        "declaration_generated": "Tak",
        "declaration_filename": "Jan_Kowalski-deklaracja.pdf",
        "deklaracja_18_lat": "Nie",
    }
    app.testing_storage.csv_rows = [row]
    app.testing_storage.saved_pdfs["output/sample_form/pdf/Jan_Kowalski-deklaracja.pdf"] = b"old pdf"

    monkeypatch.setattr(legacy_app, "get_form_definition", lambda slug: form_definition)
    monkeypatch.setattr(legacy_app, "resolve_nextcloud_template_html", lambda path: "<html></html>")

    def fake_generate_document_pdf_bytes(**kwargs):
        calls.append(kwargs["context"]["submission"]["deklaracja_18_lat"])
        return b"new pdf"

    monkeypatch.setattr(legacy_app, "generate_document_pdf_bytes", fake_generate_document_pdf_bytes)

    result = legacy_app.ensure_declaration_generated(
        {
            "submission_id": "abc",
            "form_slug": "sample_form",
            "form_title": "Form",
            "row": row,
        },
        force=True,
    )

    assert result["created"] is True
    assert calls == ["Nie"]
