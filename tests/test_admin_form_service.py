import json

import pytest

pytest.importorskip("sqlalchemy")

from database import create_engine, create_session_factory
from form_loader import FIELD_STAGE_INITIAL
from models import Base, Form
from services.admin_form_service import (
    build_definition_from_html,
    build_form_definition_from_admin_form,
    detect_form_fields,
    normalize_field_stage,
    parse_uploaded_form_definition,
    sync_form_fields,
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
