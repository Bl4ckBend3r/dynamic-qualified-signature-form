from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from database import create_session_factory
from models import EmailLog, FormVersion, PlatformMailTemplate
from test_admin_panel import admin_app, create_form


@pytest.mark.parametrize('failure', [None, 'smtp', 'invalid_email'])
def test_confirmation_targets_active_records_and_retries_only_failures(admin_app, monkeypatch, failure):
    form_id = create_form(admin_app, slug='delegation')
    people = [{'record_uuid': str(uuid4()), 'given': name, 'family': 'Test', 'contact': f'{name.lower()}@example.org'} for name in ('Jan', 'Anna', 'Ewa')]
    if failure == 'invalid_email':
        people[1]['contact'] = 'invalid'
    group = {'type': 'repeatable_group', 'name': 'delegates', 'applicant_record': True,
        'submission_confirmation': {'enabled': True, 'email_field': 'contact'},
        'decision_contact_email_field': 'contact',
        'item_context_fields': {'first_name': 'given', 'last_name': 'family', 'email': 'contact'},
        'fields': [{'name': 'given', 'type': 'text'}, {'name': 'family', 'type': 'text'}, {'name': 'contact', 'type': 'email'}]}
    inactive = {**deepcopy(group), 'name': 'inactive', 'visible_if': {'field': 'kind', 'equals': 'other'}}
    definition = {'title': 'Delegation', 'fields': [group, inactive], 'workflow': {'initial_step': 'submission', 'steps': [{'id': 'submission', 'type': 'form_submit'}]}}
    factory = create_session_factory(admin_app.config['DATABASE_URL'])
    with factory() as db:
        version = FormVersion(form_id=form_id, version_major=1, version_minor=0, version_label='1.0', status='published', definition_json=definition)
        db.add(version)
        template = db.execute(select(PlatformMailTemplate).where(PlatformMailTemplate.template_type == 'submission_received')).scalar_one_or_none()
        if template is None:
            template = PlatformMailTemplate(template_type='submission_received', name='Potwierdzenie')
            db.add(template)
        template.is_active = True
        template.subject = 'Zgłoszenie {{ participant.first_name }}'
        template.html_body = '<p>{{ participant.full_name }} {{ participant.email }} {{ submission_id }}</p><a href="{{ status_url }}">Status</a><a href="{{ participant_action_url }}">Czynności</a>'
        template.text_body = '{{ participant.first_name }} {{ status_url }}'
        db.commit()
        version_id = version.id
    calls = []
    fail_smtp = failure == 'smtp'
    def sender(**kwargs):
        calls.append(kwargs)
        if fail_smtp and kwargs['to_emails'] == [people[1]['contact']]:
            raise RuntimeError('SMTP unavailable')
    services = admin_app.extensions['services']
    services.mail_dispatch_service.smtp_sender = sender
    monkeypatch.setattr(services.mail_dispatch_service.mail_settings_service, 'resolve_smtp', lambda *args: {})
    with admin_app.test_request_context('/'):
        row = services.submission_service.create_submission('delegation', definition,
            {'data_json': {'kind': 'active', 'delegates': people, 'inactive': [{'record_uuid': str(uuid4()), 'contact': 'hidden@example.org'}],
                           '_applicant_record_ids': {'delegates': people[0]['record_uuid']}}}, form_version_id=version_id)
        assert services.submission_repository.get_by_id(row['submission_id']) is not None
        expected = [person['contact'] for person in people if person['contact'] != 'invalid']
        assert [call['to_emails'] for call in calls] == [[email] for email in expected]
        for call in calls:
            recipient = call['to_emails'][0]
            for other in people:
                if other['contact'] != recipient:
                    assert other['contact'] not in call['html_body']
            assert (row['access_token'] in call['html_body']) == (recipient == people[0]['contact'])
        count = len(calls)
        fail_smtp = False
        services.mail_dispatch_service.dispatch_submission_received(row['submission_id'])
        assert len(calls) == count + (1 if failure == 'smtp' else 0)
        with factory() as db:
            logs = db.execute(select(EmailLog).where(EmailLog.event_type == 'submission_created')).scalars().all()
            assert len({log.repeatable_item_id for log in logs}) == 3
            assert all(row['access_token'] not in log.html_body for log in logs)
            if failure:
                assert any(log.status == 'failed' for log in logs)
