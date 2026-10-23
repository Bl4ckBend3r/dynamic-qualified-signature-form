import pytest

pytest.importorskip("sqlalchemy")

from database import create_engine, create_session_factory
from form_loader import normalize_form_definition
from models import Base, Form, FormField
from services.form_builder_service import (
    FormBuilderError,
    apply_builder_state,
    serialize_builder_fields,
)


FIELD_TYPES = {"text", "textarea", "email", "tel", "number", "date", "select", "radio", "checkbox", "pesel", "file"}


def builder_form(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'form_builder.db'}"
    Base.metadata.create_all(create_engine(database_url))
    session_factory = create_session_factory(database_url)
    with session_factory() as db:
        form = Form(
            slug="builder",
            name="Builder",
            title="Builder",
            definition_json={
                "title": "Builder",
                "fields": [
                    {
                        "name": "imie",
                        "label": "Imię",
                        "type": "text",
                        "width": "full",
                        "placeholder": "Wpisz imię",
                        "visible_if": {"field": "status", "operator": "equals", "value": "ok"},
                        "custom_extension": {"keep": True},
                    },
                    {
                        "name": "zgoda",
                        "label": "Zgoda",
                        "type": "checkbox",
                        "options": [{"value": "tak", "label": "Tak"}],
                        "pdf_text": "Treść do dokumentu",
                    },
                ],
                "document_field_labels": {"imie": "Imię uczestnika"},
                "workflow": {"initial_step": "submission", "steps": [{"id": "submission"}, {"id": "review"}]},
            },
        )
        db.add(form)
        db.flush()
        db.add_all(
            [
                FormField(
                    form_id=form.id, name="imie", label="Imię", type="text", required=True, sort_order=0,
                    availability_json=[{"step": "submission", "visible": True, "editable": True, "required": True}, {"step": "review", "visible": True, "editable": False, "required": False}],
                ),
                FormField(form_id=form.id, name="zgoda", label="Zgoda", type="checkbox", required=False, options=[{"value": "tak", "label": "Tak"}], sort_order=1),
            ]
        )
        db.commit()
        return session_factory, form.id


def test_existing_form_opens_with_default_and_saved_widths(tmp_path):
    factory, form_id = builder_form(tmp_path)
    with factory() as db:
        form = db.get(Form, form_id)
        fields = sorted(form.fields, key=lambda item: item.sort_order)
        state = serialize_builder_fields(form, fields)
        assert [item["name"] for item in state] == ["imie", "zgoda"]
        assert state[0]["width_span"] == 12
        assert state[1]["width"] == "full"


def test_builder_serialization_excludes_specialized_training_selection(tmp_path):
    factory, form_id = builder_form(tmp_path)
    with factory() as db:
        form = db.get(Form, form_id)
        training_field = FormField(
            form_id=form.id,
            name="selected_trainings",
            label="Wybierz szkolenia",
            type="training_selection",
            required=False,
            sort_order=99,
            active=True,
        )
        db.add(training_field)
        db.flush()

        state = serialize_builder_fields(form, [*form.fields, training_field])
        assert "selected_trainings" not in {field["name"] for field in state}


def test_builder_round_trip_add_delete_change_type_label_order_width_and_preserve_extensions(tmp_path):
    factory, form_id = builder_form(tmp_path)
    with factory() as db:
        form = db.get(Form, form_id)
        imie, zgoda = sorted(form.fields, key=lambda item: item.sort_order)
        state = [
            {
                "id": zgoda.id, "name": "zgoda", "label": "Wybierz zgodę", "type": "radio",
                "required": True, "width": "half", "placeholder": "", "section": "Dane kandydata",
                "document_label": "", "options": ["Tak", "Nie"],
            },
            {
                "id": imie.id, "name": "imie", "label": "Imię kandydata", "type": "text",
                "required": False, "width": "half", "placeholder": "Podaj imię", "section": "Dane kandydata",
                "document_label": "Imię w umowie", "options": [],
            },
            {
                "id": None, "name": "email", "label": "Adres e-mail", "type": "email",
                "required": True, "width": "full", "placeholder": "name@example.org", "section": "Kontakt",
                "document_label": "", "options": [],
            },
        ]
        apply_builder_state(db, form, state, field_types=FIELD_TYPES, availability_definition=form.definition_json)
        db.commit()
        db.refresh(form)

        active = sorted((field for field in form.fields if field.active), key=lambda item: item.sort_order)
        assert [field.name for field in active] == ["zgoda", "imie", "email"]
        assert active[0].type == "radio"
        assert active[0].label == "Wybierz zgodę"
        assert active[2].type == "email"
        configs = {item["name"]: item for item in form.definition_json["fields"]}
        assert configs["zgoda"]["width"] == configs["imie"]["width"] == "half"
        assert configs["imie"]["placeholder"] == "Podaj imię"
        assert configs["imie"]["visible_if"] == {"field": "status", "operator": "equals", "value": "ok"}
        assert configs["imie"]["custom_extension"] == {"keep": True}
        assert configs["zgoda"]["pdf_text"] == "Treść do dokumentu"
        assert form.definition_json["document_field_labels"] == {"imie": "Imię w umowie"}
        review_permission = next(item for item in active[1].availability_json if item["step"] == "review")
        assert review_permission == {"step": "review", "visible": True, "editable": False, "required": False}


def test_two_fields_can_share_row_and_reopen_after_save(tmp_path):
    factory, form_id = builder_form(tmp_path)
    with factory() as db:
        form = db.get(Form, form_id)
        fields = sorted(form.fields, key=lambda item: item.sort_order)
        state = serialize_builder_fields(form, fields)
        for item in state:
            item["width"] = "half"
            item["width_span"] = 6
        apply_builder_state(db, form, state, field_types=FIELD_TYPES, availability_definition=form.definition_json)
        db.commit()
    with factory() as db:
        form = db.get(Form, form_id)
        reopened = serialize_builder_fields(form, sorted(form.fields, key=lambda item: item.sort_order))
        assert [item["width_span"] for item in reopened if item["id"]] == [6, 6]


def test_builder_persists_workflow_availability_and_declaration_assignment(tmp_path):
    factory, form_id = builder_form(tmp_path)
    with factory() as db:
        form = db.get(Form, form_id)
        fields = sorted(form.fields, key=lambda item: item.sort_order)
        state = serialize_builder_fields(form, fields)
        target = next(item for item in state if item["name"] == "imie")
        target["availability"] = [
            {"step": "submission", "visible": False, "editable": False, "required": False},
            {"step": "review", "visible": True, "editable": True, "required": True},
        ]
        target["document_usage"] = {"declaration": True}

        apply_builder_state(
            db,
            form,
            state,
            field_types=FIELD_TYPES,
            availability_definition=form.definition_json,
        )
        db.commit()
        db.refresh(form)

        saved = next(item for item in form.definition_json["fields"] if item.get("name") == "imie")
        assert saved["availability"] == target["availability"]
        assert saved["document_usage"] == {"declaration": True}
        field = next(item for item in form.fields if item.name == "imie")
        assert field.required is False


def test_omitting_field_marks_it_inactive_without_deleting_history(tmp_path):
    factory, form_id = builder_form(tmp_path)
    with factory() as db:
        form = db.get(Form, form_id)
        imie = next(field for field in form.fields if field.name == "imie")
        state = serialize_builder_fields(form, [imie])
        apply_builder_state(db, form, state, field_types=FIELD_TYPES, availability_definition=form.definition_json)
        db.commit()
        zgoda = next(field for field in form.fields if field.name == "zgoda")
        assert zgoda.active is False
        assert zgoda.id is not None


def test_builder_deactivates_duplicates_missing_from_state_with_cached_relationship(tmp_path):
    factory, form_id = builder_form(tmp_path)
    with factory() as db:
        form = db.get(Form, form_id)
        cached_fields = list(form.fields)
        imie = next(field for field in cached_fields if field.name == "imie")
        duplicate = FormField(
            form_id=form.id,
            name="zgoda",
            label="Historyczny duplikat zgody",
            type="checkbox",
            required=False,
            sort_order=99,
            active=True,
        )
        db.add(duplicate)
        db.flush()
        assert duplicate not in form.fields

        state = serialize_builder_fields(form, [imie])
        apply_builder_state(
            db,
            form,
            state,
            field_types=FIELD_TYPES,
            availability_definition=form.definition_json,
        )
        db.flush()

        zgoda_rows = db.query(FormField).filter_by(
            form_id=form.id,
            name="zgoda",
        ).all()
        assert len(zgoda_rows) == 2
        assert all(not field.active for field in zgoda_rows)


def test_builder_rejects_name_change_for_existing_field(tmp_path):
    factory, form_id = builder_form(tmp_path)
    with factory() as db:
        form = db.get(Form, form_id)
        field = form.fields[0]
        state = serialize_builder_fields(form, [field])
        state[0]["name"] = "nowa_nazwa"
        with pytest.raises(FormBuilderError, match="nie można zmienić"):
            apply_builder_state(db, form, state, field_types=FIELD_TYPES, availability_definition=form.definition_json)


def test_numeric_legacy_widths_normalize_to_supported_grid_values():
    definition = normalize_form_definition({"title": "Test", "fields": [{"name": "a", "type": "text", "width": 8}, {"name": "b", "type": "text", "width": 9}]})
    assert [field["width"] for field in definition["fields"]] == ["two-thirds", "three-quarters"]
