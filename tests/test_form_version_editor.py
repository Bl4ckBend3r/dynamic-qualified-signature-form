from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from config import Config
from database import create_session_factory
from models import Form, FormSubmission, FormVersion, User
from services.form_version_service import (
    FORM_VERSION_ARCHIVED,
    FORM_VERSION_DRAFT,
    FORM_VERSION_PUBLISHED,
    FormVersionService,
)
from testing_support import InMemoryStorage


class VersionEditorTestConfig(Config):
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
def version_editor_app(tmp_path, form_definition):
    import app as app_module

    class TestConfig(VersionEditorTestConfig):
        TEMP_DIR = tmp_path / "tmp"
        DATABASE_URL = f"sqlite:///{tmp_path / 'version-editor.db'}"

    flask_app = app_module.create_app(
        config_object=TestConfig,
        storage_override=InMemoryStorage(form_definition),
    )
    flask_app.config.update(TESTING=True, TEMP_DIR=tmp_path / "tmp")
    Path(flask_app.config["TEMP_DIR"]).mkdir(parents=True, exist_ok=True)
    return flask_app


def _availability(*, required: bool) -> list[dict]:
    return [
        {"step": "submission", "visible": True, "editable": True, "required": required},
    ]


def _definition(field_names: tuple[str, ...], *, required: dict[str, bool]) -> dict:
    return {
        "title": "Formularz wersjonowany",
        "description": "",
        "fields": [
            {
                "name": name,
                "label": name.upper(),
                "type": "text",
                "required": False,
                "availability": _availability(required=required[name]),
            }
            for name in field_names
        ],
        "workflow": {
            "initial_step": "submission",
            "steps": [{"id": "submission"}],
        },
    }


def _seed_v1_v2(app, *, user_id: int | None = None) -> dict[str, int | dict]:
    session_factory = create_session_factory(app.config["DATABASE_URL"])
    service = FormVersionService()
    v1_definition = _definition(("a", "b", "c"), required={"a": True, "b": False, "c": True})
    v2_definition = _definition(("a", "c", "d"), required={"a": False, "c": True, "d": False})

    with session_factory() as db:
        form = Form(
            slug="version-editor",
            name="Formularz wersjonowany",
            title="Formularz wersjonowany",
            description="",
            definition_json=deepcopy(v1_definition),
            is_active=True,
            is_public=True,
            created_by_id=user_id,
        )
        db.add(form)
        db.flush()
        service.apply_to_legacy_editor(db, form, v1_definition)
        v1 = service.create_initial_version(
            db, form, actor_id=user_id, status=FORM_VERSION_PUBLISHED
        )
        stored_v1_definition = deepcopy(v1.definition_json)
        submission = FormSubmission(
            submission_id="HISTORY-V1",
            form_slug=form.slug,
            form_name=form.name,
            form_version_id=v1.id,
            data_json={"a": "history", "b": "kept in submission"},
        )
        db.add(submission)

        v2 = service.clone_to_draft(db, form, v1, actor_id=user_id, bump="minor")
        service.update_definition(v2, v2_definition, change_summary="B usunięte, D dodane")
        service.publish(db, form, v2, actor_id=user_id, change_summary="Publikacja V2")
        db.commit()
        return {
            "form_id": form.id,
            "v1_id": v1.id,
            "v2_id": v2.id,
            "submission_id": submission.id,
            "v1_definition": stored_v1_definition,
            "v2_definition": v2_definition,
        }


def _create_admin(app) -> int:
    session_factory = create_session_factory(app.config["DATABASE_URL"])
    with session_factory() as db:
        user = User(
            email="admin@example.com",
            password_hash=generate_password_hash("secret"),
            role="super_admin",
            is_active=True,
            is_blocked=False,
        )
        db.add(user)
        db.commit()
        return user.id


def _login(client) -> str:
    page = client.get("/admin/").get_data(as_text=True)
    token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/admin/",
        data={"email": "admin@example.com", "password": "secret", "csrf_token": token},
    )
    assert response.status_code == 302
    with client.session_transaction() as session:
        return session["admin_csrf_token"]


def _builder_state(html: str) -> list[dict]:
    match = re.search(
        r'<script type="application/json" data-builder-initial-state>(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match
    return json.loads(match.group(1))


def test_v3_clone_is_an_independent_exact_snapshot_of_v2(version_editor_app):
    seeded = _seed_v1_v2(version_editor_app)
    session_factory = create_session_factory(version_editor_app.config["DATABASE_URL"])
    service = FormVersionService()

    with session_factory() as db:
        form = db.get(Form, seeded["form_id"])
        v1 = db.get(FormVersion, seeded["v1_id"])
        v2 = db.get(FormVersion, seeded["v2_id"])
        v3 = service.clone_to_draft(db, form, v2, actor_id=None, bump="minor")

        assert v3.id not in {v1.id, v2.id}
        assert v3.source_version_id == v2.id
        assert v3.status == FORM_VERSION_DRAFT
        assert v3.definition_json == seeded["v2_definition"]
        assert [field["name"] for field in v3.definition_json["fields"]] == ["a", "c", "d"]
        assert v3.definition_json["fields"][0]["availability"] == _availability(required=False)

        changed_v3 = deepcopy(v3.definition_json)
        changed_v3["fields"][0]["availability"][0]["required"] = True
        changed_v3["fields"].append(
            {"name": "e", "label": "E", "type": "text", "required": False}
        )
        service.update_definition(v3, changed_v3)
        db.flush()

        assert v1.status == FORM_VERSION_ARCHIVED
        assert v1.definition_json == seeded["v1_definition"]
        assert v2.status == FORM_VERSION_PUBLISHED
        assert v2.definition_json == seeded["v2_definition"]
        submission = db.get(FormSubmission, seeded["submission_id"])
        assert submission.form_version_id == v1.id


def test_clone_redirect_and_editors_keep_exact_draft_id_and_rehydrate_fields(version_editor_app):
    admin_id = _create_admin(version_editor_app)
    seeded = _seed_v1_v2(version_editor_app, user_id=admin_id)
    client = version_editor_app.test_client()
    csrf_token = _login(client)

    clone_response = client.post(
        f'/admin/forms/{seeded["form_id"]}/versions/{seeded["v2_id"]}/clone',
        data={"csrf_token": csrf_token, "bump": "minor"},
    )
    assert clone_response.status_code == 302

    session_factory = create_session_factory(version_editor_app.config["DATABASE_URL"])
    with session_factory() as db:
        v3 = db.execute(
            select(FormVersion).where(
                FormVersion.form_id == seeded["form_id"],
                FormVersion.status == FORM_VERSION_DRAFT,
            )
        ).scalar_one()
        v3_id = v3.id
        assert v3.source_version_id == seeded["v2_id"]

    assert f"form_version_id={v3_id}" in clone_response.headers["Location"]
    edit_response = client.get(clone_response.headers["Location"])
    edit_html = edit_response.get_data(as_text=True)
    assert edit_response.status_code == 200
    assert f'data-editor-form-version="{v3_id}"' in edit_html
    assert f'name="form_version_id" value="{v3_id}"' in edit_html
    assert "Edytujesz wersję 1.2" in edit_html

    with session_factory() as db:
        form = db.get(Form, seeded["form_id"])
        FormVersionService().apply_to_legacy_editor(db, form, seeded["v1_definition"])
        db.commit()

    fields_response = client.get(
        f'/admin/forms/{seeded["form_id"]}/fields?form_version_id={v3_id}'
    )
    fields_html = fields_response.get_data(as_text=True)
    assert fields_response.status_code == 200
    assert f'data-editor-form-version="{v3_id}"' in fields_html
    assert f'name="form_version_id" value="{v3_id}"' in fields_html
    builder_fields = _builder_state(fields_html)
    fields_by_name = {field["name"]: field for field in builder_fields if field.get("name")}
    assert set(fields_by_name) == {"a", "c", "d"}
    assert fields_by_name["a"]["availability"] == _availability(required=False)
    assert fields_by_name["d"]["availability"] == _availability(required=False)


def test_published_version_cannot_be_edited_directly(version_editor_app):
    admin_id = _create_admin(version_editor_app)
    seeded = _seed_v1_v2(version_editor_app, user_id=admin_id)
    client = version_editor_app.test_client()
    csrf_token = _login(client)
    session_factory = create_session_factory(version_editor_app.config["DATABASE_URL"])

    with session_factory() as db:
        before_v1 = deepcopy(db.get(FormVersion, seeded["v1_id"]).definition_json)
        before_v2 = deepcopy(db.get(FormVersion, seeded["v2_id"]).definition_json)

    edit_response = client.post(
        f'/admin/forms/{seeded["form_id"]}/edit',
        data={
            "csrf_token": csrf_token,
            "form_version_id": str(seeded["v2_id"]),
            "active_tab": "basic",
        },
    )
    fields_response = client.post(
        f'/admin/forms/{seeded["form_id"]}/fields',
        data={
            "csrf_token": csrf_token,
            "form_version_id": str(seeded["v2_id"]),
            "action": "builder_save",
            "builder_state": "[]",
        },
    )
    assert edit_response.status_code == 409
    assert fields_response.status_code == 409

    with session_factory() as db:
        v1 = db.get(FormVersion, seeded["v1_id"])
        v2 = db.get(FormVersion, seeded["v2_id"])
        submission = db.get(FormSubmission, seeded["submission_id"])
        assert v1.definition_json == before_v1
        assert v2.definition_json == before_v2
        assert submission.form_version_id == v1.id
