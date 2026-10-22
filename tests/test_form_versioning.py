from __future__ import annotations

from copy import deepcopy
import importlib
import re
from types import SimpleNamespace

import pytest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import Blueprint, Flask
from sqlalchemy import Column, Integer, JSON, MetaData, String, Table, Text, create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from models import Base, Form, FormField, FormSubmission, FormVersion, SubmissionFile, SubmissionTraining, SubmissionWorkflowEvent
from routes import documents as document_routes
from routes import public_forms
from routes.participant_access import resolve_participant_submission_access
from services.form_config_service import FormConfigService
from services.form_version_service import (
    FORM_VERSION_ARCHIVED,
    FORM_VERSION_DRAFT,
    FORM_VERSION_PUBLISHED,
    FormVersionError,
    FormVersionService,
    FormVersionValidationError,
)
from services.training_catalog_service import TrainingCatalogService


def _definition(field_name: str = "email", title: str = "Formularz") -> dict:
    return {
        "title": title,
        "fields": [
            {
                "name": field_name,
                "label": field_name.title(),
                "type": "email" if field_name == "email" else "text",
                "required": True,
            }
        ],
        "workflow": {"initial_step": "submission", "steps": [{"id": "submission"}]},
    }


def _database(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'versions.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    return database_url, sessionmaker(bind=engine, expire_on_commit=False)


def _form_with_field(db, *, slug="versioned") -> Form:
    form = Form(
        slug=slug,
        name="Formularz",
        title="Formularz",
        description="",
        definition_json=_definition(),
        is_active=True,
        is_public=True,
    )
    db.add(form)
    db.flush()
    db.add(
        FormField(
            form_id=form.id,
            name="email",
            label="Email",
            type="email",
            required=True,
            options=[],
            default_value="",
            section="",
            stage="initial_submission",
            sort_order=1,
            active=True,
        )
    )
    db.flush()
    return form


def _seed_active_versioned_submission(db, service, *, status="SUBMITTED"):
    form = _form_with_field(db)
    first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
    submission = FormSubmission(
        submission_id=f"submission-{status.lower()}", form_slug=form.slug,
        form_version_id=first.id, data_json={"email": "user@example.org", "legacy_note": "zachowaj"},
        email="user@example.org", process_status=status, workflow_step="submission",
    )
    db.add(submission)
    db.flush()
    return form, first, submission


def test_publish_upgrades_active_submissions_and_records_only_metadata(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form, old, submission = _seed_active_versioned_submission(db, service)
        draft = service.clone_to_draft(db, form, old, actor_id=None)
        changed = deepcopy(draft.definition_json)
        changed["fields"].append({"name": "city", "label": "Miasto", "type": "text", "required": True})
        service.update_definition(draft, changed)
        service.publish(db, form, draft, actor_id=None)
        submission_id, old_id, new_id = submission.id, old.id, draft.id
    with Session() as db:
        migrated = db.get(FormSubmission, submission_id)
        assert migrated.form_version_id == new_id
        assert migrated.data_json["email"] == "user@example.org"
        assert migrated.data_json["legacy_note"] == "zachowaj"
        assert migrated.data_json["_form_version_upgrade"]["missing_fields"] == ["city"]
        assert [field["name"] for field in db.get(FormVersion, old_id).definition_json["fields"]] == ["email"]
        event = db.execute(select(SubmissionWorkflowEvent).where(SubmissionWorkflowEvent.submission_id == submission_id)).scalar_one()
        assert event.source == "form_version_upgrade"
        assert event.side_effects["old_version_id"] == old_id
        assert event.side_effects["new_version_id"] == new_id
        assert "user@example.org" not in str(event.side_effects)
        with pytest.raises(FormVersionError):
            service.require_submission_data_complete(db, migrated)
        data = dict(migrated.data_json)
        data["city"] = "Zielona Góra"
        migrated.data_json = data
        assert service.refresh_submission_completion(db, migrated)["complete"]


@pytest.mark.parametrize("current_step", ["paper", "review"])
def test_new_required_document_reconciles_current_or_completed_step(tmp_path, current_step):
    # Given: a versioned submission with an existing document step. The new
    # version adds a required, unsigned participant attachment to that step.
    _url, Session = _database(tmp_path)
    workflow = {"flow_mode": "explicit", "schema_version": 2, "initial_step": "paper", "steps": [
        {"id": "paper", "stage_type": "document", "type": "document", "document_lifecycle": "composite",
         "document_id": "declaration", "status": "DECLARATION_WAITING_FOR_SIGNATURE", "next": "review"},
        {"id": "review", "stage_type": "decision", "type": "manual_decision", "status": "WAITING_FOR_OFFICER_DECISION"}]}
    old_definition = {"title": "Test", "fields": [], "workflow": workflow,
        "documents": [{"id": "declaration", "label": "Deklaracja", "kind": "uploaded_file",
            "required": True, "signature_required": True}]}
    new_definition = deepcopy(old_definition)
    new_definition["documents"].append({"id": "employment_certificate", "label": "Zaświadczenie",
        "kind": "uploaded_file", "required": True, "signature_required": False, "workflow_step": "paper"})
    service = FormVersionService()
    with Session.begin() as db:
        form = Form(slug="new-doc-version", name="Test", title="Test", definition_json=old_definition)
        db.add(form)
        db.flush()
        old = FormVersion(form_id=form.id, version_major=1, version_minor=0, version_label="1.0",
            status=FORM_VERSION_ARCHIVED, definition_json=old_definition)
        new = FormVersion(form_id=form.id, version_major=1, version_minor=1, version_label="1.1",
            status=FORM_VERSION_PUBLISHED, definition_json=new_definition)
        db.add_all([old, new])
        db.flush()
        submission = FormSubmission(submission_id=f"version-{current_step}", form_slug=form.slug,
            form_version_id=old.id, data_json={}, process_status="WAITING_FOR_OFFICER_DECISION",
            workflow_step=current_step, workflow_stage=current_step,
            document_states={"workflow_documents": {"paper": {"completed": True, "document_id": "declaration"}}})
        db.add(submission)
        db.flush()
        if current_step == "review":
            db.add(SubmissionWorkflowEvent(submission_id=submission.id, public_submission_id=submission.submission_id,
                form_slug=form.slug, previous_step="paper", new_step="review", source="test"))
        assert service.upgrade_submission(db, submission, new)
        marker = submission.data_json["_form_version_upgrade"]
        if current_step == "paper":
            assert not submission.document_states["workflow_documents"]["paper"]["completed"]
            assert marker["missing_documents"] == []
        else:
            assert submission.workflow_step == submission.workflow_stage == "paper"
            assert submission.process_status == "DECLARATION_WAITING_FOR_SIGNATURE"
            assert not submission.document_states["workflow_documents"]["paper"]["completed"]
            assert marker["missing_documents"] == []


@pytest.mark.parametrize("remove_old", [False, True])
@pytest.mark.parametrize("stage_type", ["documents", "document"])
def test_participant_document_version_reconcile_preserves_signed_history(tmp_path, remove_old, stage_type):
    # Given: an active submission already passed a participant stage with a
    # signed custom document; a new version adds or removes a requirement.
    _url, Session = _database(tmp_path)
    workflow = {"flow_mode": "explicit", "schema_version": 2, "initial_step": "paper", "steps": [
        {"id": "paper", "stage_type": stage_type, "type": "document",
         "document_lifecycle": "group",
         "status": "WAITING_FOR_USER_ACTION", "next": "review"},
        {"id": "review", "stage_type": "decision", "type": "manual_decision",
         "status": "WAITING_FOR_OFFICER_DECISION"}]}
    signed_document = {"id": "old_statement", "label": "Stare oświadczenie", "kind": "uploaded_file",
        "workflow_step": "paper", "required": True, "signature_required": True}
    old_definition = {"title": "Test", "fields": [], "workflow": workflow,
        "documents": [signed_document]}
    new_definition = deepcopy(old_definition)
    if remove_old:
        new_definition["documents"] = []
    else:
        new_definition["documents"].append({"id": "new_statement", "label": "Nowe oświadczenie",
            "kind": "uploaded_file", "workflow_step": "paper", "required": True,
            "signature_required": True})
    with Session.begin() as db:
        form = Form(slug=f"reconcile-{'removed' if remove_old else 'added'}", name="Test",
            title="Test", definition_json=old_definition)
        db.add(form); db.flush()
        old = FormVersion(form_id=form.id, version_major=1, version_minor=0, version_label="1.0",
            status=FORM_VERSION_ARCHIVED, definition_json=old_definition)
        latest = FormVersion(form_id=form.id, version_major=1, version_minor=1, version_label="1.1",
            status=FORM_VERSION_PUBLISHED, definition_json=new_definition)
        db.add_all([old, latest]); db.flush()
        submission = FormSubmission(submission_id="versioned-doc", form_slug=form.slug,
            form_version_id=old.id, data_json={"participant_note": "zachowaj"},
            process_status="WAITING_FOR_OFFICER_DECISION", workflow_step="review", workflow_stage="review",
            document_states={"workflow_documents": {"paper": {"completed": True,
                "documents": {"old_statement": {"completed": True, "uploaded_filename": "old.pdf"}}}}})
        db.add(submission); db.flush()
        historical = SubmissionFile(submission_id=submission.id, public_submission_id=submission.submission_id,
            form_slug=form.slug, document_id="old_statement", document_type="signed_old_statement",
            filename="old.pdf", storage_path="output/old.pdf", workflow_step_at_upload="paper",
            signed=True, status="signed")
        db.add(historical)
        db.add(SubmissionWorkflowEvent(submission_id=submission.id,
            public_submission_id=submission.submission_id, form_slug=form.slug,
            previous_step="paper", new_step="review", source="test"))
        db.flush()
        # When: the shared FormVersion reconcile upgrades the submission.
        assert FormVersionService().upgrade_submission(db, submission, latest)
        # Expected: only a new required document rewinds the active workflow;
        # the historical signed file and answers are preserved in both cases.
        assert submission.workflow_step == ("review" if remove_old else "paper")
        assert submission.process_status == ("WAITING_FOR_OFFICER_DECISION" if remove_old else "WAITING_FOR_USER_ACTION")
        assert submission.data_json["participant_note"] == "zachowaj"
        assert historical.status == "signed" and historical.signed
        if not remove_old:
            assert not submission.document_states["workflow_documents"]["paper"]["completed"]


def test_publish_skips_final_submission_and_requires_explicit_step_mapping(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form, old, active = _seed_active_versioned_submission(db, service)
        final = FormSubmission(
            submission_id="finished", form_slug=form.slug, form_version_id=old.id,
            data_json={"email": "final@example.org"}, process_status="COMPLETED", workflow_step="submission",
        )
        db.add(final)
        draft = service.clone_to_draft(db, form, old, actor_id=None)
        changed = deepcopy(draft.definition_json)
        changed["workflow"] = {"initial_step": "review", "steps": [{"id": "review"}]}
        service.update_definition(draft, changed)
        old.status = FORM_VERSION_ARCHIVED
        db.flush()
        draft.status = FORM_VERSION_PUBLISHED
        with pytest.raises(FormVersionValidationError) as error:
            service.upgrade_submission(db, active, draft)
        assert "usuniętym etapie" in error.value.errors[0]
        changed["workflow"]["step_migration"] = {"submission": "review"}
        draft.definition_json = changed
        service.upgrade_submission(db, active, draft)
        assert active.form_version_id == draft.id
        assert active.workflow_step == "review"
        assert final.form_version_id == old.id


def test_publish_preserves_signed_file_and_supersedes_unsigned_when_template_changes(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form, old, submission = _seed_active_versioned_submission(db, service)
        for document_type, signed in (("declaration", False), ("signed_declaration", True)):
            db.add(SubmissionFile(
                submission_id=submission.id, public_submission_id=submission.submission_id,
                form_slug=form.slug, document_id=document_type, document_type=document_type,
                filename=f"{document_type}.pdf", storage_path=f"test/{document_type}.pdf",
                signed=signed, status="uploaded",
            ))
        draft = service.clone_to_draft(db, form, old, actor_id=None)
        changed = deepcopy(draft.definition_json)
        changed["workflow"]["declaration_template_html"] = "<p>New</p>"
        draft.definition_json = changed
        old.status = FORM_VERSION_ARCHIVED
        db.flush()
        draft.status = FORM_VERSION_PUBLISHED
        service.upgrade_submission(db, submission, draft)
        files = db.execute(select(SubmissionFile).where(SubmissionFile.submission_id == submission.id)).scalars().all()
        assert {item.document_type: item.status for item in files} == {
            "declaration": "superseded", "signed_declaration": "uploaded",
        }


def test_reconcile_keeps_removed_values_but_flags_new_option_and_required_field(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form, old, submission = _seed_active_versioned_submission(db, service)
        data = dict(submission.data_json)
        data.update({"choice": "A", "removed": "historical"})
        submission.data_json = data
        draft = service.clone_to_draft(db, form, old, actor_id=None)
        changed = deepcopy(draft.definition_json)
        changed["fields"].extend([
            {"name": "choice", "label": "Wybór", "type": "select", "options": ["B", "C"], "required": True},
            {"name": "telephone", "label": "Telefon", "type": "tel", "required": True, "default": "123456789"},
        ])
        draft.definition_json = changed
        old.status = FORM_VERSION_ARCHIVED
        db.flush()
        draft.status = FORM_VERSION_PUBLISHED
        service.upgrade_submission(db, submission, draft)
        marker = submission.data_json["_form_version_upgrade"]
        assert marker["missing_fields"] == ["choice", "telephone"]
        assert submission.data_json["choice"] == "A"
        assert submission.data_json["removed"] == "historical"
        assert "telephone" not in submission.data_json
        assert "removed" not in [field.get("name") for field in service.completion_form_definition(submission, changed)["fields"]]


def test_publish_leaves_final_submission_on_historical_version(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form, old, active = _seed_active_versioned_submission(db, service)
        final = FormSubmission(
            submission_id="final-submission", form_slug=form.slug, form_version_id=old.id,
            data_json={"email": "final@example.org"}, process_status="COMPLETED", workflow_step="submission",
        )
        db.add(final)
        draft = service.clone_to_draft(db, form, old, actor_id=None)
        service.publish(db, form, draft, actor_id=None)
        assert active.form_version_id == draft.id
        assert final.form_version_id == old.id
        assert final.workflow_step == "submission"


def test_correction_uses_bound_published_qualification_while_draft_changes(tmp_path):
    from services.qualification_condition_service import QualificationConditionService
    from services.submission_correction_service import SubmissionCorrectionService

    _url, Session = _database(tmp_path)
    versions = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db, slug="versioned-qualification")
        published_definition = deepcopy(form.definition_json)
        published_definition["qualification_conditions"] = {"enabled": True, "conditions": [{
            "id": "email-rule", "field_name": "email", "operator": "equals",
            "expected_value": "accepted@example.org", "failure_action": "auto_reject", "is_active": True,
        }]}
        form.definition_json = published_definition
        published = versions.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        draft = versions.clone_to_draft(db, form, published, actor_id=None)
        draft_definition = deepcopy(draft.definition_json)
        draft_definition["qualification_conditions"]["conditions"][0]["expected_value"] = "draft@example.org"
        draft.definition_json = draft_definition
        submission = FormSubmission(
            submission_id="versioned-correction", form_slug=form.slug, form_version_id=published.id,
            process_status="AUTO_REJECTED", workflow_step="auto_rejected",
            data_json={"email": "draft@example.org"}, email="draft@example.org",
        )
        db.add(submission)
        db.flush()
        bound_definition = versions.resolve_for_submission(db, submission).definition_json
        assert QualificationConditionService().evaluate(
            bound_definition["qualification_conditions"], {"email": "draft@example.org"}, bound_definition["fields"],
        )["passed"] is False
        SubmissionCorrectionService().return_for_correction(
            db, submission, form_config=bound_definition, reason="Popraw adres", message_to_user="",
            clear_submission=False, actor=SimpleNamespace(id=None, email="admin@example.org", role="admin"),
        )
        assert submission.form_version_id == published.id
        assert published.definition_json["qualification_conditions"]["conditions"][0]["expected_value"] == "accepted@example.org"
        assert draft.definition_json["qualification_conditions"]["conditions"][0]["expected_value"] == "draft@example.org"


def test_document_name_stays_with_historical_form_version(tmp_path, monkeypatch):
    database_url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db, slug="named-statement")
        original = deepcopy(form.definition_json)
        original["workflow"] = {"flow_mode": "explicit", "schema_version": 2,
            "initial_step": "submission", "steps": [
                {"id": "submission", "stage_type": "user_action", "type": "form_submit",
                 "admin_label": "Zgłoszenie", "user_label": "Zgłoszenie",
                 "status": "FORM_SUBMITTED", "next": "paper"},
                {"id": "paper", "stage_type": "documents", "type": "document",
                 "admin_label": "Oświadczenia", "user_label": "Oświadczenia",
                 "document_lifecycle": "group", "status": "WAITING_FOR_USER_ACTION", "next": "done"},
                {"id": "done", "stage_type": "final", "type": "end", "final": True,
                 "admin_label": "Koniec", "user_label": "Koniec",
                 "status": "COMPLETED"}]}
        original["documents"] = [{"id": "statement", "label": "Oświadczenia",
            "participant_label": "Oświadczenie uczestnika", "kind": "uploaded_file",
            "workflow_step": "paper", "required": True, "signature_required": False}]
        form.definition_json = original
        first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        historical = FormSubmission(submission_id="finished-statement", form_slug=form.slug,
            form_version_id=first.id, process_status="COMPLETED", workflow_step="done")
        db.add(historical)
        draft = service.clone_to_draft(db, form, first, actor_id=None)
        changed = deepcopy(draft.definition_json)
        changed["documents"][0]["participant_label"] = "Oświadczenie o spełnianiu kryteriów udziału"
        from services.admin_form_service import validate_admin_form_config
        assert not (errors := validate_admin_form_config(changed)), errors
        service.update_definition(draft, changed)
        service.publish(db, form, draft, actor_id=None)
        old_id, new_id = first.id, draft.id

    app = Flask(__name__)
    app.config.update(DATABASE_URL=database_url)
    app.extensions["services"] = SimpleNamespace(
        form_version_service=service, form_config_service=FormConfigService())
    monkeypatch.setattr(document_routes, "create_session_factory", lambda _url: Session)
    with app.app_context():
        old_config = document_routes.get_form_config("named-statement", "finished-statement")
        new_config = document_routes.get_form_config("named-statement")
    with Session() as db:
        assert db.execute(select(FormSubmission).where(
            FormSubmission.submission_id == "finished-statement")).scalar_one().form_version_id == old_id
        assert db.get(FormVersion, old_id).definition_json["documents"][0]["participant_label"] == "Oświadczenie uczestnika"
        assert db.get(FormVersion, new_id).definition_json["documents"][0]["id"] == "statement"
    assert old_config["documents"][0]["participant_label"] == "Oświadczenie uczestnika"
    assert new_config["documents"][0]["participant_label"] == "Oświadczenie o spełnianiu kryteriów udziału"
    legacy = FormConfigService().normalize_form_config({"process": {"documents": [
        {"id": "statement", "label": "Oświadczenia"}]}})
    assert legacy["documents"][0]["label"] == "Oświadczenia"
    assert not legacy["documents"][0].get("participant_label")
    cleared = FormConfigService().normalize_form_config({
        "process": {"documents": [{"id": "statement", "label": "Oświadczenia",
            "participant_label": "Stara nazwa"}]},
        "documents": [{"id": "statement", "participant_label": ""}],
    })
    assert cleared["documents"][0]["participant_label"] == ""


def test_participant_access_lazily_upgrades_missed_active_submission(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form, old, submission = _seed_active_versioned_submission(db, service)
        submission.access_token = "secret"
        draft = service.clone_to_draft(db, form, old, actor_id=None)
        old.status = FORM_VERSION_ARCHIVED
        db.flush()
        draft.status = FORM_VERSION_PUBLISHED
        newer_id = draft.id

    class SubmissionServiceStub:
        def get_submission_context(self, submission_id, **_kwargs):
            with Session() as db:
                row = db.execute(select(FormSubmission).where(FormSubmission.submission_id == submission_id)).scalar_one()
                return {"submission_id": row.submission_id, "form_slug": row.form_slug, "row": {
                    "submission_id": row.submission_id, "form_slug": row.form_slug,
                    "access_token": row.access_token, "data_json": row.data_json,
                }}

    app = Flask(__name__)
    app.config.update(DATABASE_URL="sqlite:///test", SECRET_KEY="secret")
    app.extensions["services"] = SimpleNamespace(
        submission_service=SubmissionServiceStub(),
        submission_repository=SimpleNamespace(session_factory=Session),
        form_version_service=service, form_config_service=None, storage=None,
        access_token_service=SimpleNamespace(verify_required_token=lambda row, token: row["access_token"] == token),
    )
    with app.test_request_context("/test?token=secret"):
        access = resolve_participant_submission_access("submission-submitted", slug="versioned")
        assert access is not None
        assert access.submission["row"]["data_json"]["_form_version_upgrade"]["new_version_id"] == newer_id
    with Session() as db:
        row = db.execute(select(FormSubmission).where(FormSubmission.submission_id == "submission-submitted")).scalar_one()
        assert row.form_version_id == newer_id


def test_required_field_at_future_step_does_not_block_current_step(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form, old, submission = _seed_active_versioned_submission(db, service)
        draft = service.clone_to_draft(db, form, old, actor_id=None)
        changed = deepcopy(draft.definition_json)
        changed["workflow"]["steps"].append({"id": "later"})
        changed["fields"].append({
            "name": "later_answer", "label": "Późniejsza odpowiedź", "type": "text", "required": True,
            "availability": [{"step": "later", "visible": True, "editable": True, "required": True}],
        })
        draft.definition_json = changed
        old.status = FORM_VERSION_ARCHIVED
        db.flush()
        draft.status = FORM_VERSION_PUBLISHED
        service.upgrade_submission(db, submission, draft)
        assert "later_answer" not in submission.data_json["_form_version_upgrade"]["missing_fields"]
        assert "later_answer" in service.submission_data_errors(submission, changed, step="later")


def test_participant_can_complete_upgrade_with_token_and_csrf(tmp_path, monkeypatch):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form, old, submission = _seed_active_versioned_submission(db, service)
        submission.access_token = "secret"
        draft = service.clone_to_draft(db, form, old, actor_id=None)
        changed = deepcopy(draft.definition_json)
        changed["fields"].append({"name": "city", "label": "Miasto", "type": "text", "required": True})
        service.update_definition(draft, changed)
        service.publish(db, form, draft, actor_id=None)

    class SubmissionServiceStub:
        def get_submission_context(self, submission_id, **_kwargs):
            with Session() as db:
                row = db.execute(select(FormSubmission).where(FormSubmission.submission_id == submission_id)).scalar_one()
                return {"submission_id": row.submission_id, "form_slug": row.form_slug, "row": {
                    "submission_id": row.submission_id, "form_slug": row.form_slug,
                    "access_token": row.access_token, "data_json": row.data_json,
                }}

    app = Flask(__name__, template_folder=str(public_forms.Path(__file__).resolve().parents[1] / "templates"))
    app.config.update(DATABASE_URL="sqlite:///test", SECRET_KEY="secret", TESTING=True)
    app.jinja_env.filters["trusted_html"] = lambda value: value
    app.jinja_env.filters["option_value"] = lambda value: value.get("value", "") if isinstance(value, dict) else value
    app.jinja_env.filters["option_label"] = lambda value: value.get("label", "") if isinstance(value, dict) else value
    @app.context_processor
    def _layout_context():
        return {"app_name": "Test", "app_base_path": "", "site_footer": SimpleNamespace(
            logo_position="top", layout="single", logo_html="", left_html="", right_html="",
            social_left_html="", social_right_html="", social_bottom_html="",
        ), "footer_contact": SimpleNamespace(address="", phones=[], email=""),
            "footer_service_documents": []}
    app.extensions["services"] = SimpleNamespace(
        submission_service=SubmissionServiceStub(),
        submission_repository=SimpleNamespace(session_factory=Session),
        form_version_service=service, form_config_service=None, storage=None,
        submission_attachment_service=SimpleNamespace(validate_uploads=lambda *_args, **_kwargs: ([], {})),
        access_token_service=SimpleNamespace(verify_required_token=lambda row, token: row["access_token"] == token),
    )
    monkeypatch.setattr(public_forms, "db_session_factory", lambda: Session)
    documents = Blueprint("documents", __name__)
    documents.add_url_rule("/do-podpisania", "documents_to_sign", lambda: "documents")
    documents.add_url_rule("/training-selection", "training_selection", lambda: "training")
    app.register_blueprint(documents)
    app.register_blueprint(public_forms.bp)
    client = app.test_client()
    url = "/form/versioned/update/submission-submitted?token=secret"
    page = client.get(url)
    assert page.status_code == 200
    assert "Uzupełnij dane" in page.get_data(as_text=True)
    assert client.post(url, data={"email": "user@example.org", "city": "Zielona Góra"}).status_code == 400
    with client.session_transaction() as session:
        session[public_forms.PUBLIC_CSRF_SESSION_KEY] = "csrf-secret"
    assert client.post(url.replace("secret", "wrong"), data={"csrf_token": "csrf-secret"}).status_code == 404
    response = client.post(url, data={
        "csrf_token": "csrf-secret", "email": "user@example.org", "city": "Zielona Góra",
    })
    assert response.status_code == 302
    assert response.location.startswith("/do-podpisania")
    with Session() as db:
        row = db.execute(select(FormSubmission).where(FormSubmission.submission_id == "submission-submitted")).scalar_one()
        assert row.data_json["city"] == "Zielona Góra"
        assert service.refresh_submission_completion(db, row)["complete"]
        assert db.execute(select(SubmissionWorkflowEvent).where(
            SubmissionWorkflowEvent.submission_id == row.id,
            SubmissionWorkflowEvent.source == "form_version_data_completed",
        )).scalar_one_or_none() is not None


def test_training_upgrade_preserves_valid_and_flags_removed_unlocked_choice(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    definition = _definition()
    definition["documents"] = [{"id": "declaration", "fields": [{
        "type": "training_selection", "name": "selected_trainings", "enabled": True,
        "catalog": [
            {"id": "kept", "name": "Zachowane", "price": "100", "currency": "PLN", "capacity": 20, "active": True},
            {"id": "removed", "name": "Usunięte", "price": "100", "currency": "PLN", "capacity": 20, "active": True},
        ],
    }]}]
    with Session.begin() as db:
        form = _form_with_field(db)
        form.definition_json = definition
        old = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        submission = FormSubmission(
            submission_id="training-upgrade", form_slug=form.slug, form_version_id=old.id,
            data_json={"email": "user@example.org"}, process_status="TRAINING_SELECTION_OPEN", workflow_step="submission",
        )
        db.add(submission)
        db.flush()
        for key in ("kept", "removed"):
            db.add(SubmissionTraining(
                submission_id=submission.id, training_id=key, training_name_snapshot=key,
                training_price_snapshot="100", status="selected",
            ))
        draft = service.clone_to_draft(db, form, old, actor_id=None)
        changed = deepcopy(draft.definition_json)
        changed["documents"][0]["fields"][0]["catalog"] = definition["documents"][0]["fields"][0]["catalog"][:1]
        draft.definition_json = changed
        old.status = FORM_VERSION_ARCHIVED
        db.flush()
        draft.status = FORM_VERSION_PUBLISHED
        service.upgrade_submission(db, submission, draft)
        assert submission.data_json["_form_version_upgrade"]["invalid_training_ids"] == ["removed"]
        assert {row.training_id: row.status for row in db.execute(select(SubmissionTraining).where(
            SubmissionTraining.submission_id == submission.id,
        )).scalars()} == {"kept": "selected", "removed": "selected"}


def test_publish_archives_previous_and_clone_preserves_snapshot(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_DRAFT)
        service.publish(db, form, first, actor_id=None, change_summary="Pierwsza publikacja")
        first_id = first.id

    with Session.begin() as db:
        form = db.execute(select(Form).where(Form.slug == "versioned")).scalar_one()
        first = db.get(FormVersion, first_id)
        draft = service.clone_to_draft(db, form, first, actor_id=None, bump="minor")
        copied = deepcopy(draft.definition_json)
        copied["title"] = "Formularz 1.1"
        copied["fields"][0]["label"] = "Nowy e-mail"
        service.update_definition(draft, copied, change_summary="Zmiana etykiety")
        service.publish(db, form, draft, actor_id=None)
        draft_id = draft.id

    with Session() as db:
        first = db.get(FormVersion, first_id)
        second = db.get(FormVersion, draft_id)
        assert first.status == FORM_VERSION_ARCHIVED
        assert first.definition_json["title"] == "Formularz"
        assert second.status == FORM_VERSION_PUBLISHED
        assert second.version_label == "1.1"
        assert second.source_version_id == first.id
        assert service.resolve_published(db, first.form_id).id == second.id


def test_training_catalog_changes_only_draft_until_publish_and_keeps_stable_id(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    catalog_service = TrainingCatalogService()
    initial_definition = _definition()
    initial_definition["documents"] = [
        {
            "id": "declaration",
            "kind": "generated_pdf",
            "template_html": "<p>Deklaracja</p>",
            "enabled": True,
            "fields": [
                {
                    "type": "training_selection",
                    "name": "selected_trainings",
                    "label": "Wybierz szkolenia",
                    "enabled": True,
                    "required": True,
                    "currency": "PLN",
                    "max_total_amount": "5000.00",
                    "catalog": [
                        {
                            "id": "excel-stable",
                            "name": "Excel",
                            "price": "500.00",
                            "currency": "PLN",
                            "capacity": 20,
                            "active": True,
                            "sort_order": 1,
                        }
                    ],
                }
            ],
        }
    ]

    with Session.begin() as db:
        form = _form_with_field(db, slug="training-versions")
        form.definition_json = deepcopy(initial_definition)
        published = service.create_initial_version(
            db, form, actor_id=None, status=FORM_VERSION_PUBLISHED
        )
        published_id = published.id
        draft = service.clone_to_draft(db, form, published, actor_id=None)
        changed_definition = deepcopy(draft.definition_json)
        changed_field = catalog_service.get_training_field(changed_definition)
        changed_field["catalog"] = [
            {
                **changed_field["catalog"][0],
                "name": "Excel zaawansowany",
                "price": "700.00",
                "capacity": 15,
            },
            {
                "id": "power-bi",
                "name": "Power BI",
                "price": "650.00",
                "currency": "PLN",
                "capacity": 10,
                "active": True,
                "sort_order": 2,
            },
        ]
        catalog_service._replace_training_field(changed_definition, changed_field)
        changed_definition, _ = catalog_service.reconcile_definition(
            draft.definition_json,
            changed_definition,
            used_training_ids={"excel-stable"},
        )
        service.update_definition(draft, changed_definition)
        draft_id = draft.id

        historical = db.get(FormVersion, published_id)
        historical_training = catalog_service.get_training_field(historical.definition_json)["catalog"][0]
        draft_training = catalog_service.get_training_field(draft.definition_json)["catalog"][0]
        assert historical_training["name"] == "Excel"
        assert historical_training["price"] == "500.00"
        assert draft_training["id"] == "excel-stable"
        assert draft_training["name"] == "Excel zaawansowany"
        assert draft_training["price"] == "700.00"
        assert draft_training["capacity"] == 15

        service.publish(db, form, draft, actor_id=None)

    with Session() as db:
        historical = db.get(FormVersion, published_id)
        current = db.get(FormVersion, draft_id)
        historical_catalog = catalog_service.get_training_field(historical.definition_json)["catalog"]
        current_catalog = catalog_service.get_training_field(current.definition_json)["catalog"]
        assert historical.status == FORM_VERSION_ARCHIVED
        assert historical_catalog[0]["name"] == "Excel"
        assert historical_catalog[0]["price"] == "500.00"
        assert current.status == FORM_VERSION_PUBLISHED
        assert [item["id"] for item in current_catalog] == ["excel-stable", "power-bi"]


def test_archived_and_published_versions_are_immutable(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        version = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        try:
            service.update_definition(version, _definition("name"))
        except FormVersionError as exc:
            assert "roboczą" in str(exc)
        else:
            raise AssertionError("Opublikowana wersja została zmieniona")

        service.archive(db, version)
        try:
            service.update_definition(version, _definition("name"))
        except FormVersionError as exc:
            assert "roboczą" in str(exc)
        else:
            raise AssertionError("Archiwalna wersja została zmieniona")


def test_clone_uses_unambiguous_minor_and_major_numbers(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        minor = service.clone_to_draft(db, form, first, actor_id=None, bump="minor")
        assert (minor.version_major, minor.version_minor, minor.version_label) == (1, 1, "1.1")
        service.publish(db, form, minor, actor_id=None)
        major = service.clone_to_draft(db, form, minor, actor_id=None, bump="major")
        assert (major.version_major, major.version_minor, major.version_label) == (2, 0, "2.0")


def test_publish_rejects_label_inconsistent_with_numeric_version(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        draft = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_DRAFT)
        draft.version_label = "1.10"
        try:
            service.publish(db, form, draft, actor_id=None)
        except FormVersionError as exc:
            assert "major/minor" in str(exc)
        else:
            raise AssertionError("Opublikowano wersję z niespójną etykietą")


def test_publish_rejects_field_availability_for_removed_workflow_step(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        draft = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_DRAFT)
        definition = deepcopy(draft.definition_json)
        definition["fields"][0]["availability"] = [{
            "step": "removed_step",
            "visible": True,
            "editable": True,
            "required": True,
        }]
        service.update_definition(draft, definition)
        with pytest.raises(FormVersionValidationError) as exc_info:
            service.publish(db, form, draft, actor_id=None)
        assert any("nieistniejącego etapu" in error for error in exc_info.value.errors)


def test_submission_relationship_points_to_exact_version(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        version = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        submission = FormSubmission(
            submission_id="public-1",
            form_slug=form.slug,
            form_name=form.name,
            form_version_id=version.id,
            data_json={},
            access_token="token",
        )
        db.add(submission)
        db.flush()
        submission_id = submission.id
        version_id = version.id

    with Session() as db:
        submission = db.get(FormSubmission, submission_id)
        assert submission.form_version.id == version_id
        assert service.resolve_for_submission(db, submission).definition_json["title"] == "Formularz"


def _public_app(monkeypatch, Session, calls):
    app = Flask(__name__, template_folder=str(public_forms.Path(__file__).resolve().parents[1] / "templates"))
    app.config.update(TESTING=True, SECRET_KEY="test-secret", DATABASE_URL="sqlite:///test", WTF_CSRF_ENABLED=False)
    app.jinja_env.filters["trusted_html"] = lambda value: value
    app.jinja_env.filters["option_value"] = lambda value: value.get("value", "") if isinstance(value, dict) else value
    app.jinja_env.filters["option_label"] = lambda value: value.get("label", "") if isinstance(value, dict) else value

    class SubmissionServiceStub:
        def submit_form(self, slug, definition, payload, *, form_version_id=None):
            calls.append({"slug": slug, "definition": definition, "form_version_id": form_version_id})
            return {
                "ok": True,
                "errors": {},
                "values": {},
                "result": {
                    "submission_id": "new-submission",
                    "form_slug": slug,
                    "form_title": definition["title"],
                    "pdf_filename": "",
                    "pdf_url": "",
                    "signed_pdf_filename": "",
                    "signed_pdf_url": None,
                    "upload_url": "#",
                    "signature_status": "manual",
                    "verification": None,
                },
            }

    app.extensions["services"] = SimpleNamespace(
        form_version_service=FormVersionService(),
        submission_service=SubmissionServiceStub(),
    )
    monkeypatch.setattr(public_forms, "db_session_factory", lambda: Session)
    documents = Blueprint("documents", __name__)
    documents.add_url_rule("/documents-to-sign", "documents_to_sign", lambda: "")
    app.register_blueprint(documents)
    app.register_blueprint(public_forms.bp)
    @app.context_processor
    def _layout_context():
        return {
            "app_name": "Test",
            "app_base_path": "",
            "site_footer": SimpleNamespace(
                logo_position="top",
                layout="single",
                logo_html="",
                left_html="",
                right_html="",
                social_left_html="",
                social_right_html="",
                social_bottom_html="",
            ),
            "footer_contact": SimpleNamespace(address="", phones=[], email=""),
            "footer_service_documents": [],
        }
    return app


def test_public_get_requires_published_and_post_stays_on_rendered_version(tmp_path, monkeypatch):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db, slug="public-version")
        first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        first_id = first.id

    calls = []
    app = _public_app(monkeypatch, Session, calls)
    client = app.test_client()
    response = client.get("/form/public-version")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    token = re.search(
        r'name="form_version_token"[^>]*value="([^"]+)"',
        html,
        flags=re.DOTALL,
    ).group(1)

    with Session.begin() as db:
        form = db.execute(select(Form).where(Form.slug == "public-version")).scalar_one()
        first = db.get(FormVersion, first_id)
        second = service.clone_to_draft(db, form, first, actor_id=None, bump="minor")
        updated = deepcopy(second.definition_json)
        updated["title"] = "Nowszy formularz"
        service.update_definition(second, updated)
        service.publish(db, form, second, actor_id=None, change_summary="Nowa publikacja")

    response = client.post(
        "/submit/public-version",
        data={"form_version_token": token, "form_version_id": str(first_id), "email": "jan@example.test"},
    )
    assert response.status_code == 200
    assert calls[-1]["form_version_id"] == first_id
    assert calls[-1]["definition"]["title"] == "Formularz"

    with Session.begin() as db:
        second = service.resolve_published(db, db.execute(select(Form.id).where(Form.slug == "public-version")).scalar_one())
        service.archive(db, second)
    assert client.get("/form/public-version").status_code == 404


def test_rejected_public_post_rerenders_its_original_form_version(tmp_path, monkeypatch):
    # Given a rendered version and a newer publication before the participant submits.
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db, slug="rejected-version")
        first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        first_id = first.id
    calls = []
    app = _public_app(monkeypatch, Session, calls)
    client = app.test_client()
    page = client.get("/form/rejected-version").get_data(as_text=True)
    token = re.search(r'name="form_version_token"[^>]*value="([^"]+)"', page, re.DOTALL).group(1)
    with Session.begin() as db:
        form = db.execute(select(Form).where(Form.slug == "rejected-version")).scalar_one()
        newer = service.clone_to_draft(db, form, db.get(FormVersion, first_id), actor_id=None, bump="minor")
        updated = deepcopy(newer.definition_json)
        updated["fields"][0]["label"] = "New email"
        service.update_definition(newer, updated)
        service.publish(db, form, newer, actor_id=None)

    def reject(_slug, definition, _payload, *, form_version_id=None):
        calls.append({"definition": definition, "form_version_id": form_version_id})
        return {"ok": False, "errors": {"email": "Invalid"}, "values": {"email": "bad-email"}, "result": None}

    monkeypatch.setattr(app.extensions["services"].submission_service, "submit_form", reject)

    # When validation fails, Expected: the current input and old snapshot return.
    response = client.post("/submit/rejected-version", data={"form_version_token": token, "email": "bad-email"})
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert calls[-1]["form_version_id"] == first_id
    assert calls[-1]["definition"]["fields"][0]["label"] == "Email"
    assert "New email" not in html
    assert 'value="bad-email"' in html


def test_public_post_rejects_missing_or_tampered_version_token(tmp_path, monkeypatch):
    _url, Session = _database(tmp_path)
    with Session.begin() as db:
        form = _form_with_field(db, slug="secured-version")
        FormVersionService().create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
    calls = []
    client = _public_app(monkeypatch, Session, calls).test_client()
    assert client.post("/submit/secured-version", data={"email": "jan@example.test"}).status_code == 409
    assert client.post(
        "/submit/secured-version",
        data={"email": "jan@example.test", "form_version_token": "tampered"},
    ).status_code == 409
    assert calls == []


def test_document_context_uses_upgraded_submission_version_after_new_publication(tmp_path, monkeypatch):
    database_url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db, slug="historical-doc")
        first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        submission = FormSubmission(
            submission_id="historical-submission",
            form_slug=form.slug,
            form_name=form.name,
            form_version_id=first.id,
            data_json={},
            access_token="token",
        )
        db.add(submission)
        second = service.clone_to_draft(db, form, first, actor_id=None, bump="major")
        updated = deepcopy(second.definition_json)
        updated["title"] = "Nowy dokument"
        updated["fields"].append({
            "name": "late_required",
            "label": "Późniejsze pole",
            "type": "text",
            "availability": [{
                "step": "submission",
                "visible": True,
                "editable": True,
                "required": True,
            }],
        })
        service.update_definition(second, updated)
        service.publish(db, form, second, actor_id=None, change_summary="Nowa treść dokumentów")

    app = Flask(__name__)
    app.config.update(DATABASE_URL=database_url)
    app.extensions["services"] = SimpleNamespace(
        form_version_service=service,
        form_config_service=FormConfigService(),
    )
    monkeypatch.setattr(document_routes, "create_session_factory", lambda _url: Session)
    with app.app_context():
        historical_config = document_routes.get_form_config("historical-doc", "historical-submission")
        current_config = document_routes.get_form_config("historical-doc")

    assert historical_config["title"] == "Nowy dokument"
    assert current_config["title"] == "Nowy dokument"
    assert "late_required" in {field.get("name") for field in historical_config["fields"]}
    assert "late_required" in {field.get("name") for field in current_config["fields"]}


def test_migration_creates_technical_version_and_backfills_submissions(monkeypatch):
    migration = importlib.import_module("migrations.versions.20260812_0031_form_versioning")
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    forms = Table(
        "forms",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("slug", String(255), nullable=False),
        Column("name", String(255), nullable=False),
        Column("title", String(255)),
        Column("description", Text),
        Column("definition_json", JSON),
        Column("created_by_id", Integer),
    )
    fields = Table(
        "form_fields",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("form_id", Integer, nullable=False),
        Column("name", String(255)),
        Column("label", Text),
        Column("type", String(64)),
        Column("required", Integer),
        Column("options", JSON),
        Column("default_value", Text),
        Column("section", String(255)),
        Column("sort_order", Integer),
    )
    submissions = Table(
        "form_submissions",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("form_slug", String(255), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(forms.insert().values(id=1, slug="legacy", name="Legacy", title="Legacy", description="", definition_json=_definition(), created_by_id=None))
        connection.execute(fields.insert().values(id=1, form_id=1, name="email", label="E-mail", type="email", required=1, options=[], default_value="", section="Kontakt", sort_order=1))
        connection.execute(submissions.insert().values(id=1, form_slug="legacy"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.upgrade()
        versions = Table("form_versions", MetaData(), autoload_with=connection)
        migrated_submissions = Table("form_submissions", MetaData(), autoload_with=connection)
        version_rows = connection.execute(select(versions)).mappings().all()
        submission_row = connection.execute(select(migrated_submissions)).mappings().one()

    assert "form_version_id" in {column["name"] for column in inspect(engine).get_columns("form_submissions")}
    assert len(version_rows) == 1
    assert version_rows[0]["status"] == FORM_VERSION_PUBLISHED
    assert version_rows[0]["version_label"] == "1.0"
    assert version_rows[0]["definition_json"]["fields"][1]["name"] == "email"
    assert "Migracja techniczna" in version_rows[0]["change_summary"]
    assert submission_row["form_version_id"] == version_rows[0]["id"]
