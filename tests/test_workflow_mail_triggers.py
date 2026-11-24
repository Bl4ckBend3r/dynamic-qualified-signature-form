from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from database import create_session_factory
from models import EmailLog, Form, FormSubmission, FormVersion, MailTemplate, SubmissionWorkflowEvent
from services.mail_dispatch_service import MailDispatchService
from services.workflow_mail_trigger_service import WorkflowMailTriggerService
from test_admin_panel import admin_app, admin_client, create_form, create_user, login


WORKFLOW = {
    "title": "Formularz",
    "fields": [],
    "workflow": {
        "flow_mode": "explicit",
        "initial_step": "start",
        "steps": [
            {"id": "start", "admin_label": "Początek", "status": "FORM_SUBMITTED", "next": "review"},
            {"id": "review", "admin_label": "Weryfikacja zgłoszenia", "status": "REVIEW",
             "decisions": {"accepted": "done", "correction": "start"}},
            {"id": "done", "admin_label": "Zakończenie", "status": "COMPLETED", "final": True},
        ],
    },
}


def test_stage_catalog_uses_current_steps_and_their_own_decisions():
    catalog = WorkflowMailTriggerService()
    stages = catalog.stage_options(SimpleNamespace(definition_json=WORKFLOW))
    assert [(item["value"], item["label"]) for item in stages] == [
        ("start", "Początek"), ("review", "Weryfikacja zgłoszenia"), ("done", "Zakończenie")
    ]
    assert stages[0]["decisions"] == []
    assert {item["value"] for item in stages[1]["decisions"]} == {"accepted", "correction"}
    assert stages[2]["decisions"] == []


def test_legacy_binding_requires_unambiguous_stage():
    catalog = WorkflowMailTriggerService()
    stages = catalog.stage_options(SimpleNamespace(definition_json=WORKFLOW))
    old = SimpleNamespace(trigger_event="", trigger_status="REVIEW", trigger_decision="accepted",
                          is_default_for_status=True)
    assert catalog.binding_for_template(old, stages)["stage"] == "review"
    old.trigger_event = "officer_accepted"
    assert catalog.binding_for_template(old, stages)["warning"]
    old.trigger_event = ""
    old.trigger_status = "UNKNOWN"
    assert catalog.binding_for_template(old, stages)["warning"]


def test_workflow_template_cannot_be_selected_by_legacy_automatic_dispatch():
    template = SimpleNamespace(id=1, is_active=True, trigger_event="workflow_stage",
                               trigger_status="review", trigger_decision="",
                               is_default_for_status=False)
    assert MailDispatchService().select_template([template], event_type="officer_accepted") is None


def test_editor_saves_workflow_binding_without_changing_message(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="mail-stage-form", definition_json=WORKFLOW)
    factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with factory() as db:
        template = MailTemplate(form_id=form_id, name="Wiadomość", subject="Temat",
                                content_html="<p>Treść</p>", html_body="<p>Treść</p>",
                                trigger_event="", trigger_status="", trigger_decision="")
        db.add(template)
        db.commit()
        template_id = template.id
    login(admin_client)
    page = admin_client.get(f"/admin/forms/{form_id}/mail-templates/{template_id}/edit")
    html = page.get_data(as_text=True)
    assert 'data-trigger-decision-field hidden' in html
    assert '"stage_id": "review"' in html or '&#34;stage_id&#34;: &#34;review&#34;' in html
    csrf_token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = admin_client.post(f"/admin/forms/{form_id}/mail-templates/{template_id}/edit", data={
        "csrf_token": csrf_token,
        "name": "Wiadomość", "subject": "Temat", "body_html": "<p>Treść</p>",
        "automatic": "on", "trigger_stage": "review", "trigger_decision": "correction",
        "is_active": "on", "show_process_status": "on",
    })
    assert response.status_code == 302
    with factory() as db:
        saved = db.get(MailTemplate, template_id)
        assert (saved.trigger_event, saved.trigger_status, saved.trigger_decision,
                saved.is_default_for_status) == ("workflow_stage", "review", "correction", True)
        assert (saved.subject, saved.content_html) == ("Temat", "<p>Treść</p>")
    page = admin_client.get(f"/admin/forms/{form_id}/mail-templates/{template_id}/edit")
    csrf_token = page.get_data(as_text=True).split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = admin_client.post(f"/admin/forms/{form_id}/mail-templates/{template_id}/edit", data={
        "csrf_token": csrf_token, "name": "Wiadomość", "subject": "Temat",
        "body_html": "<p>Treść</p>", "is_active": "on",
    })
    assert response.status_code == 302
    with factory() as db:
        saved = db.get(MailTemplate, template_id)
        assert saved.trigger_event == "workflow_stage"
        assert saved.is_default_for_status is False
        assert saved.content_html == "<p>Treść</p>"


def test_unmapped_legacy_trigger_remains_until_admin_rebinds(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug="legacy-mail-trigger", definition_json=WORKFLOW)
    factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with factory() as db:
        template = MailTemplate(form_id=form_id, name="Stary", subject="Temat",
                                html_body="<p>Stara treść</p>", trigger_event="old_event",
                                trigger_status="OLD_STATUS", trigger_decision="old_decision",
                                is_default_for_status=True)
        db.add(template)
        db.commit()
        template_id = template.id
    login(admin_client)
    page = admin_client.get(f"/admin/forms/{form_id}/mail-templates/{template_id}/edit")
    html = page.get_data(as_text=True)
    assert "Historyczne wyzwalanie wymaga ponownego przypisania" in html
    csrf_token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    response = admin_client.post(f"/admin/forms/{form_id}/mail-templates/{template_id}/edit", data={
        "csrf_token": csrf_token, "name": "Stary", "subject": "Temat",
        "body_html": "<p>Stara treść</p>", "is_active": "on",
    })
    assert response.status_code == 302
    with factory() as db:
        saved = db.get(MailTemplate, template_id)
        assert (saved.trigger_event, saved.trigger_status, saved.trigger_decision,
                saved.is_default_for_status) == ("old_event", "OLD_STATUS", "old_decision", True)


@pytest.mark.parametrize("binding,previous,entered,decision,expected", [
    ("entry", "start", "review", "", 1),
    ("entry", "start", "done", "", 0),
    ("decision", "review", "start", "correction", 1),
    ("decision", "review", "done", "accepted", 0),
    ("disabled", "start", "review", "", 0),
])
def test_dispatch_only_for_matching_persisted_event(admin_app, binding, previous, entered, decision, expected):
    form_id = create_form(admin_app, slug=f"mail-dispatch-{binding}-{entered}-{decision or 'none'}",
                          definition_json=WORKFLOW)
    factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with factory() as db:
        form = db.get(Form, form_id)
        version = FormVersion(form_id=form_id, version_major=1, version_minor=0,
                              version_label="1.0", status="published", definition_json=deepcopy(WORKFLOW))
        db.add(version)
        db.flush()
        submission = FormSubmission(submission_id=f"MAIL-{form_id}", form_slug=form.slug,
                                    form_version_id=version.id, form_name=form.name,
                                    email="jan@example.org", workflow_step=entered, workflow_stage=entered)
        db.add(submission)
        db.flush()
        template = MailTemplate(form_id=form_id, name="Wiadomość", subject="Temat",
                                html_body="<p>Treść</p>", content_html="<p>Treść</p>",
                                trigger_event="workflow_stage", trigger_status="review",
                                trigger_decision="correction" if binding == "decision" else "",
                                is_default_for_status=binding != "disabled", is_active=True)
        db.add(template)
        db.flush()
        event = SubmissionWorkflowEvent(submission_id=submission.id,
                                        public_submission_id=submission.submission_id,
                                        form_slug=form.slug, previous_step=previous,
                                        new_step=entered, decision_code=decision)
        db.add(event)
        db.commit()
        sent = []
        dispatcher = MailDispatchService(smtp_sender=lambda **kw: sent.append(kw))
        with admin_app.app_context():
            dispatcher.dispatch_workflow_trigger_event(db=db, form=form, submission=submission, event=event)
            db.commit()
            if expected and binding == "entry":
                log = db.execute(select(EmailLog).where(EmailLog.template_id == template.id)).scalar_one()
                log.status = "queued"
                db.commit()
            dispatcher.dispatch_workflow_trigger_event(db=db, form=form, submission=submission, event=event)
            db.commit()
        assert len(sent) == expected
        assert db.execute(select(EmailLog).where(EmailLog.template_id == template.id)).scalars().all().__len__() == expected


def test_historical_version_controls_stage_match(admin_app):
    current = deepcopy(WORKFLOW)
    current["workflow"]["steps"][1]["id"] = "new_review"
    form_id = create_form(admin_app, slug="historical-mail-stage", definition_json=current)
    factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with factory() as db:
        form = db.get(Form, form_id)
        version = FormVersion(form_id=form_id, version_major=1, version_minor=0,
                              version_label="1.0", status="published", definition_json=deepcopy(WORKFLOW))
        db.add(version)
        db.flush()
        submission = FormSubmission(submission_id="historical-mail-1", form_slug=form.slug,
                                    form_version_id=version.id, email="jan@example.org", workflow_stage="review")
        db.add(submission)
        db.flush()
        db.add(MailTemplate(form_id=form_id, name="Historyczny", subject="Temat", html_body="<p>Treść</p>",
                            trigger_event="workflow_stage", trigger_status="review", is_default_for_status=True))
        event = SubmissionWorkflowEvent(submission_id=submission.id, public_submission_id=submission.submission_id,
                                        form_slug=form.slug, previous_step="start", new_step="review")
        db.add(event)
        db.commit()
        sent = []
        with admin_app.app_context():
            MailDispatchService(smtp_sender=lambda **kw: sent.append(kw)).dispatch_workflow_trigger_event(
                db=db, form=form, submission=submission, event=event)
        assert len(sent) == 1


def test_workflow_service_consumes_committed_event_once(admin_app):
    form_id = create_form(admin_app, slug="committed-mail-stage", definition_json=WORKFLOW)
    factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with factory() as db:
        form = db.get(Form, form_id)
        version = FormVersion(form_id=form_id, version_major=1, version_minor=0,
                              version_label="1.0", status="published", definition_json=deepcopy(WORKFLOW))
        db.add(version)
        db.flush()
        submission = FormSubmission(submission_id="committed-mail-1", form_slug=form.slug,
                                    form_version_id=version.id, email="jan@example.org", workflow_stage="review")
        db.add(submission)
        db.flush()
        db.add(MailTemplate(form_id=form_id, name="Etap", subject="Temat", html_body="<p>Treść</p>",
                            trigger_event="workflow_stage", trigger_status="review", is_default_for_status=True))
        db.add(SubmissionWorkflowEvent(submission_id=submission.id,
                                       public_submission_id=submission.submission_id,
                                       form_slug=form.slug, source="workflow_transition_to",
                                       previous_step="start", new_step="review"))
        db.commit()
    sent = []
    services = admin_app.extensions["services"]
    original = services.mail_dispatch_service.smtp_sender
    services.mail_dispatch_service.smtp_sender = lambda **kw: sent.append(kw)
    try:
        with admin_app.app_context():
            services.workflow_service.dispatch_persisted_workflow_event(
                "committed-mail-1", source="workflow_transition_to")
            services.workflow_service.dispatch_persisted_workflow_event(
                "committed-mail-1", source="workflow_transition_to")
    finally:
        services.mail_dispatch_service.smtp_sender = original
    assert len(sent) == 1
