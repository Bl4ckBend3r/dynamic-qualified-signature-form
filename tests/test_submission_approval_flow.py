from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from database import create_session_factory
from form_loader import load_form_definition
from models import FormSubmission, FormVersion, RepeatableGroupItemDecision, SubmissionDecision, SubmissionWorkflowEvent
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
    cards = html.split('<section id="repeatable-decisions">', 1)[1].split('id="workflow-sla"', 1)[0]
    assert 'data-required-note="false"' in cards
    assert 'Pola oznaczone' not in cards
    assert 'Decyzja <span class="required-marker"' in cards
    for value in ('bilet_bezplatny', 'bilet_ze_znizka', 'brak_biletow_pula_wykorzystana', 'requires_correction'):
        assert f'value="{value}"' in cards
    assert 'IEG → WAW' in cards and '07:30' in cards
    assert html.index('id="repeatable-decisions"') < html.index('id="workflow-sla"')
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
