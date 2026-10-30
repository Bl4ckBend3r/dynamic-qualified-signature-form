from copy import deepcopy
from html import unescape
from io import BytesIO
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from database import create_session_factory
from form_loader import load_form_definition
from models import Form, FormSubmission, FormVersion, RepeatableGroupItemDecision, SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent
from services.decision_definition_service import DecisionDefinitionError
from test_admin_panel import admin_app, admin_client, create_form, create_user, login


def make_submission(app, definition, *, step=None):
    form_id = create_form(app, slug='approval-test', name='Approval test')
    factory = create_session_factory(app.config['DATABASE_URL'])
    with factory() as db:
        version = FormVersion(form_id=form_id, version_major=1, version_minor=0,
                              version_label='1.0', status='published', definition_json=definition)
        db.add(version)
        db.commit()
        version_id = version.id
    people = [{'record_uuid': str(uuid4()), 'imie': name, 'nazwisko': 'Test',
               'email': f'{name.lower()}@example.org', 'telefon': '123456789',
               'ieg_waw_dzien': '2026-09-15', 'ieg_waw_godzina': '07:30',
               'waw_ieg_dzien': '2026-09-16', 'waw_ieg_godzina': '18:15'} for name in ('Jan', 'Anna')]
    services = app.extensions['services']
    row = services.submission_service.create_submission(
        'approval-test', definition, {'data_json': {'podrozni': people, 'typ_wnioskodawcy': 'urzednik'}},
        dispatch_received=False, form_version_id=version_id,
    )
    if step:
        services.submission_repository.update(row['submission_id'], {'workflow_step': step, 'workflow_stage': step})
    return form_id, row, people


def officer_decision_fixture(app, *, stage="officer_review"):
    definition = {
        "title": "Decyzja urzędnika",
        "fields": [
            {"type": "section", "label": "Dane zgłoszenia"},
            {"name": "application_only", "label": "Tylko we wniosku", "type": "text",
             "availability": [{"step": "officer_review", "visible": True, "editable": False, "required": False}]},
            {"type": "section", "label": "Pusta sekcja"},
            {"name": "hidden_declaration", "label": "Ukryte pole", "type": "text",
             "availability": [{"step": "declaration", "visible": False, "editable": False, "required": False}]},
            {"type": "section", "label": "Dane deklaracji"},
            {"name": "declaration_note", "label": "Uwagi do deklaracji", "type": "text",
             "required": True, "availability": [{"step": "declaration", "visible": True,
                                                   "editable": True, "required": True}]},
        ],
        "documents": [{"id": "declaration", "enabled": True, "kind": "generated_pdf",
                       "label": "Deklaracja", "template_html": "<h1>Deklaracja</h1>",
                       "fields": [{"name": "participant_statement", "label": "Oświadczenie", "type": "text",
                                   "required": True},
                                  {"name": "training_choice", "label": "Wybierz szkolenia", "type": "training_selection",
                                   "enabled": True, "catalog": [{"id": "course-a", "name": "Szkolenie A", "price": "100", "capacity": 10}],
                                   "availability": [{"step": "officer_review", "visible": True,
                                                     "editable": False, "required": False}]}]}],
        "workflow": {
            "schema_version": 2,
            "flow_mode": "explicit",
            "initial_step": "officer_review",
            "steps": [
                {"id": "officer_review", "admin_label": "Oczekuje na decyzję urzędnika",
                 "stage_type": "decision", "decision_scope": "submission",
                 "status": "WAITING_FOR_OFFICER_DECISION"},
                {"id": "declaration", "admin_label": "Deklaracja gotowa",
                 "stage_type": "document", "status": "DECLARATION_READY",
                 "next_action": "Uzupełnij i wygeneruj deklarację", "next": "declaration_signature"},
                {"id": "declaration_signature", "admin_label": "Podpisz deklarację",
                 "stage_type": "document", "document_id": "declaration",
                 "action": "await_signature", "status": "DECLARATION_WAITING_FOR_SIGNATURE",
                 "description": "Pobierz deklarację i podpisz przez <a href=\"https://example.com/podpis\" target=\"_blank\" onclick=\"alert(1)\">Profil Zaufany</a>. <script>alert(1)</script><a href=\"javascript:alert(1)\">Zły link</a>",
                 "next": "completed"},
                {"id": "completed", "admin_label": "Zakończono", "stage_type": "final",
                 "status": "PROCESS_COMPLETED", "final": True},
            ],
            "decision_types": [
                {"code": "pzytywna", "label": "Zaakceptowano", "semantic_category": "positive",
                 "step_id": "officer_review", "target_step": "declaration", "scope": "submission"},
            ],
        },
    }
    form_id = create_form(app, slug="officer-decision-test", name="Decyzja",
                          definition_json={"title": "Zmodyfikowany draft", "fields": []})
    factory = create_session_factory(app.config["DATABASE_URL"])
    with factory() as db:
        version = FormVersion(form_id=form_id, version_major=1, version_minor=0,
                              version_label="1.0", status="published", definition_json=definition)
        db.add(version)
        db.flush()
        submission = FormSubmission(
            submission_id=str(uuid4()), form_slug="officer-decision-test", form_name="Decyzja",
            form_version_id=version.id, process_status="WAITING_FOR_OFFICER_DECISION",
            workflow_step="officer_review", workflow_stage=stage,
            access_token="participant-test-token",
        )
        db.add(submission)
        db.commit()
        return form_id, submission.id, submission.submission_id


def post_officer_decision(app, client, form_id, pk, *, decision="pzytywna"):
    listing = client.get(f"/admin/forms/{form_id}/submissions").get_data(as_text=True)
    csrf = listing.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    return client.post(f"/admin/forms/{form_id}/submissions/decisions", data={
        "csrf_token": csrf, "submission_row_ids": [str(pk)],
        f"officer_decision_{pk}": decision,
    })


def test_officer_decision_commits_transition_and_audit_once(admin_app, admin_client):
    create_user(admin_app)
    form_id, pk, _ = officer_decision_fixture(admin_app)
    login(admin_client)

    assert post_officer_decision(admin_app, admin_client, form_id, pk).status_code == 302
    assert post_officer_decision(admin_app, admin_client, form_id, pk).status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        decisions = db.scalars(select(SubmissionDecision).where(SubmissionDecision.submission_id == pk)).all()
        events = db.scalars(select(SubmissionWorkflowEvent).where(
            SubmissionWorkflowEvent.submission_id == pk,
            SubmissionWorkflowEvent.reason == "officer_decision",
        )).all()
        assert submission.workflow_step == "declaration"
        assert len(decisions) == len(events) == 1
        assert (decisions[0].workflow_step, decisions[0].target_step) == ("officer_review", "declaration")
        assert (events[0].previous_step, events[0].new_step) == ("officer_review", "declaration")


def test_officer_decision_transition_error_rolls_back_all_domain_rows(admin_app, admin_client, monkeypatch):
    from services.workflow_service import WorkflowTransitionError

    create_user(admin_app)
    form_id, pk, _ = officer_decision_fixture(admin_app)
    login(admin_client)
    workflow = admin_app.extensions["services"].workflow_service
    original = workflow.transition_submission

    def fail_after_transition(*args, **kwargs):
        original(*args, **kwargs)
        raise WorkflowTransitionError("Test błędu po utworzeniu eventu")

    monkeypatch.setattr(workflow, "transition_submission", fail_after_transition)
    assert post_officer_decision(admin_app, admin_client, form_id, pk).status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        assert submission.workflow_step == "officer_review"
        assert submission.officer_decision is None or not submission.officer_decision
        assert not db.scalars(select(SubmissionDecision).where(SubmissionDecision.submission_id == pk)).all()
        assert not db.scalars(select(SubmissionWorkflowEvent).where(SubmissionWorkflowEvent.submission_id == pk)).all()


def test_officer_decision_invalid_version_target_is_controlled(admin_app, admin_client):
    create_user(admin_app)
    form_id, pk, _ = officer_decision_fixture(admin_app)
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        version = submission.form_version
        definition = deepcopy(version.definition_json)
        definition["workflow"]["decision_types"][0]["target_step"] = "unknown_target"
        version.definition_json = definition
        db.commit()
    login(admin_client)
    assert post_officer_decision(admin_app, admin_client, form_id, pk).status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        assert submission.workflow_step == "officer_review"
        assert not submission.officer_decision
        assert db.scalar(select(SubmissionDecision.id).where(SubmissionDecision.submission_id == pk)) is None


def test_officer_decision_document_without_config_is_rejected_before_commit(admin_app, admin_client):
    create_user(admin_app)
    form_id, pk, _ = officer_decision_fixture(admin_app)
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        version = submission.form_version
        definition = deepcopy(version.definition_json)
        document_step = next(step for step in definition["workflow"]["steps"] if step["id"] == "declaration")
        document_step["stage_type"] = "documents"
        document_step["action"] = "none"
        document_step["document_lifecycle"] = "group"
        definition["documents"] = [{"id": "declaration", "enabled": False, "kind": "generated_pdf", "workflow_step": "declaration"}]
        from services.documents.document_workflow_service import is_document_step, step_documents
        assert is_document_step(document_step)
        assert not step_documents(definition, document_step)
        version.definition_json = definition
        db.commit()
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        persisted_definition = db.get(FormSubmission, pk).form_version.definition_json
        persisted_step = next(step for step in persisted_definition["workflow"]["steps"] if step["id"] == "declaration")
        assert is_document_step(persisted_step)
        assert not step_documents(persisted_definition, persisted_step)
        from services.workflow_service import WorkflowTransitionError
        with pytest.raises(WorkflowTransitionError):
            admin_app.extensions["services"].workflow_service.validate_automatic_path(persisted_definition, "declaration")
    login(admin_client)
    assert post_officer_decision(admin_app, admin_client, form_id, pk).status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        assert submission.workflow_step == "officer_review"
        assert not submission.officer_decision
        assert db.scalar(select(SubmissionDecision.id).where(SubmissionDecision.submission_id == pk)) is None


@pytest.mark.parametrize("failure", ["result", "exception"])
def test_officer_decision_mail_failure_preserves_domain_commit(admin_app, admin_client, monkeypatch, caplog, failure):
    from services.mail_dispatch_service import MailDispatchResult

    create_user(admin_app)
    form_id, pk, _ = officer_decision_fixture(admin_app)
    login(admin_client)
    dispatcher = admin_app.extensions["services"].mail_dispatch_service
    if failure == "result":
        monkeypatch.setattr(dispatcher, "dispatch_workflow_decision_event", lambda **_: MailDispatchResult("failed"))
    else:
        def fail_mail(**_):
            raise RuntimeError("private-contact@example.invalid")
        monkeypatch.setattr(dispatcher, "dispatch_workflow_decision_event", fail_mail)
    assert post_officer_decision(admin_app, admin_client, form_id, pk).status_code == 302
    with admin_client.session_transaction() as session:
        assert any("nie udało się wysłać powiadomienia" in message for _, message in session.get("_flashes", []))
    assert "private-contact@example.invalid" not in caplog.text
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        assert db.get(FormSubmission, pk).workflow_step == "declaration"
        assert db.scalar(select(SubmissionDecision.id).where(SubmissionDecision.submission_id == pk)) is not None


def test_officer_decision_disabled_mail_does_not_block(admin_app, admin_client):
    create_user(admin_app)
    form_id, pk, _ = officer_decision_fixture(admin_app)
    login(admin_client)
    assert post_officer_decision(admin_app, admin_client, form_id, pk).status_code == 302
    with admin_client.session_transaction() as session:
        assert not any("nie udało się wysłać powiadomienia" in message for _, message in session.get("_flashes", []))
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        assert db.get(FormSubmission, pk).workflow_step == "declaration"


def test_officer_decision_missing_mail_template_does_not_block(admin_app, admin_client, monkeypatch):
    from services.mail_dispatch_service import MailDispatchResult

    create_user(admin_app)
    form_id, pk, _ = officer_decision_fixture(admin_app)
    login(admin_client)
    dispatcher = admin_app.extensions["services"].mail_dispatch_service
    monkeypatch.setattr(dispatcher, "dispatch_workflow_decision_event", lambda **_: MailDispatchResult("skipped", error_message="Brak aktywnego szablonu maila."))
    assert post_officer_decision(admin_app, admin_client, form_id, pk).status_code == 302
    with admin_client.session_transaction() as session:
        assert not any("nie udało się wysłać powiadomienia" in message for _, message in session.get("_flashes", []))
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        assert db.get(FormSubmission, pk).workflow_step == "declaration"


def test_officer_decision_automatic_error_is_reported_after_commit(admin_app, admin_client, monkeypatch):
    from services.workflow_service import WorkflowTransitionError

    create_user(admin_app)
    form_id, pk, _ = officer_decision_fixture(admin_app)
    login(admin_client)
    workflow = admin_app.extensions["services"].workflow_service
    monkeypatch.setattr(workflow, "run_automatic_steps", lambda *_: (_ for _ in ()).throw(WorkflowTransitionError("Test")))
    assert post_officer_decision(admin_app, admin_client, form_id, pk).status_code == 302
    with admin_client.session_transaction() as session:
        assert any("automatyczny etap workflow" in message for _, message in session.get("_flashes", []))
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        assert db.get(FormSubmission, pk).workflow_step == "declaration"


def test_submission_enters_versioned_decision_before_declaration(admin_app):
    from services.admin_workflow_view_service import build_admin_workflow_view
    from services.workflow_service import WorkflowTransitionError

    definition = {"title": "Ścieżka decyzji", "fields": [], "workflow": {
        "flow_mode": "explicit", "initial_step": "application_sent", "steps": [
            {"id": "application_sent", "stage_type": "user_action", "type": "form_submit",
             "admin_label": "Wniosek złożony", "status": "FORM_SUBMITTED", "next": "human_gate"},
            {"id": "human_gate", "stage_type": "decision", "decision_scope": "submission",
             "admin_label": "Oczekujący na decyzję", "status": "WAITING_FOR_OFFICER_DECISION"},
            {"id": "ready_document", "stage_type": "documents", "admin_label": "Deklaracja gotowa",
             "status": "DECLARATION_READY"},
        ], "decision_types": [{"code": "approved", "step_id": "human_gate", "target_step": "ready_document"}],
    }}
    form_id, row, _ = make_submission(admin_app, definition)
    services = admin_app.extensions["services"]
    public_id = row["submission_id"]
    persisted = services.submission_repository.get_by_id(public_id)
    assert persisted["workflow_step"] == persisted["workflow_stage"] == "human_gate"
    assert persisted["process_status"] == "WAITING_FOR_OFFICER_DECISION"

    # An unrelated editor state must not replace the submission's FormVersion.
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        form = db.get(Form, form_id)
        form.definition_json = {"workflow": {"initial_step": "application_sent", "steps": [
            {"id": "application_sent", "next": "ready_document"}, {"id": "ready_document"},
        ]}}
        db.commit()
        submission = db.get(FormSubmission, persisted["id"])
        events = db.scalars(select(SubmissionWorkflowEvent).where(
            SubmissionWorkflowEvent.submission_id == submission.id).order_by(SubmissionWorkflowEvent.id)).all()
        assert [(event.previous_step, event.new_step) for event in events] == [("application_sent", "human_gate")]
        view = build_admin_workflow_view(submission, form_config=definition, events=[
            {"previous_step": event.previous_step, "new_step": event.new_step} for event in events
        ])
        assert [section["state"] for section in view["sections"]] == ["completed", "current", "future"]

    services.workflow_service.run_automatic_steps(persisted, definition)
    assert services.submission_repository.get_by_id(public_id)["workflow_step"] == "human_gate"
    with pytest.raises(WorkflowTransitionError):
        services.workflow_service.transition_to(public_id, "ready_document")
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, persisted["id"])
        with pytest.raises(WorkflowTransitionError):
            services.workflow_service.transition_submission(
                submission, "DECLARATION_READY", target_step="ready_document",
                actor="system", reason="automatic_shortcut",
            )
        services.workflow_service.transition_submission(
            submission, "DECLARATION_READY", target_step="ready_document",
            decision_code="approved", actor="officer", reason="officer_decision",
        )
        db.commit()
        assert submission.workflow_step == submission.workflow_stage == "ready_document"
        event = db.scalars(select(SubmissionWorkflowEvent).where(
            SubmissionWorkflowEvent.submission_id == submission.id,
            SubmissionWorkflowEvent.reason == "officer_decision",
        )).one()
        assert (event.previous_step, event.new_step) == ("human_gate", "ready_document")


def test_skip_diagnostic_does_not_repair_record_with_later_document(admin_app):
    from scripts.diagnostics.report_workflow_skip import build_report
    from test_workflow_v2_validation import explicit_workflow

    workflow = explicit_workflow()
    workflow["steps"].insert(2, {"id": "document", "stage_type": "documents", "status": "DECLARATION_READY", "next": "completed"})
    for decision in workflow["decision_types"]:
        decision["target_step"] = "document"
    _, row, _ = make_submission(admin_app, {"title": "Diagnostyka", "fields": [], "workflow": workflow})
    public_id = row["submission_id"]
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.scalar(select(FormSubmission).where(FormSubmission.submission_id == public_id))
        event = db.scalar(select(SubmissionWorkflowEvent).where(
            SubmissionWorkflowEvent.submission_id == submission.id,
        ))
        event.new_step = "document"
        event.new_status = "DECLARATION_READY"
        event.source = "legacy_shortcut"
        submission.workflow_step = submission.workflow_stage = "document"
        submission.process_status = "DECLARATION_READY"
        db.add(SubmissionFile(
            submission_id=submission.id, public_submission_id=public_id, form_slug=submission.form_slug,
            document_type="declaration", filename="generated.pdf", storage_path="test/generated.pdf", status="generated",
        ))
        db.commit()
        report = build_report(db, public_id)
        assert build_report(db, submission_pk=submission.id, form_id=submission.form_version.form_id) == report
        assert report["assessment"]["unvisited_decision_steps"] == ["review"]
        assert report["assessment"]["recorded_decision"] is False
        assert report["assessment"]["later_document_types"] == ["declaration"]
        assert report["assessment"]["automatic_repair_safe"] is False
        db.refresh(submission)
        assert submission.workflow_step == "document"


@pytest.mark.parametrize("stage", ["officer_review", "declaration"])
def test_officer_positive_decision_uses_canonical_step_and_version_status(admin_app, admin_client, stage):
    create_user(admin_app)
    form_id, pk, public_id = officer_decision_fixture(admin_app, stage=stage)
    login(admin_client)
    list_url = f"/admin/forms/{form_id}/submissions"
    before = admin_client.get(list_url).get_data(as_text=True)
    row_before = before.split("<tbody>", 1)[1].split("</tr>", 1)[0]
    assert 'value="pzytywna"' in row_before
    assert "Oczekuje na decyzję urzędnika" in row_before
    detail_before = admin_client.get(f"{list_url}/{pk}").get_data(as_text=True)
    assert 'value="pzytywna"' in detail_before
    csrf = before.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post(f"{list_url}/decisions", data={
        "csrf_token": csrf, "submission_row_ids": [str(pk)],
        f"officer_decision_{pk}": "pzytywna",
    })
    assert response.status_code == 302

    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        assert submission.process_status == "DECLARATION_READY"
        assert submission.workflow_step == submission.workflow_stage == "declaration"
        assert submission.officer_decision == "pzytywna"
        event = db.execute(select(SubmissionWorkflowEvent).where(
            SubmissionWorkflowEvent.submission_id == pk,
            SubmissionWorkflowEvent.reason == "officer_decision",
        )).scalar_one()
        assert (event.previous_step, event.new_step, event.new_status) == (
            "officer_review", "declaration", "DECLARATION_READY",
        )
        assert event.decision_code == "pzytywna"
        assert admin_app.extensions["services"].decision_definition_service.available_for_submission(
            submission, scope="submission"
        ) == []

    after = admin_client.get(list_url).get_data(as_text=True)
    row_after = after.split("<tbody>", 1)[1].split("</tr>", 1)[0]
    assert "Deklaracja gotowa" in row_after
    assert "Oczekuje na decyzję urzędnika" not in row_after
    assert 'value="pzytywna"' not in row_after
    all_submissions = admin_client.get("/admin/submissions")
    assert all_submissions.status_code == 200
    all_row = all_submissions.get_data(as_text=True).split("<tbody>", 1)[1].split("</tr>", 1)[0]
    assert "Deklaracja gotowa" in all_row
    assert "Oczekuje na decyzję urzędnika" not in all_row
    detail = admin_client.get(f"{list_url}/{pk}").get_data(as_text=True)
    assert 'value="pzytywna"' not in detail


def test_declaration_step_hides_officer_decisions_even_with_stale_stage(admin_app, admin_client):
    create_user(admin_app)
    form_id, pk, _ = officer_decision_fixture(admin_app, stage="officer_review")
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        submission.workflow_step = "declaration"
        db.commit()
        assert admin_app.extensions["services"].decision_definition_service.available_for_submission(
            submission, scope="submission"
        ) == []
    login(admin_client)
    list_url = f"/admin/forms/{form_id}/submissions"
    html = admin_client.get(list_url).get_data(as_text=True)
    assert 'value="pzytywna"' not in html.split("<tbody>", 1)[1].split("</tr>", 1)[0]
    csrf = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = admin_client.post(f"{list_url}/{pk}/decision", data={
        "csrf_token": csrf, "officer_decision": "pzytywna",
    })
    assert response.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        assert submission.officer_decision == ""
        assert submission.workflow_step == "declaration"


@pytest.mark.parametrize("stale_stage", [False, True])
@pytest.mark.parametrize("legacy_signature", [False, True])
def test_participant_can_fill_declaration_from_versioned_step(admin_app, admin_client, monkeypatch, stale_stage, legacy_signature):
    create_user(admin_app)
    form_id, pk, public_id = officer_decision_fixture(admin_app)
    login(admin_client)
    admin_detail = f"/admin/forms/{form_id}/submissions/{pk}"
    detail = admin_client.get(admin_detail).get_data(as_text=True)
    csrf = detail.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    assert admin_client.post(admin_detail + "/decision", data={
        "csrf_token": csrf, "officer_decision": "pzytywna",
    }).status_code == 302
    if stale_stage:
        with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
            submission = db.get(FormSubmission, pk)
            submission.workflow_stage = "officer_review"
            db.commit()

    token = "participant-test-token"
    status = admin_client.get(f"/api/submissions/{public_id}/acceptance-status",
                              headers={"Authorization": f"Bearer {token}"})
    assert status.status_code == 200
    assert status.json["current_workflow_step"] == "declaration"
    assert status.json["can_fill_declaration"] is True

    documents = admin_client.get(f"/do-podpisania?submission_id={public_id}&token={token}")
    assert documents.status_code == 200
    html = documents.get_data(as_text=True)
    assert "Deklaracja gotowa" in html
    assert "Uzupełnij i wygeneruj deklarację" in html
    assert "Wypełnij deklarację" in html
    assert "Zapisz dodatkowe informacje" not in html
    assert "Wybierz lub zmień szkolenia" not in html
    assert "data-training-picker" not in html
    from routes.documents import build_documents_to_sign_result, get_form_config, get_submission_context, requires_additional_fields
    with admin_app.test_request_context():
        submission_context = get_submission_context(public_id)
        version_config = get_form_config("officer-decision-test", public_id)
        assert admin_app.extensions["services"].declaration_flow_service.has_additional_fields(version_config) is False
        assert requires_additional_fields(version_config, submission_context["row"]) is False
        view = build_documents_to_sign_result(public_id, submission_context, access_token=token)
        assert view.get("needs_additional_fields", False) is False
    declaration_url = f"/declaration/officer-decision-test/{public_id}?token={token}"
    declaration = admin_client.get(declaration_url)
    assert declaration.status_code == 200
    declaration_html = declaration.get_data(as_text=True)
    assert "Uzupelnienie deklaracji uczestnictwa" in declaration_html
    assert "Dane deklaracji" in declaration_html
    assert "Dane zgłoszenia" not in declaration_html
    assert "Pusta sekcja" not in declaration_html
    assert "Tylko we wniosku" not in declaration_html
    assert "Ukryte pole" not in declaration_html
    assert "training_choice" not in declaration_html

    csrf = declaration_html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    early_upload = admin_client.post(
        f"/upload-declaration-signed/officer-decision-test/{public_id}",
        data={"csrf_token": csrf, "access_token": token,
              "signed_declaration_pdf": (BytesIO(b"%PDF-1.4\n"), "signed.pdf")},
    )
    assert early_upload.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        assert db.query(SubmissionFile).filter_by(submission_id=pk, signed=True).count() == 0
    training_url = f"/submissions/{public_id}/trainings?token={token}"
    assert admin_client.get(training_url).status_code == 403
    assert admin_client.post(training_url, data={"csrf_token": csrf, "training_choice": "course-a"}).status_code == 403
    response = admin_client.post(declaration_url, data={
        "csrf_token": csrf, "declaration_note": "Uwagi", "participant_statement": "Potwierdzam",
    })
    assert response.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        assert submission.declaration_generated == "Tak"
        assert submission.workflow_step == submission.workflow_stage == "declaration_signature"
        assert submission.process_status == "DECLARATION_WAITING_FOR_SIGNATURE"

    if legacy_signature:
        with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
            submission = db.get(FormSubmission, pk)
            version = db.get(FormVersion, submission.form_version_id)
            definition = deepcopy(version.definition_json)
            signature_step = next(step for step in definition["workflow"]["steps"] if step["id"] == "declaration_signature")
            signature_step.pop("document_id")
            signature_step.pop("action")
            version.definition_json = definition
            db.commit()
    if stale_stage:
        with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
            submission = db.get(FormSubmission, pk)
            submission.workflow_stage = "officer_review"
            db.commit()

    signature_status = admin_client.get(
        f"/api/submissions/{public_id}/acceptance-status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert signature_status.status_code == 200
    assert signature_status.json["can_download_declaration"] is True
    assert signature_status.json["can_upload_signed_declaration"] is True
    assert signature_status.json["declaration_stage_completed"] is False
    assert '<a href="https://example.com/podpis" target="_blank" rel="noopener noreferrer">Profil Zaufany</a>' in signature_status.json["instruction"]["current_stage_description"]
    assert "javascript:" not in signature_status.json["instruction"]["current_stage_description"]
    assert "alert(1)" not in signature_status.json["instruction"]["current_stage_description"]
    signature_page = admin_client.get(f"/do-podpisania?submission_id={public_id}&token={token}")
    assert signature_page.status_code == 200
    signature_html = signature_page.get_data(as_text=True)
    assert "Pobierz deklarację PDF" in signature_html
    assert 'name="signed_declaration_pdf"' in signature_html
    assert "Wypełnij deklarację" not in signature_html
    assert "Wybierz lub zmień szkolenia" not in signature_html
    assert '<a href="https://example.com/podpis" target="_blank" rel="noopener noreferrer">Profil Zaufany</a>' in signature_html
    assert "javascript:" not in signature_html
    assert "alert(1)" not in signature_html
    download_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>\s*Pobierz deklarację PDF', signature_html)
    assert download_match is not None
    download_url = unescape(download_match.group(1))
    downloaded = admin_client.get(download_url)
    assert downloaded.status_code == 200
    assert downloaded.data.startswith(b"%PDF")
    assert admin_client.get(download_url.replace(f"token={token}", "token=wrong-token")).status_code == 404

    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        filename = db.get(FormSubmission, pk).declaration_filename
    storage = admin_app.extensions["services"].storage
    missing_files = {path: content for path, content in storage.saved_pdfs.items() if path.endswith(filename)}
    assert missing_files
    for path in missing_files:
        del storage.saved_pdfs[path]
    missing_html = admin_client.get(f"/do-podpisania?submission_id={public_id}&token={token}").get_data(as_text=True)
    assert re.search(r'<a[^>]*>\s*Pobierz deklarację PDF', missing_html) is None
    assert 'name="signed_declaration_pdf"' not in missing_html
    storage.saved_pdfs.update(missing_files)

    upload_url = f"/upload-declaration-signed/officer-decision-test/{public_id}"
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        submission.workflow_step = "officer_review"
        db.commit()
    wrong_step = admin_client.post(upload_url, data={"csrf_token": csrf, "access_token": token,
        "signed_declaration_pdf": (BytesIO(downloaded.data), "signed.pdf")})
    assert wrong_step.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        assert db.query(SubmissionFile).filter_by(submission_id=pk, signed=True).count() == 0
        submission = db.get(FormSubmission, pk)
        submission.workflow_step = "declaration_signature"
        db.commit()
    admin_app.config["WTF_CSRF_ENABLED"] = True
    assert admin_client.post(upload_url, data={"access_token": token,
        "signed_declaration_pdf": (BytesIO(downloaded.data), "signed.pdf")}).status_code == 400
    assert admin_client.post(upload_url, data={"csrf_token": csrf, "access_token": "wrong-token",
        "signed_declaration_pdf": (BytesIO(downloaded.data), "signed.pdf")}).status_code == 404

    monkeypatch.setattr(admin_app.extensions["services"].document_signing_service, "verify_uploaded_pdf",
                        lambda *_args, **_kwargs: {"is_signed": True, "result": "VALID_SIGNATURE",
                                                 "signature_type": "trusted_profile", "validation_status": "VALID"})
    uploaded = admin_client.post(upload_url, data={"csrf_token": csrf, "access_token": token,
        "signed_declaration_pdf": (BytesIO(downloaded.data), "signed.pdf")})
    assert uploaded.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        submission = db.get(FormSubmission, pk)
        assert submission.declaration_signature_valid == "Tak"
        assert submission.workflow_step == "completed"
        signed_file = db.query(SubmissionFile).filter_by(submission_id=pk, signed=True,
                                                          document_id="declaration").one()
        assert signed_file.signature_status == "valid"


@pytest.mark.parametrize(('decision', 'target'), [('accepted', 'declaration_generation'), ('rejected', 'end_rejected'), ('conditionally_accepted', 'declaration_generation')])
def test_submission_waits_for_approval_then_follows_configured_decision(admin_app, admin_client, decision, target):
    create_user(admin_app)
    definition = load_form_definition('examples/forms/loty.json')
    definition['workflow']['decision_types'].append({'code': 'conditionally_accepted', 'label': 'Akceptacja warunkowa',
        'semantic_category': 'positive', 'require_reason': True, 'step_id': 'initial_acceptance',
        'target_step': 'declaration_generation', 'scope': 'submission'})
    form_id, row, people = make_submission(admin_app, definition)
    services = admin_app.extensions['services']
    public_id = row['submission_id']
    assert row['workflow_step'] == 'initial_acceptance'
    assert not services.workflow_service.can_execute_step(row, definition, 'declaration_generation')
    assert services.workflow_service.resolve_next_step(definition, 'initial_acceptance') is None
    for _ in range(2):
        status = admin_client.get(f'/api/submissions/{public_id}/acceptance-status', headers={'Authorization': f"Bearer {row['access_token']}"})
        assert status.status_code == 200
        assert status.json['current_workflow_step'] == 'initial_acceptance'
        assert not status.json['can_fill_declaration']
        assert not status.json['can_sign_documents']
    assert not services.submission_repository.get_by_id(public_id).get('declaration_filename')
    factory = create_session_factory(admin_app.config['DATABASE_URL'])
    with factory() as db:
        submission = db.execute(select(FormSubmission).where(FormSubmission.submission_id == public_id)).scalar_one()
        pk = submission.id
        assert services.decision_definition_service.repeatable_groups(submission)[0]['decisions'] == []
        with pytest.raises(DecisionDefinitionError):
            services.decision_definition_service.decide_item(db, submission, group_key='podrozni', item_id=people[0]['record_uuid'], decision_code='accepted', comment='', actor=SimpleNamespace(id=None))
        assert db.execute(select(SubmissionWorkflowEvent).where(SubmissionWorkflowEvent.submission_id == pk)).scalars().first().new_step == 'initial_acceptance'
    login(admin_client)
    detail = admin_client.get(f'/admin/forms/{form_id}/submissions/{pk}').get_data(as_text=True)
    csrf = detail.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    if decision == 'conditionally_accepted':
        assert 'value="conditionally_accepted"' in detail
        admin_client.post(f'/admin/forms/{form_id}/submissions/{pk}/decision', data={'csrf_token': csrf, 'officer_decision': decision})
        assert services.submission_repository.get_by_id(public_id)['workflow_stage'] == 'initial_acceptance'
    response = admin_client.post(f'/admin/forms/{form_id}/submissions/{pk}/decision', data={
        'csrf_token': csrf, 'officer_decision': decision, 'officer_decision_reason': 'Uzasadnienie',
    })
    assert response.status_code == 302
    assert services.submission_repository.get_by_id(public_id)['workflow_stage'] == target
    if decision != 'rejected':
        status = admin_client.get(f'/api/submissions/{public_id}/acceptance-status', headers={'Authorization': f"Bearer {row['access_token']}"})
        assert status.json['can_fill_declaration'] is True
    if decision == 'conditionally_accepted':
        with factory() as db:
            saved = db.execute(select(SubmissionDecision).where(SubmissionDecision.submission_id == pk)).scalar_one()
            assert saved.justification == 'Uzasadnienie'
            assert saved.semantic_category == 'positive'


def test_custom_item_options_require_reason_and_only_change_selected_uuid(admin_app, admin_client):
    create_user(admin_app)
    definition = load_form_definition('examples/forms/loty.json')
    definition['workflow']['decision_types'].append({
        'code': 'requires_correction', 'label': 'Do poprawy', 'semantic_category': 'correction',
        'require_reason': True, 'step_id': 'dit_decision_urzednik', 'target_step': 'dit_decision_urzednik', 'scope': 'item',
    })
    form_id, row, people = make_submission(admin_app, definition, step='dit_decision_urzednik')
    service = admin_app.extensions['services'].decision_definition_service
    factory = create_session_factory(admin_app.config['DATABASE_URL'])
    with factory() as db:
        submission = db.execute(select(FormSubmission).where(FormSubmission.submission_id == row['submission_id'])).scalar_one()
        pk = submission.id
        actor = SimpleNamespace(id=None, email='', role='admin')
        with pytest.raises(DecisionDefinitionError, match='Uzasadnienie'):
            service.decide_item(db, submission, group_key='podrozni', item_id=people[1]['record_uuid'], decision_code='requires_correction', comment='', actor=actor)
        service.decide_item(db, submission, group_key='podrozni', item_id=people[1]['record_uuid'], decision_code='requires_correction', comment='Uzupełnij datę', actor=actor)
        decisions = list(db.execute(select(RepeatableGroupItemDecision)).scalars())
        assert [item.item_id for item in decisions] == [people[1]['record_uuid']]
        assert not service.completion_state(db, submission, 'podrozni')['complete']
        service.decide_item(db, submission, group_key='podrozni', item_id=people[0]['record_uuid'], decision_code='bilet_bezplatny', comment='', actor=actor)
        assert not service.completion_state(db, submission, 'podrozni')['complete']
        db.commit()
    login(admin_client)
    html = admin_client.get(f'/admin/forms/{form_id}/submissions/{pk}').get_data(as_text=True)
    cards = html.split('<section id="repeatable-decisions">', 1)[1].split('<dialog', 1)[0]
    assert 'data-required-note="false"' in cards
    assert 'Pola oznaczone' not in cards
    assert 'Decyzja <span class="required-marker"' in cards
    for value in ('bilet_bezplatny', 'bilet_ze_znizka', 'brak_biletow_pula_wykorzystana', 'requires_correction'):
        assert f'value="{value}"' in cards
    assert 'IEG → WAW' in cards and '07:30' in cards
    assert html.index('id="workflow-sla"') < html.index('id="repeatable-decisions"')
    assert '<dt>Imię</dt>' not in cards and '<dt>Nazwisko</dt>' not in cards


def test_draft_option_assignments_preserve_other_steps_and_published_snapshots(admin_app):
    form_id = create_form(admin_app)
    service = admin_app.extensions['services'].decision_definition_service
    with create_session_factory(admin_app.config['DATABASE_URL'])() as db:
        definition = service.save_definition(db, code='conditionally_accepted', label='Akceptacja warunkowa', category='positive')
        draft = FormVersion(form_id=form_id, version_major=1, version_minor=0, version_label='1.0', status='draft',
                            definition_json={'workflow': {'steps': [{'id': 'review'}, {'id': 'review_people'}, {'id': 'done'}]}})
        service.assign_to_draft(draft, definition, step_id='review', target_step='done', require_reason=True, scope='submission', sort_order=2)
        service.assign_to_draft(draft, definition, step_id='review_people', target_step='done', require_reason=True, scope='item', sort_order=1)
        snapshot = deepcopy(draft.definition_json)
        assert len(snapshot['workflow']['decision_types']) == 2
        assert snapshot['workflow']['decision_types'][0]['step_id'] == 'review_people'
        service.assign_to_draft(draft, definition, step_id='review_people', target_step='done', remove=True)
        assert len(draft.definition_json['workflow']['decision_types']) == 1
        published = SimpleNamespace(status='published', definition_json=snapshot)
        with pytest.raises(DecisionDefinitionError):
            service.assign_to_draft(published, definition, step_id='review', target_step='done')
        definition.label = 'Zmieniona nazwa katalogowa'
        assert published.definition_json['workflow']['decision_types'][0]['label'] == 'Akceptacja warunkowa'


def test_discount_ticket_changes_only_selected_uuid_and_preserves_old_history(admin_app, admin_client):
    create_user(admin_app)
    definition = load_form_definition('examples/forms/loty.json')
    form_id, row, people = make_submission(admin_app, definition, step='dit_decision_urzednik')
    services = admin_app.extensions['services']
    factory = create_session_factory(admin_app.config['DATABASE_URL'])
    with factory() as db:
        submission = db.execute(select(FormSubmission).where(FormSubmission.submission_id == row['submission_id'])).scalar_one()
        pk = submission.id
        third = {**people[0], 'record_uuid': str(uuid4()), 'imie': 'Ewa'}
        submission.data_json = {**submission.data_json, 'podrozni': [*people, third]}
        old_definition = deepcopy(definition)
        old_definition['workflow']['decision_types'] = [{'code': 'correction', 'label': 'Do poprawy',
            'semantic_category': 'correction', 'scope': 'item', 'step_id': 'dit_decision_urzednik', 'target_step': 'dit_decision_urzednik'}]
        old_version = FormVersion(form_id=form_id, version_major=0, version_minor=9, version_label='0.9', status='archived', definition_json=old_definition)
        db.add(old_version)
        db.flush()
        historical = FormSubmission(submission_id=str(uuid4()), form_slug='approval-test', form_version_id=old_version.id,
            workflow_step='dit_decision_urzednik', workflow_stage='dit_decision_urzednik',
            process_status='WAITING_FOR_OFFICER_DECISION', data_json={'podrozni': people})
        db.add(historical)
        db.flush()
        services.decision_definition_service.decide_item(db, historical, group_key='podrozni', item_id=people[0]['record_uuid'],
            decision_code='correction', comment='Historyczny komentarz', actor=SimpleNamespace(id=None))
        historical_pk = historical.id
        db.commit()
    login(admin_client)
    html = admin_client.get(f'/admin/forms/{form_id}/submissions/{pk}').get_data(as_text=True)
    csrf = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    # This route must not send external mail during the test.
    services.mail_dispatch_service.dispatch_to_submission = lambda *args, **kwargs: SimpleNamespace(status='sent', log=None)
    response = admin_client.post(f'/admin/forms/{form_id}/submissions/{pk}/repeatable/podrozni/{people[1]["record_uuid"]}/decision',
        data={'csrf_token': csrf, 'decision_code': 'bilet_ze_znizka', 'comment': ''})
    assert response.status_code == 302
    with factory() as db:
        saved = list(db.execute(select(RepeatableGroupItemDecision).where(RepeatableGroupItemDecision.submission_id == pk)).scalars())
        assert [(d.item_id, d.decision_code, d.semantic_category) for d in saved] == [(people[1]['record_uuid'], 'bilet_ze_znizka', 'positive')]
        assert services.decision_definition_service.completion_state(db, db.get(FormSubmission, pk), 'podrozni')['complete'] is False
        assert db.get(FormSubmission, historical_pk).form_version.definition_json == old_definition
    historical_html = admin_client.get(f'/admin/forms/{form_id}/submissions/{historical_pk}').get_data(as_text=True)
    assert 'Do poprawy' in historical_html and 'Historia decyzji (1)' in historical_html


def test_explicit_item_decision_all_and_mail_configuration(admin_app, admin_client, monkeypatch):
    from test_workflow_v2_validation import explicit_workflow
    create_user(admin_app)
    definition = load_form_definition('examples/forms/loty.json')
    definition['workflow'] = explicit_workflow()
    decision_step = definition['workflow']['steps'][1]
    decision_step.update(decision_scope='item', decision_group='podrozni', completion_policy='ALL', completion_next='completed', decision_email={'enabled': False})
    for option in definition['workflow']['decision_types']:
        option.update(scope='item', require_reason=option['code'] == 'b')
    form_id, row, people = make_submission(admin_app, definition)
    assert row['workflow_stage'] == 'review'
    services = admin_app.extensions['services']
    pk = services.submission_repository.get_by_id(row['submission_id'])['id']
    monkeypatch.setattr(services.mail_dispatch_service, 'dispatch_to_submission', lambda **kwargs: (_ for _ in ()).throw(AssertionError('Mail must be disabled')))
    login(admin_client)
    html = admin_client.get(f'/admin/forms/{form_id}/submissions/{pk}').get_data(as_text=True)
    csrf = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    url = f'/admin/forms/{form_id}/submissions/{pk}/repeatable/podrozni/'
    admin_client.post(url + people[0]['record_uuid'] + '/decision', data={'csrf_token': csrf, 'decision_code': 'a'})
    assert services.submission_repository.get_by_id(row['submission_id'])['workflow_stage'] == 'review'
    admin_client.post(url + people[1]['record_uuid'] + '/decision', data={'csrf_token': csrf, 'decision_code': 'b'})
    assert services.submission_repository.get_by_id(row['submission_id'])['workflow_stage'] == 'review'
    admin_client.post(url + people[1]['record_uuid'] + '/decision', data={'csrf_token': csrf, 'decision_code': 'b', 'comment': 'Uzasadnienie'})
    assert services.submission_repository.get_by_id(row['submission_id'])['workflow_stage'] == 'completed'
    with create_session_factory(admin_app.config['DATABASE_URL'])() as db:
        history = list(db.execute(select(RepeatableGroupItemDecision).where(RepeatableGroupItemDecision.submission_id == pk)).scalars())
        assert {entry.item_id for entry in history} == {person['record_uuid'] for person in people}
        assert {entry.decision_code for entry in history} == {'a', 'b'}


def test_explicit_document_signature_before_decision_public_access(admin_app, admin_client):
    from test_workflow_v2_validation import explicit_workflow
    definition = load_form_definition('examples/forms/loty.json')
    workflow = explicit_workflow()
    workflow['steps'][0]['next'] = 'signature'
    workflow['steps'].insert(1, {'id': 'signature', 'stage_type': 'document', 'action': 'await_signature', 'document_id': 'declaration', 'user_label': 'Podpisz dokument przed decyzją', 'status': 'DECLARATION_WAITING_FOR_SIGNATURE', 'next': 'review'})
    definition['workflow'] = workflow
    form_id, row, _ = make_submission(admin_app, definition)
    services = admin_app.extensions['services']
    services.submission_repository.update(row['submission_id'], {'declaration_filename': 'declaration.pdf', 'declaration_generated': 'Tak'})
    response = admin_client.get(f"/api/submissions/{row['submission_id']}/acceptance-status", headers={'Authorization': f"Bearer {row['access_token']}"})
    assert response.status_code == 200
    assert response.json['can_sign_documents'] is True
    assert response.json['can_upload_signed_declaration'] is True
    assert response.json['status_title'] == 'Podpisz dokument przed decyzją'
    refreshed = services.submission_repository.get_by_id(row['submission_id'])
    services.document_service._update_submission(refreshed, {'declaration_signature_valid': 'Tak', 'process_status': 'DECLARATION_SIGNED'})
    assert services.submission_repository.get_by_id(row['submission_id'])['workflow_stage'] == 'review'
    assert not services.submission_service.get_submission_context(row['submission_id'])['can_sign_documents']


def test_explicit_submission_decision_event_and_officer_action(admin_app, admin_client, monkeypatch):
    from test_workflow_v2_validation import explicit_workflow
    from services.mail_dispatch_service import MailDispatchResult
    create_user(admin_app)
    definition = load_form_definition('examples/forms/loty.json')
    workflow = explicit_workflow()
    workflow['steps'][1]['decision_email'] = {'enabled': True, 'template_type': 'configured_decision'}
    workflow['steps'].insert(2, {'id': 'office_action', 'admin_label': 'Weryfikacja dokumentu', 'user_label': 'Dokument jest sprawdzany', 'stage_type': 'officer_action', 'status': 'OFFICER_REVIEW', 'next': 'completed'})
    workflow['decision_types'][0]['target_step'] = 'office_action'
    definition['workflow'] = workflow
    form_id, row, _ = make_submission(admin_app, definition)
    services = admin_app.extensions['services']
    pk = services.submission_repository.get_by_id(row['submission_id'])['id']
    deliveries = []
    monkeypatch.setattr(services.mail_dispatch_service, 'select_template', lambda *args: SimpleNamespace(id=1))
    monkeypatch.setattr(services.mail_dispatch_service, 'dispatch_to_submission', lambda **kwargs: deliveries.append(kwargs) or MailDispatchResult('sent'))
    login(admin_client)
    detail = f'/admin/forms/{form_id}/submissions/{pk}'
    html = admin_client.get(detail).get_data(as_text=True)
    csrf = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    result = admin_client.post(detail + '/decision', data={'csrf_token': csrf, 'officer_decision': 'a'})
    assert result.status_code == 302
    assert services.submission_repository.get_by_id(row['submission_id'])['workflow_stage'] == 'office_action'
    assert len(deliveries) == 1
    assert deliveries[0]['event_type'] == 'configured_decision'
    assert deliveries[0]['extra_context']['workflow_step'] == 'review'
    assert admin_client.post(detail + '/complete-action', data={'workflow_step': 'office_action'}).status_code == 400
    admin_client.post(detail + '/complete-action', data={'csrf_token': csrf, 'workflow_step': 'review'})
    assert services.submission_repository.get_by_id(row['submission_id'])['workflow_stage'] == 'office_action'
    admin_client.post(detail + '/complete-action', data={'csrf_token': csrf, 'workflow_step': 'office_action'})
    assert services.submission_repository.get_by_id(row['submission_id'])['workflow_stage'] == 'completed'
    with create_session_factory(admin_app.config['DATABASE_URL'])() as db:
        assert db.get(FormVersion, row['form_version_id']).definition_json == definition
