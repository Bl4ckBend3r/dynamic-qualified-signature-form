"""Draft editor state must not become public form or training rules."""
from copy import deepcopy
from io import BytesIO
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from models import Form, FormSubmission, FormVersion, SubmissionTraining
from routes import documents as document_routes
from routes.admin.training_management import _save_training_catalog_with_draft
from services.form_version_service import FormVersionService
from services.training_catalog_service import TrainingCatalogService
from test_admin_panel import (admin_app, admin_csrf, create_form, create_user,
                              grant_rbac, login, _inline_training_payload)


def _definition(title="Published", *, training_name="Published training"):
    return {
        "title": title,
        "fields": [{"name": "email", "label": "Email", "type": "email", "required": True}],
        "workflow": {"initial_step": "submission", "steps": [{"id": "submission"}]},
        "documents": [{"id": "declaration", "kind": "generated_pdf", "enabled": False,
                       "template_html": "<p>Declaration</p>", "fields": [{
            "type": "training_selection", "name": "selected_trainings", "enabled": True,
            "currency": "PLN", "max_total_amount": "5000", "required": False,
            "catalog": [{"id": "course-a", "name": training_name, "price": "100",
                         "capacity": 10, "active": True}],
        }]}],
    }


@pytest.fixture
def versioned_case(admin_app):
    slug = f"isolation-{uuid4().hex[:8]}"
    form_id = create_form(admin_app, slug=slug, definition_json=_definition(),
                          training_selection_open=True)
    services = admin_app.extensions["services"]
    with services.submission_repository.session_factory() as db:
        version = FormVersion(form_id=form_id, version_major=1, version_minor=0,
                              version_label="1.0", status="published",
                              definition_json=_definition())
        db.add(version)
        db.flush()
        submission = FormSubmission(submission_id=f"submission-{uuid4().hex}", form_slug=slug,
                                    form_version_id=version.id, access_token=uuid4().hex)
        db.add(submission)
        db.commit()
        version_id, submission_id = version.id, submission.submission_id
    return admin_app, services, form_id, slug, version_id, submission_id


def test_draft_creation_and_edit_keep_public_form_catalog_and_recruitment_open(versioned_case):
    app, services, form_id, slug, published_id, submission_id = versioned_case
    client = app.test_client()
    assert client.get(f"/form/{slug}").status_code == 200
    with app.app_context(), services.submission_repository.session_factory() as db:
        form = db.get(Form, form_id)
        published = db.get(FormVersion, published_id)
        draft = FormVersionService().clone_to_draft(db, form, published, actor_id=None)
        assert form.training_selection_open is True
        changed = deepcopy(draft.definition_json)
        changed["title"] = "Draft only"
        changed_field = TrainingCatalogService.get_training_field(changed)
        changed_field["max_total_amount"] = "1"
        changed_field["catalog"] = [{"id": "draft-course", "name": "Draft course", "price": "1"}]
        TrainingCatalogService._replace_training_field(changed, changed_field)
        FormVersionService().update_definition(draft, changed)
        FormVersionService().materialize_editor_mirror(db, form, draft)
        draft_id = draft.id
        db.commit()
        assert form.training_selection_open is True
        assert form.definition_json["title"] == "Draft only"  # editor mirror
        assert [item["id"] for item in TrainingCatalogService.get_training_field(form)["catalog"]] == ["course-a"]
        assert published.definition_json["title"] == "Published"
        assert FormVersionService().resolve_published(db, form_id).id == published_id
        submission = db.execute(select(FormSubmission).where(FormSubmission.submission_id == submission_id)).scalar_one()
        public = FormVersionService().public_definition_for_submission(db, form, submission)
        assert public["title"] == "Published"
        field = services.submission_training_service.selection_field(form, public)
        assert field["max_total_amount"] == "5000"
        assert [item["id"] for item in field["catalog"]] == ["course-a"]
        assert services.submission_training_service.open_selection_stage(db, form, submission)
        access_token = submission.access_token
        db.commit()
    response = client.get(f"/form/{slug}")
    assert response.status_code == 200
    assert "Draft only" not in response.text
    training_page = client.get(f"/submissions/{submission_id}/trainings?token={access_token}")
    assert training_page.status_code == 200
    assert "Published training" in training_page.text and "Draft course" not in training_page.text
    create_user(app)
    login(client)
    editor = client.get(f"/admin/forms/{form_id}/edit?form_version_id={draft_id}")
    assert editor.status_code == 200
    with services.submission_repository.session_factory() as db:
        assert db.get(Form, form_id).training_selection_open is True
    assert client.get(f"/submissions/{submission_id}/trainings?token={access_token}").status_code == 200


def test_publication_switches_new_forms_but_keeps_historical_version(versioned_case):
    app, services, form_id, slug, old_id, submission_id = versioned_case
    with app.app_context(), services.submission_repository.session_factory() as db:
        form = db.get(Form, form_id)
        old = db.get(FormVersion, old_id)
        draft = FormVersionService().clone_to_draft(db, form, old, actor_id=None)
        changed = deepcopy(draft.definition_json)
        changed["title"] = "New published"
        FormVersionService().update_definition(draft, changed)
        FormVersionService().publish(db, form, draft, actor_id=None)
        db.commit()
        assert old.status == "archived" and draft.status == "published"
        assert FormVersionService().resolve_published(db, form_id).id == draft.id
        historical = db.execute(select(FormSubmission).where(FormSubmission.submission_id == submission_id)).scalar_one()
        assert FormVersionService().public_definition_for_submission(db, form, historical)["title"] == "Published"
        unbound = FormSubmission(submission_id=f"unbound-{uuid4().hex}", form_slug=slug,
                                 access_token=uuid4().hex)
        db.add(unbound)
        db.flush()
        assert FormVersionService().public_definition_for_submission(db, form, unbound) is None
        unbound_id = unbound.submission_id
        db.commit()
    with app.app_context():
        assert document_routes.get_form_config(slug, submission_id)["title"] == "Published"
        assert document_routes.get_form_config(slug, unbound_id) is None
    page = app.test_client().get(f"/form/{slug}")
    assert page.status_code == 200 and "New published" in page.text


def test_operational_catalog_changes_without_draft_or_version_publication(versioned_case):
    app, services, form_id, _slug, published_id, submission_id = versioned_case
    with app.app_context(), services.submission_repository.session_factory() as db:
        form = db.get(Form, form_id)
        updated = deepcopy(form.definition_json)
        field = TrainingCatalogService.get_training_field(updated)
        field["catalog"] = [{"id": "course-b", "name": "Operational", "price": "200", "active": True}]
        TrainingCatalogService._replace_training_field(updated, field)
        _save_training_catalog_with_draft(db, form, updated)
        db.commit()
        assert [item["id"] for item in TrainingCatalogService.get_training_field(form)["catalog"]] == ["course-b"]
        published = db.get(FormVersion, published_id)
        assert published.status == "published"
        assert [item["id"] for item in TrainingCatalogService.get_training_field(published.definition_json)["catalog"]] == ["course-a"]
        historical = db.execute(select(FormSubmission).where(FormSubmission.submission_id == submission_id)).scalar_one()
        effective = FormVersionService().public_definition_for_submission(db, form, historical)
        merged = services.submission_training_service.selection_field(form, effective)
        assert [item["id"] for item in merged["catalog"]] == ["course-b"]
        assert merged["max_total_amount"] == "5000"


def test_removing_training_field_from_draft_keeps_operational_catalog(versioned_case):
    app, services, form_id, _slug, published_id, submission_id = versioned_case
    with app.app_context(), services.submission_repository.session_factory() as db:
        form = db.get(Form, form_id)
        published = db.get(FormVersion, published_id)
        draft = FormVersionService().clone_to_draft(db, form, published, actor_id=None)
        changed = deepcopy(draft.definition_json)
        changed["documents"][0]["fields"] = []
        FormVersionService().update_definition(draft, changed)
        FormVersionService().materialize_editor_mirror(db, form, draft)
        db.commit()
        assert TrainingCatalogService.get_training_field(draft.definition_json) is None
        assert [item["id"] for item in TrainingCatalogService.get_training_field(form)["catalog"]] == ["course-a"]
        historical = db.execute(select(FormSubmission).where(FormSubmission.submission_id == submission_id)).scalar_one()
        assert FormVersionService().public_definition_for_submission(db, form, historical)["title"] == "Published"


def test_catalog_permissions_and_archived_training_keep_historical_participation(versioned_case):
    app, services, form_id, slug, published_id, submission_id = versioned_case
    manager_email = f"manager-{uuid4().hex[:8]}@example.org"
    manager_id = create_user(app, email=manager_email, role="form_manager")
    grant_rbac(app, manager_id, ["can_view_submissions"], form_id=form_id)
    client = app.test_client()
    login(client, email=manager_email)
    add_url = f"/admin/forms/{form_id}/training-management/catalog"
    payload = _inline_training_payload(
        csrf_token=admin_csrf(client), training_item_id="course-b",
        training_item_name="Operational course", training_item_active="1",
    )
    assert client.post(add_url, data=payload).status_code == 403
    grant_rbac(app, manager_id, ["can_edit_form"], form_id=form_id)
    assert client.post(add_url, data=payload).status_code == 302
    with services.submission_repository.session_factory() as db:
        submission = db.execute(select(FormSubmission).where(FormSubmission.submission_id == submission_id)).scalar_one()
        db.add(SubmissionTraining(submission_id=submission.id, training_id="course-a",
                                  training_name_snapshot="Published training",
                                  training_price_snapshot="100", status="selected"))
        db.commit()
    delete_url = f"/admin/forms/{form_id}/training-management/course-a/delete"
    assert client.post(delete_url, data={"csrf_token": admin_csrf(client),
                                         "training_item_change_reason": "Archive"}).status_code == 302
    with services.submission_repository.session_factory() as db:
        form = db.get(Form, form_id)
        live = TrainingCatalogService.get_training_field(form)["catalog"]
        assert {item["id"] for item in live} == {"course-a", "course-b"}
        assert next(item for item in live if item["id"] == "course-a")["archived"] is True
        version = db.get(FormVersion, published_id)
        assert version.status == "published"
        assert [item["id"] for item in TrainingCatalogService.get_training_field(version.definition_json)["catalog"]] == ["course-a"]
        row = db.execute(select(SubmissionTraining).where(SubmissionTraining.training_id == "course-a")).scalar_one()
        assert row.training_name_snapshot == "Published training" and row.status == "selected"


def test_document_generation_uses_submission_version_not_editor_draft(versioned_case, monkeypatch):
    app, services, form_id, _slug, published_id, submission_id = versioned_case
    with app.app_context(), services.submission_repository.session_factory() as db:
        form = db.get(Form, form_id)
        published = db.get(FormVersion, published_id)
        old_definition = deepcopy(published.definition_json)
        old_definition["documents"].append({"id": "agreement", "kind": "generated_pdf",
                                            "template_html": "<p>Published agreement</p>",
                                            "enabled": True})
        published.definition_json = deepcopy(old_definition)
        form.definition_json = deepcopy(old_definition)
        submission = db.execute(select(FormSubmission).where(FormSubmission.submission_id == submission_id)).scalar_one()
        submission.data_json = {"email": "test@example.org"}
        draft = FormVersionService().clone_to_draft(db, form, published, actor_id=None)
        changed = deepcopy(draft.definition_json)
        next(item for item in changed["documents"] if item["id"] == "agreement")["template_html"] = "<p>Draft agreement</p>"
        FormVersionService().update_definition(draft, changed)
        FormVersionService().materialize_editor_mirror(db, form, draft)
        db.commit()
    templates = []
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(output)
    monkeypatch.setattr(services.document_service.pdf_render_service, "render_document_pdf_bytes",
                        lambda **kwargs: templates.append(kwargs["template_html"]) or output.getvalue())
    with app.app_context():
        row = services.submission_repository.get_by_id(submission_id)
        definition = services.workflow_service.definition_for(row)
        assert next(item for item in definition["documents"] if item["id"] == "agreement")["template_html"] == "<p>Published agreement</p>"
        result = services.document_service.generate_document(row, definition, "agreement")
        assert result["created"] is True
        assert templates == ["<p>Published agreement</p>"]
