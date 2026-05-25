import json


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
                {"id": "excel", "name": "Excel", "price": 1200},
                {"id": "angielski", "name": "Angielski", "price": 1000},
            ]
        ),
    }
    app.testing_storage.csv_rows = [row]

    monkeypatch.setattr(legacy_app, "get_form_definition", lambda slug: form_definition)
    monkeypatch.setattr(legacy_app, "resolve_nextcloud_template_html", lambda path: "<html></html>")
    monkeypatch.setattr(legacy_app, "generate_document_pdf_bytes", lambda **kwargs: b"%PDF-1.4\n")

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
    ]
    assert [item["filename"] for item in agreements] == [
        "Jan_Kowalski-excel-umowa.pdf",
        "Jan_Kowalski-angielski-umowa.pdf",
    ]
    updated = app.testing_storage.csv_rows[0]
    assert updated["agreement_generated"] == "Tak"
    assert len(json.loads(updated["training_agreements"])) == 2


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
