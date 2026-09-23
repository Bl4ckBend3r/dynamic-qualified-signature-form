from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from werkzeug.datastructures import MultiDict

from models import EmailLog, Form, FormSubmission, FormVersion, MailTemplate, RepeatableGroupItemDecision
from services.admin_form_service import _workflow_email_notifications
from services.mail_recipient_service import MailRecipientResolver
from test_admin_panel import admin_app, create_form


@pytest.fixture
def record_mail_env(admin_app, monkeypatch):
    services = admin_app.extensions['services']
    form_id = create_form(admin_app, slug='record-mail')
    people = [{'record_uuid': str(uuid4()), 'given': name, 'family': f'Family{name}',
               'contact': f'{name.lower()}@example.org', 'phone': f'55500000{i}', 'private_note': 'not-in-mail'}
              for i, name in enumerate(('Jan', 'Anna', 'Piotr'))]
    group = {'type': 'repeatable_group', 'name': 'delegates', 'label': 'Delegaci', 'applicant_record': True,
        'visible_if': {'field': 'kind', 'equals': 'active'}, 'decision_contact_email_field': 'contact',
        'item_context_fields': {'first_name': 'given', 'last_name': 'family', 'email': 'contact', 'phone': 'phone'},
        'fields': [{'name': 'given', 'type': 'text'}, {'name': 'family', 'type': 'text'},
                   {'name': 'contact', 'type': 'email'}, {'name': 'phone', 'type': 'tel'}]}
    source = {'type': 'repeatable_group', 'group': 'delegates', 'email_field': 'contact'}
    definition = {'title': 'Powiadomienia', 'fields': [group, {**deepcopy(group), 'name': 'inactive',
        'visible_if': {'field': 'kind', 'equals': 'other'}}], 'workflow': {'flow_mode': 'explicit', 'initial_step': 'review',
        'steps': [{'id': 'review', 'stage_type': 'decision', 'decision_email': {'enabled': True, 'template_type': 'notice'}}],
        'email_notifications': [{'id': 'notice', 'enabled': True, 'template_type': 'notice', 'recipient_source': source}]}}
    repo = services.submission_repository
    with repo.session_factory() as db:
        version = FormVersion(form_id=form_id, version_major=1, version_minor=0, version_label='1.0', status='published', definition_json=definition)
        db.add(version)
        db.flush()
        model = FormSubmission(submission_id=str(uuid4()), form_slug='record-mail', form_version_id=version.id,
            email='owner@example.org', access_token='secret-submission-wide-token', workflow_stage='review', workflow_step='review',
            process_status='WAITING_FOR_OFFICER_DECISION', data_json={'kind': 'active', 'delegates': people,
            'inactive': [{'record_uuid': str(uuid4()), 'given': 'Hidden', 'contact': 'hidden@example.org'}],
            'private_root': 'secret-root', '_applicant_record_ids': {'delegates': people[0]['record_uuid']}})
        db.add(model)
        template = MailTemplate(form_id=form_id, name='Informacja dla osoby', template_type='notice', trigger_event='notice', is_active=True,
            subject='Informacja {{ participant.first_name }}', html_body='<p>{{ participant.record_uuid }} {{ participant.full_name }} {{ participant.email }} {{ participant.phone }}</p><a href="{{ status_url }}">Status</a><a href="{{ participant_action_url }}">Czynności</a><p>{{ decision_label }}</p>',
            text_body='{{ participant.record_uuid }} {{ participant.full_name }} {{ participant.email }} {{ status_url }} {{ decision_label }}')
        db.add(template)
        db.commit()
        pk, template_id = model.id, template.id
    calls, contexts, failures = [], [], set()
    def sender(**kwargs):
        calls.append(kwargs)
        if kwargs['to_emails'][0] in failures:
            raise RuntimeError('SMTP unavailable')
    services.mail_dispatch_service.smtp_sender = sender
    monkeypatch.setattr(services.mail_dispatch_service.mail_settings_service, 'resolve_smtp', lambda *args: {})
    import services.mail_dispatch_service as mail_module
    render_html = mail_module.render_platform_mail_html
    def render(template, context, **kwargs):
        contexts.append(deepcopy(context))
        return render_html(template, context, **kwargs)
    monkeypatch.setattr(mail_module, 'render_platform_mail_html', render)
    def dispatch(event='status_changed', event_id='event-1', **kwargs):
        with repo.session_factory() as db:
            return services.mail_dispatch_service.dispatch_to_submission(db=db, form=db.get(Form, form_id),
                submission=db.get(FormSubmission, pk), template=db.get(MailTemplate, template_id),
                event_type=event, event_id=event_id, **kwargs)
    def logs():
        with repo.session_factory() as db:
            return db.execute(select(EmailLog).where(EmailLog.submission_id == pk).order_by(EmailLog.id)).scalars().all()
    env = SimpleNamespace(app=admin_app, services=services, repo=repo, form_id=form_id, pk=pk, template_id=template_id,
        people=people, definition=definition, source=source, calls=calls, contexts=contexts, failures=failures, dispatch=dispatch, logs=logs)
    with admin_app.test_request_context('/'):
        yield env


@pytest.mark.parametrize('event', ['status_changed', 'document_generated', 'process_completed'])
def test_a_broadcast_sends_one_message_per_record(record_mail_env, event):
    env = record_mail_env
    result = env.dispatch(event)
    assert result.sent and len(result.deliveries) == 3
    assert [call['to_emails'] for call in env.calls] == [[p['contact']] for p in env.people]
    assert len(env.contexts) == len(env.logs()) == 3
    assert all('cc' not in call and 'bcc' not in call and call['attachments'] == [] for call in env.calls)


def test_b_personal_context_and_action_authorization(record_mail_env):
    env = record_mail_env
    env.dispatch(extra_context={'data_json': {'leak': 'other-person'}, 'document_url': 'secret-link', 'participant': env.people[2]}, attachments=[{'filename': 'whole-submission.pdf'}])
    for index, (call, context) in enumerate(zip(env.calls, env.contexts)):
        person = env.people[index]
        assert context['participant']['record_uuid'] == person['record_uuid']
        assert context['participant']['phone'] == person['phone']
        assert 'data_json' not in context and 'values' not in context['participant']
        assert 'secret-root' not in call['html_body'] and 'not-in-mail' not in call['html_body']
        for other in env.people:
            if other is not person:
                assert other['record_uuid'] not in call['html_body'] and other['contact'] not in call['text_body']
        assert ('secret-submission-wide-token' in call['html_body']) is (index == 0)
        assert context['status_url'] == context['public_status_url']
        assert 'token=' not in context['status_url']
        if index:
            assert context['participant_action_url'] == '' and context['status_url'] == context['public_status_url']
    assert all('secret-submission-wide-token' not in log.html_body for log in env.logs())


@pytest.mark.parametrize('substate,credential', [('ready', True), ('upload', True),
    ('awaiting_office_signature', True), ('ready', False)])
def test_composite_mail_links_and_preview_use_recipient_context(record_mail_env, substate, credential, caplog):
    from services.mail_template_service import render_platform_mail_text, render_template_text
    env = record_mail_env
    definition = deepcopy(env.definition)
    definition['documents'] = [{'id': 'custom-paper', 'label': 'Pismo', 'template_html': '<p>Pismo</p>'}]
    definition['workflow']['steps'] = [{'id': 'arbitrary-stage', 'type': 'document', 'stage_type': 'document',
        'document_lifecycle': 'composite', 'document_id': 'custom-paper', 'document_options': {
            'download': True, 'participant_signature': True, 'upload_required': True, 'office_signature': True}}]
    with env.repo.session_factory() as db:
        submission = db.get(FormSubmission, env.pk)
        submission.form_version.definition_json = definition
        submission.workflow_step = submission.workflow_stage = 'arbitrary-stage'
        submission.document_states = {'workflow_documents': {'arbitrary-stage': {
            'substate': substate, 'files': [{'filename': 'document.pdf'}]}}}
        if not credential:
            submission.access_token = ''
        template = db.get(MailTemplate, env.template_id)
        template.html_body = '<p>Dzień dobry {{ participant.first_name }} / {{ imie }}. Numer {{ submission_id }}.</p><a href="{{ public_status_url }}">Status</a><a href="{{ document_url }}">Pobierz</a><a href="{{ podpisz_url }}">Podpisz</a>'
        template.text_body = '{{ participant.first_name }} {{ imie }} {{ submission_id }} {{ public_status_url }} {{ document_url }} {{ podpisz_url }}'
        db.commit()
    assert env.dispatch().sent
    assert len(env.calls) == 3
    for index, (call, context) in enumerate(zip(env.calls, env.contexts)):
        allowed = index == 0 and credential
        assert context['imie'] == env.people[index]['given']
        assert bool(context['document_url']) == allowed
        assert bool(context['podpisz_url']) == (allowed and substate != 'awaiting_office_signature')
        assert 'token=' not in context['public_status_url']
        assert '{{' not in call['text_body']
        if allowed:
            assert '/arbitrary-stage/download/document.pdf?' in context['document_url']
        with env.repo.session_factory() as db:
            template = db.get(MailTemplate, env.template_id)
            assert render_template_text(template.subject, context) == call['subject']
            assert render_platform_mail_text(template, context) == call['text_body']
    assert 'missing_special_link=document_url' in caplog.text
    assert 'secret-submission-wide-token' not in caplog.text
    assert all('secret-submission-wide-token' not in log.html_body for log in env.logs())


def test_c_item_event_targets_only_its_uuid(record_mail_env):
    env = record_mail_env
    uuid_b = env.people[1]['record_uuid']
    env.dispatch('decision', record_uuid=uuid_b, group_key='delegates', extra_context={'decision_label': 'Bilet ze zniżką'})
    assert len(env.calls) == 1 and env.calls[0]['to_emails'] == [env.people[1]['contact']]
    assert 'Bilet ze zniżką' in env.calls[0]['html_body']
    assert env.logs()[0].repeatable_item_id == uuid_b
    env.dispatch('decision', event_id='wrong-item', record_uuid=str(uuid4()), group_key='delegates')
    assert len(env.calls) == 1


def test_d_inactive_and_empty_groups_never_fall_back_to_owner(record_mail_env):
    env = record_mail_env
    assert env.dispatch(recipient_source={**env.source, 'group': 'inactive'}).status == 'skipped'
    with env.repo.session_factory() as db:
        model = db.get(FormSubmission, env.pk)
        model.data_json = {**model.data_json, 'delegates': []}
        db.commit()
    assert env.dispatch().status == 'skipped'
    assert env.calls == [] and env.logs() == []


def test_e_idempotency_uses_record_event_and_template_version(record_mail_env):
    env = record_mail_env
    with env.repo.session_factory() as db:
        model = db.get(FormSubmission, env.pk)
        data = deepcopy(model.data_json)
        data['delegates'][1]['contact'] = data['delegates'][0]['contact']
        model.data_json = data
        db.commit()
    env.dispatch()
    assert len(env.calls) == 3 and env.calls[0]['to_emails'] == env.calls[1]['to_emails']
    assert env.dispatch().status == 'skipped' and len(env.calls) == 3
    assert len({json.loads(log.administrator_message)['idempotency_key'] for log in env.logs()}) == 3
    env.dispatch(event_id='event-2')
    assert len(env.calls) == 6
    with env.repo.session_factory() as db:
        db.get(MailTemplate, env.template_id).subject = 'Nowa wersja {{ participant.first_name }}'
        db.commit()
    env.dispatch(event_id='event-2')
    assert len(env.calls) == 9


def test_f_partial_failure_retries_only_failed_record(record_mail_env):
    env = record_mail_env
    env.failures.add(env.people[1]['contact'])
    result = env.dispatch()
    assert result.status == 'failed'
    assert [log.status for log in env.logs()] == ['sent', 'failed', 'sent']
    env.failures.clear()
    assert env.dispatch().sent
    assert len(env.calls) == 4 and env.calls[-1]['to_emails'] == [env.people[1]['contact']]
    assert [log.status for log in env.logs()] == ['sent', 'failed', 'sent', 'sent']
    with env.repo.session_factory() as db:
        assert db.get(FormSubmission, env.pk).workflow_stage == 'review'


def test_recipient_config_validation_and_editor_preservation(record_mail_env):
    env = record_mail_env
    assert MailRecipientResolver.validate_source(env.source, env.definition) == []
    assert MailRecipientResolver.validate_source({**env.source, 'email_field': 'given'}, env.definition)
    assert MailRecipientResolver.validate_source({**env.source, 'group': 'missing'}, env.definition)
    existing = env.definition['workflow']['email_notifications']
    assert next(n for n in _workflow_email_notifications(MultiDict(), existing) if n['id'] == 'notice') == existing[0]
    post = MultiDict({'notification_id': 'notice', 'notification_notice_enabled': 'on', 'notification_notice_template': 'notice',
        'notification_notice_recipient_type': 'repeatable_group', 'notification_notice_recipient_group': 'delegates', 'notification_notice_recipient_email': 'contact'})
    assert next(n for n in _workflow_email_notifications(post, existing) if n['id'] == 'notice')['recipient_source'] == env.source


def test_persisted_item_decision_event_retries_snapshot_without_broadcast(record_mail_env):
    env = record_mail_env
    person = env.people[1]
    with env.repo.session_factory() as db:
        decision = RepeatableGroupItemDecision(submission_id=env.pk, group_key='delegates', item_id=person['record_uuid'],
            decision_code='discount', decision_label='Bilet ze zniżką', semantic_category='positive', workflow_step='review',
            comment='Decyzja tylko dla Anny', participant_snapshot_json={'record_uuid': person['record_uuid'],
            'first_name': person['given'], 'last_name': person['family'], 'full_name': 'Anna FamilyAnna',
            'email': person['contact'], 'phone': person['phone'], 'decision_label': 'Bilet ze zniżką', 'values': {'secret': 'not-in-mail'}})
        db.add(decision)
        db.commit()
        decision_id = decision.id
    def dispatch_decision():
        with env.repo.session_factory() as db:
            decision = db.get(RepeatableGroupItemDecision, decision_id)
            decision.was_created = False
            return env.services.mail_dispatch_service.dispatch_workflow_decision_event(db=db, form=db.get(Form, env.form_id),
                submission=db.get(FormSubmission, env.pk), decision=decision)
    env.failures.add(person['contact'])
    assert dispatch_decision().status == 'failed'
    env.failures.clear()
    assert dispatch_decision().sent
    dispatch_decision()
    assert len(env.calls) == 2 and all(call['to_emails'] == [person['contact']] for call in env.calls)
    assert all(log.item_decision_id == decision_id and log.repeatable_item_id == person['record_uuid'] for log in env.logs())
    assert all('values' not in context['participant'] for context in env.contexts)


def test_correction_event_partial_retry_does_not_use_submission_wide_sent_flag(record_mail_env):
    from datetime import datetime, timezone
    env = record_mail_env
    with env.repo.session_factory() as db:
        submission = db.get(FormSubmission, env.pk)
        definition = deepcopy(submission.form_version.definition_json)
        definition['workflow']['send_email_notifications'] = True
        definition['workflow']['email_notifications'][0]['template_type'] = 'correction_accepted'
        submission.form_version.definition_json = definition
        submission.correction_completed_at = datetime.now(timezone.utc)
        submission.email = ''
        template = db.get(MailTemplate, env.template_id)
        template.template_type = template.trigger_event = 'correction_accepted'
        db.commit()
        public_id = submission.submission_id
    env.failures.add(env.people[1]['contact'])
    assert env.services.mail_dispatch_service.dispatch_correction_accepted(public_id).status == 'failed'
    env.failures.clear()
    assert env.services.mail_dispatch_service.dispatch_correction_accepted(public_id).sent
    assert len(env.calls) == 4 and env.calls[-1]['to_emails'] == [env.people[1]['contact']]


def test_alternate_notification_address_does_not_receive_applicant_token(record_mail_env):
    env = record_mail_env
    with env.repo.session_factory() as db:
        submission = db.get(FormSubmission, env.pk)
        definition = deepcopy(submission.form_version.definition_json)
        definition['fields'][0]['fields'].append({'name': 'alternate', 'type': 'email'})
        definition['workflow']['email_notifications'][0]['recipient_source']['email_field'] = 'alternate'
        submission.form_version.definition_json = definition
        data = deepcopy(submission.data_json)
        for i, person in enumerate(data['delegates']):
            person['alternate'] = f'notification-{i}@example.org'
        submission.data_json = data
        db.commit()
    env.dispatch()
    assert len(env.calls) == 3
    assert all('secret-submission-wide-token' not in call['html_body'] for call in env.calls)
    assert all(context['participant_action_url'] == '' for context in env.contexts)
