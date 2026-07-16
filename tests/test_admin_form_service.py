import json
import zipfile
from io import BytesIO

import pytest
from werkzeug.datastructures import MultiDict

pytest.importorskip("sqlalchemy")

from database import create_engine, create_session_factory
from form_loader import FIELD_STAGE_INITIAL
from models import Base, Form
from services.admin_form_service import (
    build_definition_from_docx,
    build_definition_from_html,
    build_form_definition_from_admin_form,
    detect_form_fields,
    form_has_additional_fields,
    normalize_admin_form_definition,
    normalize_field_stage,
    parse_training_dates_from_form,
    parse_workflow_json,
    parse_training_dates_text,
    parse_uploaded_form_definition,
    sync_form_fields,
    validate_admin_form_config,
)


def test_build_definition_from_html_detects_basic_fields():
    definition = build_definition_from_html(
        '<label>Imię</label><input name="imie" required><textarea name="opis"></textarea>',
        "formularz.html",
    )

    assert definition["title"] == "formularz"
    assert [field["name"] for field in definition["fields"]] == ["imie", "opis"]
    assert definition["fields"][0]["required"] is True


def test_parse_uploaded_json_definition():
    definition = parse_uploaded_form_definition(json.dumps({"title": "Test", "fields": []}).encode(), "test.json")

    assert definition["title"] == "Test"


def test_build_definition_from_docx_detects_template_placeholders():
    content = BytesIO()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>{{ imiona }}</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("word/document.xml", xml)

    definition = build_definition_from_docx(content.getvalue(), "formularz.docx")

    assert definition["title"] == "formularz"
    assert definition["fields"][0]["name"] == "imiona"


def test_normalize_and_validate_admin_form_config():
    definition = normalize_admin_form_definition(
        {
            "title": "Form",
            "fields": [{"type": "text", "name": "imie", "label": "ImiÄ™"}],
        }
    )

    assert definition["fields"][0]["stage"] == FIELD_STAGE_INITIAL
    assert validate_admin_form_config(definition) == []


def test_validate_admin_form_config_reports_invalid_definition():
    errors = validate_admin_form_config({"title": "Broken", "fields": [{"type": "text"}]})

    assert errors


def test_parse_workflow_json_requires_object():
    assert parse_workflow_json("", {"initial_step": "submission"}) == {"initial_step": "submission"}
    with pytest.raises(ValueError):
        parse_workflow_json("[]", {})


def test_detect_form_fields_includes_document_fields():
    fields = detect_form_fields(
        {
            "fields": [{"type": "text", "name": "main"}],
            "documents": {"declaration": {"fields": [{"type": "text", "name": "document_field"}]}},
        }
    )

    assert [field["name"] for field in fields] == ["main", "document_field"]


def test_normalize_field_stage_falls_back_to_initial():
    assert normalize_field_stage("after_officer_acceptance") == "after_officer_acceptance"
    assert normalize_field_stage("unknown") == FIELD_STAGE_INITIAL


def test_build_form_definition_from_admin_form_updates_workflow():
    form_data = {
        "workflow_json": '{"steps": []}',
        "workflow_name": "Nowy workflow",
        "workflow_initial_step": "submitted",
        "requires_declaration": "on",
        "declaration_template_html": "<p>Deklaracja</p>",
    }

    definition = build_form_definition_from_admin_form({"title": "Form", "fields": []}, form_data)

    assert definition["workflow"]["name"] == "Nowy workflow"
    assert definition["workflow"]["initial_step"] == "submitted"
    assert definition["workflow"]["requires_declaration"] is True
    assert definition["workflow"]["declaration_template_html"] == "<p>Deklaracja</p>"


def test_admin_contract_settings_create_generated_agreement_config():
    definition = build_form_definition_from_admin_form(
        {"title": "Form", "fields": []},
        {
            "workflow_json": '{"steps": []}',
            "workflow_name": "Umowy",
            "workflow_initial_step": "",
            "requires_contract": "on",
            "contract_template_html": "<main>Umowa {{ agreement_number }}</main>",
            "contract_generation_mode": "single",
            "contract_filename_pattern": "{first_name}_{last_name}-contract.pdf",
            "contract_number_pattern": "U/{submission_id}/{generated_date}",
        },
    )

    agreement = next(item for item in definition["documents"] if item["id"] == "agreement")
    assert definition["workflow"]["managed_documents"] is True
    assert agreement["enabled"] is True
    assert agreement["template_html"] == "<main>Umowa {{ agreement_number }}</main>"
    assert agreement["generation_mode"] == "single"
    assert agreement["filename_pattern"] == "{first_name}_{last_name}-contract.pdf"
    assert agreement["numbering"]["number_pattern"] == "U/{submission_id}/{generated_date}"


def test_parse_training_dates_text_validates_and_sorts_dates():
    dates = parse_training_dates_text(
        "2026-09-10||10:00|12:00|Sala 2|Opis\n2026-08-01||||Sala 1|"
    )

    assert [date["start_date"] for date in dates] == ["2026-08-01", "2026-09-10"]
    assert dates[1]["location"] == "Sala 2"


def test_parse_training_dates_text_rejects_invalid_date_range():
    with pytest.raises(ValueError, match="Data zakończenia"):
        parse_training_dates_text("2026-09-10|2026-09-01||||")


def test_parse_training_dates_from_readable_form_fields_groups_rows_by_training():
    form_data = MultiDict(
        [
            ("training_date_training_index", "1"),
            ("training_date_start_date", "2026-09-10"),
            ("training_date_end_date", "2026-09-11"),
            ("training_date_start_time", "09:00"),
            ("training_date_end_time", "15:00"),
            ("training_date_location", "Zielona Góra"),
            ("training_date_description", "Warsztat"),
            ("training_date_training_index", "0"),
            ("training_date_start_date", "2026-08-01"),
            ("training_date_end_date", ""),
            ("training_date_start_time", ""),
            ("training_date_end_time", ""),
            ("training_date_location", "Online"),
            ("training_date_description", ""),
        ]
    )

    dates = parse_training_dates_from_form(form_data, 2)

    assert dates[0][0]["start_date"] == "2026-08-01"
    assert dates[1][0] == {
        "start_date": "2026-09-10",
        "end_date": "2026-09-11",
        "start_time": "09:00",
        "end_time": "15:00",
        "location": "Zielona Góra",
        "description": "Warsztat",
    }


def test_sync_form_fields_keeps_database_shape(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'admin_form_service.db'}"
    Base.metadata.create_all(create_engine(database_url))
    session_factory = create_session_factory(database_url)

    with session_factory() as db:
        form = Form(slug="test", name="Test", title="Test", definition_json={"fields": []})
        db.add(form)
        db.flush()

        sync_form_fields(
            db,
            form,
            {
                "fields": [
                    {"type": "section", "label": "Sekcja"},
                    {"type": "text", "name": "imie", "label": "Imię", "required": True},
                ]
            },
        )
        db.commit()
        db.refresh(form)

        assert len(form.fields) == 1
        assert form.fields[0].name == "imie"
        assert form.fields[0].section == "Sekcja"
        assert form.fields[0].active is True


def test_form_has_additional_fields_detects_after_acceptance_stage():
    form = Form(
        slug="test",
        name="Test",
        title="Test",
        definition_json={
            "fields": [
                {"type": "text", "name": "initial", "stage": "initial_submission"},
                {"type": "text", "name": "after", "stage": "after_officer_acceptance"},
            ]
        },
    )

    assert form_has_additional_fields(form)
