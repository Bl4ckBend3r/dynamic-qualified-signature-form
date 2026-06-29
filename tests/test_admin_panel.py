import io
import json
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from flask import url_for
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from conftest import InMemoryStorage
from config import Config
from database import create_session_factory
from models import (
    EmailLog,
    Form,
    FormField,
    FormPermission,
    FormSubmission,
    Logo,
    MailFooter,
    MailTemplate,
    MailTemplateAsset,
    SubmissionDecision,
    SubmissionFile,
    SubmissionWorkflowEvent,
    User,
)


class AdminTestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SERVER_NAME = "localhost"
    NEXTCLOUD_BASE_URL = "https://nextcloud.test"
    NEXTCLOUD_USERNAME = "tester"
    NEXTCLOUD_APP_PASSWORD = "secret"
    NEXTCLOUD_FORMS_DIR = "Formularze"
    NEXTCLOUD_OUTPUT_DIR = "output"
    SMTP_HOST = "smtp.test"
    SMTP_USER = "user"
    SMTP_PASSWORD = "secret"
    MAIL_FROM = "noreply@example.com"
    AUTO_CREATE_DB_SCHEMA = True


@pytest.fixture()
def admin_app(tmp_path, form_definition):
    import app as app_module

    class TestConfig(AdminTestConfig):
        TEMP_DIR = tmp_path / "tmp"
        DATABASE_URL = f"sqlite:///{tmp_path / 'admin.db'}"

    flask_app = app_module.create_app(config_object=TestConfig, storage_override=InMemoryStorage(form_definition))
    flask_app.config.update(TESTING=True, TEMP_DIR=tmp_path / "tmp")
    Path(flask_app.config["TEMP_DIR"]).mkdir(parents=True, exist_ok=True)
    yield flask_app


@pytest.fixture()
def admin_client(admin_app):
    return admin_app.test_client()


def create_user(app, email="admin@example.com", password="secret", role="super_admin"):
    session_factory = create_session_factory(app.config["DATABASE_URL"])
    with session_factory() as db:
        user = User(
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            is_active=True,
            is_blocked=False,
        )
        db.add(user)
        db.commit()
        return user.id


def create_form(app, slug="sample_form", name="Sample", user_id=None, **kwargs):
    session_factory = create_session_factory(app.config["DATABASE_URL"])
    with session_factory() as db:
        form = Form(
            slug=slug,
            name=name,
            title=kwargs.pop("title", name),
            definition_json=kwargs.pop("definition_json", {"title": name, "fields": []}),
            created_by_id=user_id,
            **kwargs,
        )
        db.add(form)
        db.flush()
        if user_id:
            db.add(FormPermission(user_id=user_id, form_id=form.id, can_manage=True))
        db.commit()
        return form.id


def login(client, email="admin@example.com", password="secret"):
    response = client.get("/admin/")
    html = response.get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    return client.post("/admin/", data={"email": email, "password": password, "csrf_token": token})


def test_admin_requires_login(admin_client):
    response = admin_client.get("/admin/dashboard")

    assert response.status_code == 302
    assert "/admin/" in response.location


def test_admin_login(admin_app, admin_client):
    create_user(admin_app)

    response = login(admin_client)

    assert response.status_code == 302
    assert response.location.endswith("/admin/dashboard")


def test_super_admin_sees_all_forms(admin_app, admin_client):
    create_user(admin_app)
    create_form(admin_app, slug="one", name="One")
    create_form(admin_app, slug="two", name="Two")
    login(admin_client)

    response = admin_client.get("/admin/forms")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "One" in html
    assert "Two" in html


def test_form_manager_sees_only_assigned_forms(admin_app, admin_client):
    manager_id = create_user(admin_app, email="manager@example.com", role="form_manager")
    create_form(admin_app, slug="owned", name="Owned", user_id=manager_id)
    create_form(admin_app, slug="hidden", name="Hidden")
    login(admin_client, email="manager@example.com")

    response = admin_client.get("/admin/forms")
    html = response.get_data(as_text=True)

    assert "Owned" in html
    assert "Hidden" not in html


def test_delete_button_visible_only_for_super_admin(admin_app, admin_client):
    create_user(admin_app)
    create_form(admin_app, slug="one", name="One")
    login(admin_client)

    response = admin_client.get("/admin/forms")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Usuń" in html
    assert "/admin/forms/1/delete" in html


def test_delete_button_hidden_for_regular_admin(admin_app, admin_client):
    admin_id = create_user(admin_app, email="regular@example.com", role="admin")
    create_form(admin_app, slug="owned", name="Owned", user_id=admin_id)
    login(admin_client, email="regular@example.com")

    response = admin_client.get("/admin/forms")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Usuń" not in html


def test_regular_admin_cannot_delete_form(admin_app, admin_client):
    admin_id = create_user(admin_app, email="regular@example.com", role="admin")
    form_id = create_form(admin_app, slug="owned", name="Owned", user_id=admin_id)
    login(admin_client, email="regular@example.com")
    token = admin_client.get("/admin/forms").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(f"/admin/forms/{form_id}/delete", data={"csrf_token": token})

    assert response.status_code == 403


def test_workflow_status_tile_has_no_border():
    stylesheet = Path("static/documents_to_sign.css").read_text(encoding="utf-8")
    status_block = stylesheet.split(".status-tile {", 1)[1].split("}", 1)[0]

    assert "border: 0;" in status_block
    assert "border: 1px solid var(--border)" not in status_block

def test_super_admin_delete_removes_form_from_database(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="delete_me", name="Delete me", is_active=True, is_public=True)
    login(admin_client)
    token = admin_client.get("/admin/forms").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(f"/admin/forms/{form_id}/delete", data={"csrf_token": token})

    assert response.status_code == 302
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        assert db.get(Form, form_id) is None
    list_html = admin_client.get("/admin/forms").get_data(as_text=True)
    assert "Delete me" not in list_html
    assert admin_client.get("/form/delete_me").status_code == 404


def test_super_admin_delete_blocks_form_with_submissions(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="with_submission", name="With submission")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.add(FormSubmission(submission_id="abc", form_slug="with_submission", form_name="With submission"))
        db.commit()
    login(admin_client)
    token = admin_client.get("/admin/forms").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(f"/admin/forms/{form_id}/delete", data={"csrf_token": token})

    assert response.status_code == 302
    with session_factory() as db:
        assert db.get(Form, form_id) is not None


def test_logo_upload_rejects_invalid_image_content(admin_app, admin_client):
    create_user(admin_app)
    login(admin_client)
    token = admin_client.get("/admin/logos").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        "/admin/logos",
        data={
            "csrf_token": token,
            "name": "Bad logo",
            "logo_file": (io.BytesIO(b"not an image"), "logo.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        assert db.query(Logo).count() == 0


def test_public_forms_use_database_visibility_and_label(admin_app, admin_client):
    create_form(admin_app, slug="public_form", name="Public Form", label_text="PROJEKT 6.08", sort_order=1)
    create_form(admin_app, slug="hidden_form", name="Hidden Form", is_public=False, sort_order=2)
    create_form(admin_app, slug="inactive_form", name="Inactive Form", is_active=False, sort_order=3)

    response = admin_client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Public Form" in html
    assert "PROJEKT 6.08" in html
    assert "Hidden Form" not in html
    assert "Inactive Form" not in html


def test_public_form_page_uses_active_database_fields(admin_app, admin_client):
    form_id = create_form(admin_app, slug="db_form", name="Database Form")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.add_all(
            [
                FormField(form_id=form_id, name="email", label="E-mail", type="email", active=True),
                FormField(form_id=form_id, name="archived", label="Archived", type="text", active=False),
            ]
        )
        db.commit()

    response = admin_client.get("/form/db_form")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="email"' in html
    assert 'name="archived"' not in html


def test_upload_form_detects_fields(admin_app, admin_client):
    create_user(admin_app)
    login(admin_client)
    payload = {
        "title": "Uploaded",
        "fields": [
            {"type": "section", "label": "Dane"},
            {"type": "text", "name": "imiona", "label": "Imiona", "required": True},
            {"type": "select", "name": "status", "label": "Status", "options": ["A", "B"]},
        ],
    }
    token = admin_client.get("/admin/forms/upload").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        "/admin/forms/upload",
        data={"csrf_token": token, "slug": "uploaded", "form_file": (io.BytesIO(json.dumps(payload).encode()), "uploaded.json")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        form = db.query(Form).filter_by(slug="uploaded").one()
        assert [field.name for field in form.fields] == ["imiona", "status"]


def test_upload_form_preserves_polish_characters_and_workflow(admin_app, admin_client):
    create_user(admin_app)
    login(admin_client)
    payload = {
        "title": "Zażółć gęślą jaźń",
        "fields": [{"type": "text", "name": "imiona", "label": "Imię i nazwisko"}],
        "workflow": {
            "name": "Ścieżka",
            "initial_step": "submission",
            "steps": [{"id": "submission", "type": "end", "triggers": ["application_submitted"]}],
        },
    }
    token = admin_client.get("/admin/forms/upload").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        "/admin/forms/upload",
        data={
            "csrf_token": token,
            "slug": "polskie",
            "form_file": (io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8")), "polskie.json"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        form = db.query(Form).filter_by(slug="polskie").one()
        assert form.title == "Zażółć gęślą jaźń"
        assert form.definition_json["fields"][0]["label"] == "Imię i nazwisko"
        assert form.definition_json["workflow"]["name"] == "Ścieżka"


def test_upload_html_form_redirects_to_fields(admin_app, admin_client):
    create_user(admin_app)
    login(admin_client)
    html_form = b'<form><input name="email" type="email" required><textarea name="opis"></textarea></form>'
    token = admin_client.get("/admin/forms/upload").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        "/admin/forms/upload",
        data={
            "csrf_token": token,
            "slug": "html_form",
            "name": "HTML Form",
            "form_file": (io.BytesIO(html_form), "html_form.html"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert "/fields" in response.location
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        form = db.query(Form).filter_by(slug="html_form").one()
        assert [field.name for field in form.fields] == ["email", "opis"]


def test_workflow_can_be_edited_after_json_import(admin_app, admin_client):
    create_user(admin_app)
    definition = {
        "title": "Workflow Form",
        "fields": [{"type": "text", "name": "first_name", "label": "Imię"}],
        "workflow": {
            "name": "Import",
            "initial_step": "submission",
            "steps": [
                {"id": "submission", "type": "form_submit", "next": "completed", "triggers": ["application_submitted"]},
                {"id": "completed", "type": "end"},
            ],
        },
    }
    form_id = create_form(admin_app, slug="workflow_form", name="Workflow Form", definition_json=definition)
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/edit").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/edit",
        data={
            "csrf_token": token,
            "name": "Workflow Form",
            "slug": "workflow_form",
            "title": "Workflow Form",
            "sort_order": "0",
            "workflow_name": "Po imporcie",
            "workflow_initial_step": "submission",
            "requires_declaration": "on",
            "declaration_template_html": "<p>Deklaracja {{ first_name }}</p>",
            "workflow_json": json.dumps(definition["workflow"], ensure_ascii=False),
            "is_active": "on",
            "is_public": "on",
        },
    )

    assert response.status_code == 302
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        form = db.get(Form, form_id)
        workflow = form.definition_json["workflow"]
        assert workflow["name"] == "Po imporcie"
        assert workflow["requires_declaration"] is True
        assert workflow["declaration_template_html"] == "<p>Deklaracja {{ first_name }}</p>"
        declaration = next(document for document in form.definition_json["documents"] if document["id"] == "declaration")
        assert declaration["template_html"] == "<p>Deklaracja {{ first_name }}</p>"


def test_workflow_edit_shows_tooltips_and_conditional_html_fields(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(
        admin_app,
        slug="workflow_form",
        name="Workflow Form",
        definition_json={
            "title": "Workflow Form",
            "fields": [],
            "workflow": {
                "name": "Workflow",
                "initial_step": "submission",
                "requires_contract": True,
                "contract_template_html": "<p>Umowa</p>",
                "steps": [{"id": "submission", "type": "end", "triggers": ["application_submitted"]}],
            },
        },
    )
    login(admin_client)

    response = admin_client.get(f"/admin/forms/{form_id}/edit")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "application_submitted" in html
    assert "Uruchamiany po wysłaniu formularza przez użytkownika." in html
    assert "additional_fields_completed" in html
    assert "Szablon umowy HTML" in html
    assert "Szablon deklaracji HTML" in html
    assert 'data-workflow-template="contract"' in html


def test_admin_submission_list_renders_workflow_status_label(admin_app, admin_client):
    user_id = create_user(admin_app)
    form_id = create_form(
        admin_app,
        slug="status_form",
        name="Status Form",
        user_id=user_id,
        definition_json={
            "title": "Status Form",
            "fields": [],
            "workflow": {
                "initial_step": "submission",
                "statuses": [{"id": "custom_status", "label": "Przyjazny status"}],
                "steps": [{"id": "submission", "type": "end"}],
            },
        },
    )
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.add(FormSubmission(submission_id="abc", form_slug="status_form", form_name="Status Form", process_status="custom_status"))
        db.commit()
    login(admin_client)

    response = admin_client.get(f"/admin/forms/{form_id}/submissions")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Przyjazny status" in html


def test_admin_submission_list_renders_unknown_status_fallback(admin_app, admin_client):
    user_id = create_user(admin_app)
    form_id = create_form(admin_app, slug="status_form", name="Status Form", user_id=user_id)
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.add(FormSubmission(submission_id="abc", form_slug="status_form", form_name="Status Form", process_status="MISSING_STATUS"))
        db.commit()
    login(admin_client)

    response = admin_client.get(f"/admin/forms/{form_id}/submissions")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Nieznany status: MISSING_STATUS" in html


def test_form_fields_can_be_edited(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.add(FormField(form_id=form_id, name="email", label="E-mail", type="email", required=True, section="Kontakt", sort_order=1))
        db.commit()
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/fields").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    with session_factory() as db:
        field = db.query(FormField).filter_by(form_id=form_id, name="email").one()
        field_id = field.id

    response = admin_client.post(
        f"/admin/forms/{form_id}/fields",
        data={
            "csrf_token": token,
            f"field_{field_id}_label": "Adres e-mail",
            f"field_{field_id}_type": "text",
            f"field_{field_id}_required": "on",
            f"field_{field_id}_section": "Dane kontaktowe",
            f"field_{field_id}_sort_order": "3",
        },
    )

    assert response.status_code == 302
    with session_factory() as db:
        field = db.query(FormField).filter_by(id=field_id).one()
        assert field.label == "Adres e-mail"
        assert field.type == "text"
        assert field.section == "Dane kontaktowe"
        assert field.sort_order == 3


def test_form_field_can_be_added_and_deactivated_without_removing_history(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(
            submission_id="history",
            form_slug="sample_form",
            form_name="Sample",
            data_json={"kurs": "Python"},
        )
        db.add(submission)
        db.commit()
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/fields").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/fields",
        data={
            "csrf_token": token,
            "action": "add",
            "new_name": "kurs",
            "new_label": "Kurs",
            "new_type": "select",
            "new_options": "Python\nExcel",
        },
    )

    assert response.status_code == 302
    with session_factory() as db:
        field = db.query(FormField).filter_by(form_id=form_id, name="kurs").one()
        field_id = field.id
        assert field.active is True
        assert field.options == ["Python", "Excel"]

    html = admin_client.get(f"/admin/forms/{form_id}/fields").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = admin_client.post(
        f"/admin/forms/{form_id}/fields",
        data={"csrf_token": token, "action": f"delete:{field_id}"},
    )

    assert response.status_code == 302
    with session_factory() as db:
        field = db.get(FormField, field_id)
        submission = db.query(FormSubmission).filter_by(submission_id="history").one()
        assert field.active is False
        assert submission.data_json["kurs"] == "Python"


def test_submissions_filter_and_sort_dynamic_data(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.add_all(
            [
                FormSubmission(submission_id="a", form_slug="sample_form", form_name="Sample", email="a@example.com", nazwisko="Zed", data_json={"kurs": "Excel"}),
                FormSubmission(submission_id="b", form_slug="sample_form", form_name="Sample", email="b@example.com", nazwisko="Ann", data_json={"kurs": "Python"}),
            ]
        )
        db.commit()
    login(admin_client)

    filtered = admin_client.get(f"/admin/forms/{form_id}/submissions?field=kurs&value=Python").get_data(as_text=True)
    sorted_html = admin_client.get(f"/admin/forms/{form_id}/submissions?sort=nazwisko&direction=asc").get_data(as_text=True)

    assert "b@example.com" in filtered
    assert "a@example.com" not in filtered
    assert sorted_html.index("Ann") < sorted_html.index("Zed")


def test_submissions_filter_supports_dynamic_operator(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.add_all(
            [
                FormSubmission(submission_id="a", form_slug="sample_form", form_name="Sample", email="a@example.com", data_json={"kurs": "Excel"}),
                FormSubmission(submission_id="b", form_slug="sample_form", form_name="Sample", email="b@example.com", data_json={"kurs": "Python"}),
            ]
        )
        db.commit()
    login(admin_client)

    html = admin_client.get(
        f"/admin/forms/{form_id}/submissions?field=kurs&operator=equals&value=Python"
    ).get_data(as_text=True)

    assert "b@example.com" in html
    assert "a@example.com" not in html


def test_officer_decision_visible_and_quick_update(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(submission_id="abc", form_slug="sample_form", form_name="Sample", email="a@example.com")
        db.add(submission)
        db.commit()
        submission_pk = submission.id
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/submissions").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/submissions/{submission_pk}/decision",
        data={"csrf_token": token, "officer_decision": "accepted", "officer_decision_reason": "OK"},
    )

    assert response.status_code == 302
    with session_factory() as db:
        submission = db.get(FormSubmission, submission_pk)
        assert submission.officer_decision == "accepted"
        assert submission.officer_decision_reason == ""
        decision = db.query(SubmissionDecision).filter_by(public_submission_id="abc").one()
        assert decision.decision == "accepted"
        assert decision.justification == ""
        assert decision.previous_status == "FORM_SUBMITTED"
        assert decision.target_status in {"OFFICER_ACCEPTED", "accepted_waiting_for_additional_fields"}
        assert decision.email_requested is True
        workflow_event = db.query(SubmissionWorkflowEvent).filter_by(public_submission_id="abc").one()
        assert workflow_event.new_status in {"REVIEW_ACCEPTED", "ACCEPTED_WAITING_FOR_ADDITIONAL_FIELDS"}
    html = admin_client.get(f"/admin/forms/{form_id}/submissions").get_data(as_text=True)
    assert "Decyzja urzednika" in html
    assert "accepted" in html


def test_officer_decision_mail_uses_mail_dispatch_service_once(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(
            submission_id="abc",
            form_slug="sample_form",
            form_name="Sample",
            email="jan@example.com",
            process_status="FORM_SUBMITTED",
        )
        db.add(submission)
        db.commit()
        submission_pk = submission.id
    calls = []
    admin_app.extensions["services"].mail_dispatch_service.dispatch_decision_email = lambda submission_id, decision: calls.append((submission_id, decision))
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/submissions").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    first = admin_client.post(
        f"/admin/forms/{form_id}/submissions/{submission_pk}/decision",
        data={"csrf_token": token, "officer_decision": "accepted", "officer_decision_reason": "OK"},
    )
    second = admin_client.post(
        f"/admin/forms/{form_id}/submissions/{submission_pk}/decision",
        data={"csrf_token": token, "officer_decision": "accepted", "officer_decision_reason": "OK again"},
    )

    assert first.status_code == 302
    assert second.status_code == 302
    assert calls == [("abc", "accepted")]
    with session_factory() as db:
        decisions = db.query(SubmissionDecision).filter_by(public_submission_id="abc").order_by(SubmissionDecision.id).all()
        assert [decision.email_requested for decision in decisions] == [True, False]


def test_officer_decision_update_survives_missing_decision_audit_table(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(
            submission_id="abc",
            form_slug="sample_form",
            form_name="Sample",
            email="jan@example.com",
            process_status="FORM_SUBMITTED",
        )
        db.add(submission)
        db.commit()
        submission_pk = submission.id
        db.execute(text("DROP TABLE submission_decisions"))
        db.commit()
    calls = []
    admin_app.extensions["services"].mail_dispatch_service.dispatch_decision_email = lambda submission_id, decision: calls.append((submission_id, decision))
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/submissions").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/submissions/{submission_pk}/decision",
        data={"csrf_token": token, "officer_decision": "rejected", "officer_decision_reason": "Braki"},
    )

    assert response.status_code == 302
    assert calls == [("abc", "rejected")]
    with session_factory() as db:
        submission = db.get(FormSubmission, submission_pk)
        assert submission.officer_decision == "rejected"
        assert submission.officer_decision_reason == "Braki"
        assert submission.process_status == "OFFICER_REJECTED"


def test_bulk_officer_decision_update_saves_all_rows(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        first = FormSubmission(
            submission_id="abc",
            form_slug="sample_form",
            form_name="Sample",
            email="jan@example.com",
            process_status="FORM_SUBMITTED",
        )
        second = FormSubmission(
            submission_id="def",
            form_slug="sample_form",
            form_name="Sample",
            email="ewa@example.com",
            process_status="FORM_SUBMITTED",
        )
        db.add_all([first, second])
        db.commit()
        first_pk = first.id
        second_pk = second.id
    calls = []
    admin_app.extensions["services"].mail_dispatch_service.dispatch_decision_email = lambda submission_id, decision: calls.append((submission_id, decision))
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/submissions").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/submissions/decisions",
        data={
            "csrf_token": token,
            "submission_row_ids": [str(first_pk), str(second_pk)],
            f"officer_decision_{first_pk}": "accepted",
            f"officer_decision_reason_{first_pk}": "",
            f"officer_decision_{second_pk}": "correction",
            f"officer_decision_reason_{second_pk}": "Popraw PESEL",
        },
    )

    assert response.status_code == 302
    assert calls == [("abc", "accepted")]
    with session_factory() as db:
        first = db.get(FormSubmission, first_pk)
        second = db.get(FormSubmission, second_pk)
        assert first.officer_decision == "accepted"
        assert first.officer_decision_reason == ""
        assert second.officer_decision == "correction"
        assert second.officer_decision_reason == "Popraw PESEL"
        assert second.correction_required == "Tak"
        assert second.correction_message == "Popraw PESEL"
        assert second.process_status == "WAITING_FOR_CORRECTION"


def test_bulk_officer_decision_update_requires_reason_for_rejection_or_correction(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(
            submission_id="abc",
            form_slug="sample_form",
            form_name="Sample",
            email="jan@example.com",
            process_status="FORM_SUBMITTED",
        )
        db.add(submission)
        db.commit()
        submission_pk = submission.id
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/submissions").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/submissions/decisions",
        data={
            "csrf_token": token,
            "submission_row_ids": [str(submission_pk)],
            f"officer_decision_{submission_pk}": "rejected",
            f"officer_decision_reason_{submission_pk}": "",
        },
        follow_redirects=True,
    )

    assert "Pominieto 1 decyzji" in response.get_data(as_text=True)
    with session_factory() as db:
        submission = db.get(FormSubmission, submission_pk)
        assert submission.officer_decision == ""


def test_submission_detail_survives_missing_submission_file_alignment_columns(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(
            submission_id="abc",
            form_slug="sample_form",
            form_name="Sample",
            email="jan@example.com",
            process_status="FORM_SUBMITTED",
        )
        db.add(submission)
        db.commit()
        submission_pk = submission.id
        db.execute(text("ALTER TABLE submission_files DROP COLUMN original_filename"))
        db.commit()
    login(admin_client)

    response = admin_client.get(f"/admin/forms/{form_id}/submissions/{submission_pk}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "dokumentow" in html


def test_dashboard_survives_missing_submission_file_alignment_columns(admin_app, admin_client):
    create_user(admin_app)
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.execute(text("ALTER TABLE submission_files DROP COLUMN original_filename"))
        db.commit()
    login(admin_client)

    response = admin_client.get("/admin/dashboard")

    assert response.status_code == 200
    assert "Dashboard" in response.get_data(as_text=True)


def test_send_mail_logs_email(admin_app, admin_client, monkeypatch):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(
            submission_id="abc",
            form_slug="sample_form",
            form_name="Sample",
            email="jan@example.com",
            nazwisko="Kowalski",
            imiona="Jan",
            data_json={"imiona": "Jan"},
        )
        db.add(submission)
        db.flush()
        db.add(MailTemplate(form_id=form_id, name="Info", subject="Witaj {{ imiona }}", html_body="<p>{{ submission_id }}</p>"))
        db.commit()
        submission_pk = submission.id
    sent = []
    admin_app.extensions["notification_service"].smtp_sender = lambda **kwargs: sent.append(kwargs)
    login(admin_client)
    token = admin_client.get(f"/admin/forms/{form_id}/submissions/{submission_pk}/mail").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/submissions/{submission_pk}/mail",
        data={"csrf_token": token, "to_email": "jan@example.com", "subject": "Witaj {{ imiona }}", "html_body": "<p>{{ submission_id }}</p>"},
    )

    assert response.status_code == 302
    assert sent[0]["subject"] == "Witaj Jan"
    assert "platform-mail-card" in sent[0]["html_body"]
    assert "abc" in sent[0]["html_body"]
    with session_factory() as db:
        log = db.query(EmailLog).one()
        assert log.status == "sent"
        assert log.to_email == "jan@example.com"


def test_send_mail_view_populates_editor_from_selected_template(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(
            submission_id="abc",
            form_slug="sample_form",
            form_name="Sample",
            email="jan@example.com",
            nazwisko="Kowalski",
            imiona="Jan",
        )
        db.add(submission)
        db.flush()
        db.add(
            MailTemplate(
                form_id=form_id,
                name="Wniosek zaakceptowany",
                subject="Temat {{ imiona }}",
                content_html="<p>HTML {{ submission_id }}</p>",
                content_text="TXT {{ submission_id }}",
                html_body="<p>HTML {{ submission_id }}</p>",
                text_body="TXT {{ submission_id }}",
            )
        )
        db.commit()
        submission_pk = submission.id
    login(admin_client)

    html = admin_client.get(f"/admin/forms/{form_id}/submissions/{submission_pk}/mail").get_data(as_text=True)

    payload_text = html.split("const templates = ", 1)[1].split(";\n", 1)[0]
    payload = json.loads(payload_text)
    assert payload[0]["subject"] == "Temat {{ imiona }}"
    assert payload[0]["html_body"] == "<p>HTML {{ submission_id }}</p>"
    assert payload[0]["text_body"] == "TXT {{ submission_id }}"
    assert "fillFromTemplate()" in html
    assert "Mail systemowy" not in html


def test_send_mail_uses_first_template_when_manual_fields_are_empty(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(
            submission_id="abc",
            form_slug="sample_form",
            form_name="Sample",
            email="jan@example.com",
            nazwisko="Kowalski",
            imiona="Jan",
            data_json={"imiona": "Jan"},
        )
        db.add(submission)
        db.flush()
        db.add(MailTemplate(form_id=form_id, name="Info", subject="Witaj {{ imiona }}", html_body="<p>{{ submission_id }}</p>"))
        db.commit()
        submission_pk = submission.id
    sent = []
    admin_app.extensions["notification_service"].smtp_sender = lambda **kwargs: sent.append(kwargs)
    login(admin_client)
    token = admin_client.get(f"/admin/forms/{form_id}/submissions/{submission_pk}/mail").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/submissions/{submission_pk}/mail",
        data={"csrf_token": token, "to_email": "jan@example.com"},
    )

    assert response.status_code == 302
    assert sent[0]["subject"] == "Witaj Jan"
    assert "abc" in sent[0]["html_body"]
    with session_factory() as db:
        log = db.query(EmailLog).one()
        assert log.status == "sent"


def test_send_mail_uses_system_fallback_only_without_templates(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(
            submission_id="abc",
            form_slug="sample_form",
            form_name="Sample",
            email="jan@example.com",
            nazwisko="Kowalski",
            imiona="Jan",
            data_json={"imiona": "Jan"},
        )
        db.add(submission)
        db.commit()
        submission_pk = submission.id
    sent = []
    admin_app.extensions["notification_service"].smtp_sender = lambda **kwargs: sent.append(kwargs)
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/submissions/{submission_pk}/mail").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    assert "Mail systemowy" in html

    response = admin_client.post(
        f"/admin/forms/{form_id}/submissions/{submission_pk}/mail",
        data={"csrf_token": token, "to_email": "jan@example.com"},
    )

    assert response.status_code == 302
    assert sent[0]["subject"] == "Informacja dotyczaca zgloszenia abc"
    assert "Przesylamy informacje dotyczaca zgloszenia" in sent[0]["html_body"]
    with session_factory() as db:
        log = db.query(EmailLog).one()
        assert log.status == "sent"
        assert log.template_id is None


def test_send_mail_failure_flash_includes_error_reason(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(
            submission_id="abc",
            form_slug="sample_form",
            form_name="Sample",
            email="jan@example.com",
            nazwisko="Kowalski",
            imiona="Jan",
            data_json={"imiona": "Jan"},
        )
        db.add(submission)
        db.commit()
        submission_pk = submission.id

    def fail_smtp(**kwargs):
        raise RuntimeError("SMTP auth failed")

    admin_app.extensions["notification_service"].smtp_sender = fail_smtp
    login(admin_client)
    token = admin_client.get(f"/admin/forms/{form_id}/submissions/{submission_pk}/mail").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/submissions/{submission_pk}/mail",
        data={"csrf_token": token, "to_email": "jan@example.com", "subject": "Test", "html_body": "<p>Test</p>"},
        follow_redirects=True,
    )

    html = response.get_data(as_text=True)
    assert "Nie udalo sie wyslac maila: RuntimeError: SMTP auth failed" in html
    with session_factory() as db:
        log = db.query(EmailLog).one()
        assert log.status == "failed"
        assert log.error_message == "RuntimeError: SMTP auth failed"


def test_send_bulk_mail_to_selected_submissions_uses_matching_template(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.add(
            FormSubmission(
                submission_id="abc",
                form_slug="sample_form",
                form_name="Sample",
                email="jan@example.com",
                imiona="Jan",
                officer_decision="accepted",
                data_json={"imiona": "Jan"},
            )
        )
        db.add(
            MailTemplate(
                form_id=form_id,
                name="Accepted",
                subject="Witaj {{ imiona }}",
                html_body="<p>{{ officer_decision }}</p>",
                text_body="{{ submission_id }}",
                trigger_event="manual_bulk",
                trigger_decision="accepted",
            )
        )
        db.commit()
    sent = []
    admin_app.extensions["notification_service"].smtp_sender = lambda **kwargs: sent.append(kwargs)
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/submissions").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/submissions/mail-selected",
        data={"csrf_token": token, "submission_ids": ["abc"]},
    )

    assert response.status_code == 302
    assert sent[0]["subject"] == "Witaj Jan"
    assert "platform-mail-card" in sent[0]["html_body"]
    assert "accepted" in sent[0]["html_body"]
    assert sent[0]["text_body"].strip().startswith("Accepted")
    with session_factory() as db:
        log = db.query(EmailLog).one()
        assert log.status == "sent"
        assert log.public_submission_id == "abc"


def test_admin_mail_module_keeps_endpoint_names(admin_app):
    import routes.admin.mail as admin_mail
    from routes.admin import bp

    assert admin_mail is not None
    assert bp.name == "admin"
    with admin_app.test_request_context():
        assert url_for("admin.submission_mail", form_id=1, submission_pk=2) == "/admin/forms/1/submissions/2/mail"
        assert url_for("admin.submissions_mail_selected", form_id=1) == "/admin/forms/1/submissions/mail-selected"
        assert url_for("admin.mail_templates_list", form_id=1) == "/admin/forms/1/mail-templates"
        assert url_for("admin.mail_template_delete", form_id=1, template_id=2) == "/admin/forms/1/mail-templates/2/delete"
        assert url_for("admin.mail_footers_list", form_id=1) == "/admin/forms/1/mail-footers"


@pytest.mark.parametrize("role,email", [("super_admin", "admin@example.com"), ("admin", "admin-role@example.com"), ("form_manager", "manager@example.com")])
def test_mail_template_delete_allowed_for_all_managing_roles(admin_app, admin_client, role, email):
    user_id = create_user(admin_app, email=email, role=role)
    form_id = create_form(admin_app, slug=f"form_{role}", name="Sample", user_id=None if role == "super_admin" else user_id)
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        template = MailTemplate(form_id=form_id, name="Delete me", subject="Temat", html_body="<p>Test</p>")
        db.add(template)
        db.commit()
        template_id = template.id
    login(admin_client, email=email)
    html = admin_client.get(f"/admin/forms/{form_id}/mail-templates").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(f"/admin/forms/{form_id}/mail-templates/{template_id}/delete", data={"csrf_token": token})

    assert response.status_code == 302
    with session_factory() as db:
        assert db.get(MailTemplate, template_id) is None


def test_mail_template_delete_requires_manage_permission(admin_app, admin_client):
    user_id = create_user(admin_app, email="viewer@example.com", role="admin")
    form_id = create_form(admin_app, slug="view_only", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.add(FormPermission(user_id=user_id, form_id=form_id, can_manage=False))
        template = MailTemplate(form_id=form_id, name="Keep me", subject="Temat", html_body="<p>Test</p>")
        db.add(template)
        db.commit()
        template_id = template.id
    login(admin_client, email="viewer@example.com")
    token = admin_client.get("/admin/forms").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = admin_client.post(f"/admin/forms/{form_id}/mail-templates/{template_id}/delete", data={"csrf_token": token})

    assert response.status_code == 403
    with session_factory() as db:
        assert db.get(MailTemplate, template_id) is not None


def test_zip_mail_template_import_reads_content_text_and_assets(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    login(admin_client)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr(
            "zaakceptowany.html",
            "<html><head><style>.x{color:red}</style></head><body>Wniosek zaakceptowany<br>{{ imiona }}<br>Instrukcja<br>Przejdz dalej</body></html>",
        )
        archive.writestr("zaakceptowany.txt", "Wniosek zaakceptowany\n\n{{ imiona }}\n\nInstrukcja\nPrzejdz dalej")
        archive.writestr("images/logo.png", b"png-bytes")
    zip_buffer.seek(0)
    html = admin_client.get(f"/admin/forms/{form_id}/mail-templates/import-zip").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/mail-templates/import-zip",
        data={
            "csrf_token": token,
            "name": "ZIP",
            "subject": "Temat",
            "zip_file": (zip_buffer, "mail.zip"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        template = db.query(MailTemplate).filter_by(name="ZIP").one()
        assert template.content_title == "Wniosek zaakceptowany"
        assert "<style>" not in template.html_body
        assert "{{ imiona }}" in template.html_body
        assert "{{ imiona }}" in template.text_body
        assert "Przejdz dalej" in template.instruction_text
        asset = db.query(MailTemplateAsset).one()
        assert asset.filename == "logo.png"
        assert asset.content == b"png-bytes"


def test_html_mail_template_import_is_primary_path(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    login(admin_client)
    list_html = admin_client.get(f"/admin/forms/{form_id}/mail-templates").get_data(as_text=True)

    assert "Importuj HTML" in list_html
    assert "Dodaj szablon" in list_html
    assert "Import ZIP" not in list_html

    html = admin_client.get(f"/admin/forms/{form_id}/mail-templates/import-html").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = admin_client.post(
        f"/admin/forms/{form_id}/mail-templates/import-html",
        data={
            "csrf_token": token,
            "name": "HTML",
            "subject": "Temat {{ submission_id }}",
            "body_html": "<h2>Potwierdzenie</h2><p>{{ submission_id }}</p>",
            "body_text": "Potwierdzenie {{ submission_id }}",
        },
    )

    assert response.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        template = db.query(MailTemplate).filter_by(name="HTML").one()
        assert template.content_title == "Potwierdzenie"
        assert "{{ submission_id }}" in template.content_html
        assert template.content_text == "Potwierdzenie {{ submission_id }}"


def test_form_manager_cannot_use_technical_zip_import(admin_app, admin_client):
    manager_id = create_user(admin_app, email="manager@example.com", role="form_manager")
    form_id = create_form(admin_app, slug="sample_form", name="Sample", user_id=manager_id)
    login(admin_client, email="manager@example.com")

    response = admin_client.get(f"/admin/forms/{form_id}/mail-templates/import-zip")

    assert response.status_code == 403


def test_platform_mail_layout_has_expected_defaults():
    from types import SimpleNamespace
    from services.mail_template_service import MAIL_LAYOUT, render_platform_mail_html

    template = SimpleNamespace(
        name="Wniosek zaakceptowany",
        content_title="Wniosek zaakceptowany",
        html_body="<p>Dzien dobry {{ imiona }}</p>",
        text_body="Dzien dobry {{ imiona }}",
        instruction_html="<p>Krok</p>",
        instruction_text="Instrukcja\nKrok",
        footer_note="Numer: <strong>{{ submission_id }}</strong>",
    )
    html = render_platform_mail_html(
        template,
        {"imiona": "Jan", "submission_id": "abc", "form_name": "Sample", "status_label": "Zaakceptowano"},
    )

    assert MAIL_LAYOUT["container_width"] == "600px"
    assert "width:600px" in html
    assert "#f0f1f5" in html
    assert "#1d2e5b" in html
    assert "#c8a35d" in html
    assert "Dzien dobry Jan" in html
    assert "Numer zgloszenia" in html


def test_mail_template_edit_does_not_expose_style_controls(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    login(admin_client)

    html = admin_client.get(f"/admin/forms/{form_id}/mail-templates/new").get_data(as_text=True)

    assert "Wgraj plik HTML" in html
    assert "Wklej HTML" in html
    assert "Treść TXT" in html
    assert "Instrukcja" in html
    assert "Kolor" not in html
    assert 'name="label_color"' not in html
    assert 'name="label_background"' not in html
    assert "Margines" not in html


def test_mail_template_preview_renders_platform_layout(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    login(admin_client)

    html = admin_client.get(f"/admin/forms/{form_id}/mail-templates/new").get_data(as_text=True)

    assert "platform-mail-card" in html
    assert "#f0f1f5" in html
    assert "Numer zgloszenia" in html


def test_simple_html_full_document_extracts_body_and_sanitizes():
    from services.mail_template_service import parse_mail_content

    parsed = parse_mail_content(
        '<!doctype html><html><head><title>x</title></head><body><h2>Odrzucony</h2><p>{{ decision }}</p><script>alert(1)</script><a href="javascript:alert(1)">x</a></body></html>'
    )

    assert parsed.title == "Odrzucony"
    assert "<body" not in parsed.body_html
    assert "<script" not in parsed.body_html
    assert "javascript:" not in parsed.body_html
    assert "{{ decision }}" in parsed.body_html


def test_mail_import_inlines_safe_embedded_css_and_keeps_inline_styles():
    from services.mail_template_service import parse_mail_content

    parsed = parse_mail_content(
        """
        <!doctype html>
        <html>
        <head>
            <style>
                .lead { color: #1d2e5b; font-weight: 700; background-image: url("https://bad.test/x.png"); }
                #cta { background-color: #c8a35d; padding: 12px; position: absolute; }
                p { line-height: 1.5; }
                .lead strong { color: red; }
            </style>
        </head>
        <body>
            <h2>Potwierdzenie</h2>
            <p class="lead" style="font-size: 18px; position: fixed;">Witaj {{ imie }}</p>
            <div id="cta">Dalej</div>
        </body>
        </html>
        """
    )

    assert 'style="' in parsed.body_html
    assert "color:#1d2e5b" in parsed.body_html
    assert "font-weight:700" in parsed.body_html
    assert "line-height:1.5" in parsed.body_html
    assert "font-size:18px" in parsed.body_html
    assert 'style="background-color:#c8a35d;padding:12px"' in parsed.body_html
    assert "background-image" not in parsed.body_html
    assert "position" not in parsed.body_html
    assert "class=" not in parsed.body_html
    assert "id=" not in parsed.body_html
    assert "<style" not in parsed.body_html


def test_simple_html_fragment_is_preserved_as_content():
    from services.mail_template_service import parse_mail_content

    parsed = parse_mail_content("<p>Potwierdzenie {{ submission_id }}</p>")

    assert parsed.body_html == "<p>Potwierdzenie {{ submission_id }}</p>"


def test_platform_mail_renders_jinja_submission_and_agreement():
    from types import SimpleNamespace
    from services.mail_template_service import build_mail_context, render_platform_mail_html

    template = SimpleNamespace(
        name="Umowa",
        content_title="Umowa {{ agreement.get(\"number\") }}",
        content_html="<p>{{ submission.get(\"imie\") }} / {{ agreement.get(\"training_name\") }}</p>",
        content_text="",
        html_body="<p>{{ submission.get(\"imie\") }} / {{ agreement.get(\"training_name\") }}</p>",
        text_body="",
        instruction_html="",
        instruction_text="",
        footer_note="",
    )
    form = SimpleNamespace(name="Sample", slug="sample")
    submission = SimpleNamespace(
        data_json={"imie": "Jan"},
        __table__=SimpleNamespace(columns=[]),
    )
    context = build_mail_context(form, submission, extra={"agreement": {"number": "1/2026", "training_name": "Excel"}})
    html = render_platform_mail_html(template, context)

    assert "Umowa 1/2026" in html
    assert "Jan / Excel" in html
    assert "platform-instruction-title" not in html
    assert "Instrukcja" not in html


def test_platform_mail_renders_html_escaped_jinja_quotes():
    from types import SimpleNamespace
    from services.mail_template_service import render_platform_mail_html

    template = SimpleNamespace(
        name="Umowa",
        content_title="Umowa",
        content_html="<p>{{ agreement.get(&quot;number&quot;) }}</p>",
        content_text="",
        html_body="",
        text_body="",
        instruction_html="",
        instruction_text="",
        footer_note="",
    )

    html = render_platform_mail_html(template, {"agreement": {"number": "1/2026"}, "form_name": "Sample", "submission_id": "abc"})

    assert "1/2026" in html
    assert "&quot;number&quot;" not in html


def test_auto_template_selection_by_type_for_decisions(admin_app):
    from routes.admin import select_mail_template

    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    accepted = MailTemplate(form_id=form_id, name="Accepted", template_type="accepted", subject="", html_body="")
    rejected = MailTemplate(form_id=form_id, name="Rejected", template_type="rejected", subject="", html_body="")
    submission = FormSubmission(
        submission_id="abc",
        form_slug="sample_form",
        form_name="Sample",
        officer_decision="rejected",
    )

    assert select_mail_template([accepted, rejected], submission, "manual_bulk").name == "Rejected"


def test_super_admin_can_upload_logo(admin_app, admin_client):
    create_user(admin_app)
    login(admin_client)
    html = admin_client.get("/admin/logos").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    response = admin_client.post(
        "/admin/logos",
        data={
            "csrf_token": token,
            "name": "Logo",
            "logo_file": (io.BytesIO(png_bytes), "logo.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        logo = db.query(Logo).one()
        assert logo.name == "Logo"
        assert logo.active is True
        assert logo.size_bytes == len(png_bytes)
        assert logo.checksum_sha256
        logo_id = logo.id

    edit_html = admin_client.get(f"/admin/logos/{logo_id}/edit").get_data(as_text=True)
    edit_token = edit_html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = admin_client.post(
        f"/admin/logos/{logo_id}/edit",
        data={"csrf_token": edit_token, "name": "Logo po zmianie"},
    )

    assert response.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        logo = db.get(Logo, logo_id)
        assert logo.name == "Logo po zmianie"
        assert logo.active is False


def test_super_admin_can_toggle_logo_and_fetch_asset(admin_app, admin_client):
    create_user(admin_app)
    login(admin_client)
    logo_path = Path(admin_app.config["TEMP_DIR"]) / "logo.png"
    logo_path.parent.mkdir(parents=True, exist_ok=True)
    logo_path.write_bytes(b"logo-bytes")
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        logo = Logo(
            name="Logo",
            filename="logo.png",
            storage_path=str(logo_path),
            mime_type="image/png",
            size_bytes=10,
            checksum_sha256="abc",
            active=True,
        )
        db.add(logo)
        db.commit()
        logo_id = logo.id

    html = admin_client.get("/admin/logos").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    assert "Logo" in html
    assert admin_client.get(f"/admin/logos/{logo_id}/asset").status_code == 200

    response = admin_client.post(f"/admin/logos/{logo_id}/toggle", data={"csrf_token": token})

    assert response.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        logo = db.get(Logo, logo_id)
        assert logo.active is False
    assert admin_client.get(f"/admin/logos/{logo_id}/asset").status_code == 200


def test_regular_admin_cannot_toggle_logo(admin_app, admin_client):
    create_user(admin_app, role="admin")
    logo_path = Path(admin_app.config["TEMP_DIR"]) / "logo.png"
    logo_path.parent.mkdir(parents=True, exist_ok=True)
    logo_path.write_bytes(b"logo-bytes")
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        logo = Logo(
            name="Logo",
            filename="logo.png",
            storage_path=str(logo_path),
            mime_type="image/png",
            size_bytes=10,
            checksum_sha256="abc",
            active=True,
        )
        db.add(logo)
        db.commit()
        logo_id = logo.id

    login(admin_client)
    with admin_client.session_transaction() as session:
        token = session["admin_csrf_token"]

    response = admin_client.post(f"/admin/logos/{logo_id}/toggle", data={"csrf_token": token})

    assert response.status_code == 403
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        logo = db.get(Logo, logo_id)
        assert logo.active is True


def test_form_manager_can_select_existing_logo_but_not_upload(admin_app, admin_client):
    manager_id = create_user(admin_app, email="manager@example.com", role="form_manager")
    form_id = create_form(admin_app, slug="owned", name="Owned", user_id=manager_id)
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        logo = Logo(name="Logo", filename="logo.png", storage_path=str(Path(admin_app.config["TEMP_DIR"]) / "logo.png"), mime_type="image/png", active=True)
        db.add(logo)
        db.commit()
        logo_id = logo.id
    login(admin_client, email="manager@example.com")
    html = admin_client.get(f"/admin/forms/{form_id}/edit").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/edit",
        data={
            "csrf_token": token,
            "name": "Owned",
            "title": "Owned",
            "slug": "owned",
            "logo_id": str(logo_id),
            "is_active": "on",
            "is_public": "on",
        },
    )

    assert response.status_code == 302
    with session_factory() as db:
        form = db.get(Form, form_id)
        assert form.logo_id == logo_id

    upload_html = admin_client.get("/admin/forms/upload").get_data(as_text=True)
    upload_token = upload_html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = admin_client.post(
        "/admin/logos",
        data={
            "csrf_token": upload_token,
            "name": "Nope",
            "logo_file": (io.BytesIO(b"png"), "logo.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 403


def test_mail_footer_uses_logo_library_instead_of_manual_path(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        logo = Logo(
            name="Logo UMWL",
            filename="logo.png",
            storage_path=str(Path(admin_app.config["TEMP_DIR"]) / "logo.png"),
            mime_type="image/png",
            active=True,
        )
        db.add(logo)
        db.commit()
        logo_id = logo.id

    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/mail-footers/new").get_data(as_text=True)
    assert "Logo z Nextcloud lub URL" not in html
    assert "Logo w stopce" in html
    assert "Brak logo" in html
    assert "Logo UMWL" in html
    assert "Logo można dodać tylko z poziomu konta super_admin." in html
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/mail-footers/new",
        data={
            "csrf_token": token,
            "name": "Stopka",
            "logo_id": str(logo_id),
            "logo_path": "https://example.com/manual.png",
            "html_body": "<p>Kontakt</p>",
            "is_active": "on",
            "is_default": "on",
        },
    )

    assert response.status_code == 302
    with session_factory() as db:
        footer = db.query(MailFooter).one()
        assert footer.logo_id == logo_id
        assert footer.logo_path == ""
        assert footer.html_body == "<p>Kontakt</p>"
        assert footer.is_active is True
        assert footer.is_default is True


def test_mail_footer_rejects_inactive_logo(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="sample_form", name="Sample")
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        active_logo = Logo(
            name="Aktywne logo",
            filename="active.png",
            storage_path=str(Path(admin_app.config["TEMP_DIR"]) / "active.png"),
            mime_type="image/png",
            active=True,
        )
        inactive_logo = Logo(
            name="Nieaktywne logo",
            filename="inactive.png",
            storage_path=str(Path(admin_app.config["TEMP_DIR"]) / "inactive.png"),
            mime_type="image/png",
            active=False,
        )
        db.add_all([active_logo, inactive_logo])
        db.commit()
        inactive_logo_id = inactive_logo.id

    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/mail-footers/new").get_data(as_text=True)
    assert "Aktywne logo" in html
    assert "Nieaktywne logo" not in html
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/mail-footers/new",
        data={
            "csrf_token": token,
            "name": "Stopka",
            "logo_id": str(inactive_logo_id),
            "html_body": "<p>Kontakt</p>",
            "is_active": "on",
        },
    )

    assert response.status_code == 403
    with session_factory() as db:
        assert db.query(MailFooter).count() == 0


def test_user_without_form_access_cannot_edit_mail_footer(admin_app, admin_client):
    owner_id = create_user(admin_app, email="owner@example.com", role="form_manager")
    create_user(admin_app, email="blocked@example.com", role="form_manager")
    form_id = create_form(admin_app, slug="owned", name="Owned", user_id=owner_id)

    login(admin_client, email="blocked@example.com")
    response = admin_client.get(f"/admin/forms/{form_id}/mail-footers/new")

    assert response.status_code == 403


def test_submission_files_store_metadata_only(admin_app):
    create_user(admin_app)
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        submission = FormSubmission(submission_id="abc", form_slug="sample_form", form_name="Sample")
        db.add(submission)
        db.flush()
        db.add(
            SubmissionFile(
                submission_id=submission.id,
                public_submission_id="abc",
                form_slug="sample_form",
                filename="doc.pdf",
                storage_path="output/sample_form/pdf/doc.pdf",
                mime_type="application/pdf",
                size_bytes=100,
                checksum_sha256="abc",
                signed=False,
            )
        )
        db.commit()
        file_row = db.query(SubmissionFile).one()
        assert hasattr(file_row, "storage_path")
        assert hasattr(file_row, "signature_validation_result")
        assert hasattr(file_row, "generated_at")
        assert not hasattr(file_row, "content")


def test_super_admin_can_block_user(admin_app, admin_client):
    create_user(admin_app)
    user_id = create_user(admin_app, email="blocked@example.com", role="form_manager")
    login(admin_client)
    token = admin_client.get("/admin/users").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(f"/admin/users/{user_id}/toggle-block", data={"csrf_token": token})

    assert response.status_code == 302
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        user = db.get(User, user_id)
        assert user.is_blocked is True


def test_super_admin_can_change_user_password(admin_app, admin_client):
    from werkzeug.security import check_password_hash

    create_user(admin_app)
    user_id = create_user(admin_app, email="password@example.com", role="form_manager")
    login(admin_client)
    html = admin_client.get(f"/admin/users/{user_id}/password").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/users/{user_id}/password",
        data={"csrf_token": token, "password": "new-secret", "password_confirm": "new-secret"},
    )

    assert response.status_code == 302
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        user = db.get(User, user_id)
        assert check_password_hash(user.password_hash, "new-secret")


def test_super_admin_can_delete_user(admin_app, admin_client):
    create_user(admin_app)
    user_id = create_user(admin_app, email="delete@example.com", role="form_manager")
    form_id = create_form(admin_app, slug="delete_user_form", name="Delete User Form", user_id=user_id)
    login(admin_client)
    token = admin_client.get("/admin/users").get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(f"/admin/users/{user_id}/delete", data={"csrf_token": token})

    assert response.status_code == 302
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        assert db.get(User, user_id) is None
        assert db.query(FormPermission).filter_by(user_id=user_id).count() == 0
        assert db.get(Form, form_id).created_by_id is None


def test_form_training_catalog_can_be_edited_in_admin(admin_app, admin_client):
    create_user(admin_app)
    definition = {
        "title": "Training Form",
        "fields": [],
        "documents": [
            {
                "id": "declaration",
                "label": "Deklaracja",
                "kind": "generated_pdf",
                "enabled": True,
                "template_html": "<p>Deklaracja</p>",
                "fields": [
                    {"type": "section", "label": "Wybór szkoleń"},
                    {"type": "section", "label": "Oświadczenia uczestnika"},
                    {"type": "checkbox", "name": "osw_rodo"},
                ],
            }
        ],
        "workflow": {
            "name": "Workflow",
            "initial_step": "submission",
            "steps": [{"id": "submission", "type": "end"}],
        },
    }
    form_id = create_form(admin_app, slug="training_form", name="Training Form", definition_json=definition)
    login(admin_client)
    html = admin_client.get(f"/admin/forms/{form_id}/edit").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(
        f"/admin/forms/{form_id}/edit",
        data={
            "csrf_token": token,
            "name": "Training Form",
            "slug": "training_form",
            "title": "Training Form",
            "sort_order": "0",
            "workflow_name": "Workflow",
            "workflow_initial_step": "submission",
            "workflow_json": json.dumps(definition["workflow"]),
            "is_active": "on",
            "is_public": "on",
            "training_selection_enabled": "on",
            "training_selection_name": "selected_trainings",
            "training_selection_label": "Wybierz szkolenia",
            "training_selection_max_total": "5000",
            "training_selection_currency": "PLN",
            "training_selection_required": "on",
            "training_item_id": ["s1", "s2"],
            "training_item_name": ["Szkolenie 1", "Szkolenie 2"],
            "training_item_price": ["1200", "1800"],
        },
    )

    assert response.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        form = db.get(Form, form_id)
        declaration = next(document for document in form.definition_json["documents"] if document["id"] == "declaration")
        training_field = next(field for field in declaration["fields"] if field["type"] == "training_selection")
        assert [field.get("name") or field.get("label") for field in declaration["fields"]] == [
            "Wybór szkoleń",
            "selected_trainings",
            "Oświadczenia uczestnika",
            "osw_rodo",
        ]
        assert training_field["max_total_amount"] == 5000
        assert training_field["currency"] == "PLN"
        assert training_field["catalog"] == [
            {"id": "s1", "name": "Szkolenie 1", "price": 1200},
            {"id": "s2", "name": "Szkolenie 2", "price": 1800},
        ]


def test_declaration_download_disabled_before_declaration_form_is_completed(admin_app, admin_client):
    create_form(
        admin_app,
        slug="declaration_flow",
        name="Declaration Flow",
        definition_json={
            "title": "Declaration Flow",
            "fields": [],
            "documents": [
                {
                    "id": "declaration",
                    "label": "Deklaracja",
                    "kind": "generated_pdf",
                    "enabled": True,
                    "template_html": "<p>Deklaracja</p>",
                    "fields": [{"type": "radio", "name": "deklaracja_18_lat", "label": "18 lat", "options": ["Tak", "Nie"]}],
                }
            ],
        },
    )
    session_factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with session_factory() as db:
        db.add(
            FormSubmission(
                submission_id="decl-1",
                form_slug="declaration_flow",
                form_name="Declaration Flow",
                officer_decision="accepted",
                acceptance_required="Tak",
                declaration_required="Tak",
                declaration_generated="",
                declaration_filename="",
                agreement_required="Nie",
            )
        )
        db.commit()

    response = admin_client.get("/do-podpisania?submission_id=decl-1")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/declaration/declaration_flow/decl-1" in html
    assert "/downloads/pdfs/declaration_flow" not in html
    assert "Pobierz deklarac" in html
    assert "disabled" in html
    with session_factory() as db:
        submission = db.query(FormSubmission).filter_by(submission_id="decl-1").one()
        assert submission.declaration_generated != "Tak"
