from threading import Thread
from types import SimpleNamespace
from uuid import uuid4
from pathlib import Path

import pytest

from werkzeug.serving import make_server

from database import create_session_factory
from form_loader import load_form_definition
from models import Form, FormVersion, FormSubmission
from test_admin_panel import admin_app, create_user
from test_submission_approval_flow import make_submission
from test_composite_document_workflow import composite_env
from test_signature_verifier import signed_pdf_factory
from test_repeatable_mail_recipients import record_mail_env


def test_repeatable_mail_recipient_editor_persists_draft(record_mail_env, participant_page):
    from copy import deepcopy
    from playwright.sync_api import expect
    from test_workflow_v2_validation import explicit_workflow
    env = record_mail_env
    create_user(env.app)
    definition = deepcopy(env.definition)
    definition['workflow'] = {**explicit_workflow(), 'email_notifications': definition['workflow']['email_notifications']}
    definition['fields'].insert(0, {'name': 'kind', 'type': 'text', 'label': 'Rodzaj'})
    with env.repo.session_factory() as db:
        form = db.get(Form, env.form_id)
        draft = FormVersion(form_id=form.id, version_major=1, version_minor=1, version_label='1.1', status='draft', definition_json=definition)
        db.add(draft)
        env.services.form_version_service.apply_to_legacy_editor(db, form, definition)
        db.commit()
    server = make_server('127.0.0.1', 0, env.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = participant_page
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        page.goto(f'{base}/admin/')
        page.locator('[name="email"]').fill('admin@example.com')
        page.locator('[name="password"]').fill('secret')
        page.locator('button[type="submit"]').click()
        assert page.goto(f'{base}/admin/forms/{env.form_id}/edit?tab=emails').status == 200
        source = page.locator('[name="notification_notice_recipient_type"]')
        expect(source).to_have_value('repeatable_group')
        source.select_option('submission')
        expect(page.locator('[name="notification_notice_recipient_group"]')).to_be_hidden()
        source.select_option('repeatable_group')
        page.locator('[name="notification_notice_recipient_group"]').select_option('delegates')
        page.locator('[name="notification_notice_recipient_email"]').select_option('contact')
        page.locator('[name="notification_notice_event"]').fill('status_changed')
        with page.expect_response(lambda response: response.request.method == 'POST' and '/edit' in response.url) as response:
            page.get_by_role('button', name='Zapisz zmiany', exact=True).click()
        assert response.value.status == 302, page.locator('body').inner_text()[:3500]
        with env.repo.session_factory() as db:
            draft = db.query(FormVersion).filter_by(form_id=env.form_id, status='draft').one()
            saved = next(n for n in draft.definition_json['workflow']['email_notifications'] if n['id'] == 'notice')
            assert saved['event'] == 'status_changed' and saved['recipient_source'] == env.source
            published = db.query(FormVersion).filter_by(form_id=env.form_id, status='published').one()
            assert published.definition_json == env.definition
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_admin_person_cards_and_decision_catalog_in_browser(admin_app, participant_page):
    create_user(admin_app)
    definition = load_form_definition('examples/forms/loty.json')
    form_id, row, people = make_submission(admin_app, definition, step='dit_decision_urzednik')
    pk = admin_app.extensions['services'].submission_repository.get_by_id(row['submission_id'])['id']
    with create_session_factory(admin_app.config['DATABASE_URL'])() as db:
        submission = db.get(FormSubmission, pk)
        third = {**people[0], 'record_uuid': str(uuid4()), 'imie': 'Ewa', 'email': 'ewa@example.org'}
        submission.data_json = {**submission.data_json, 'podrozni': [*people, third]}
        service = admin_app.extensions['services'].decision_definition_service
        service.decide_item(db, submission, group_key='podrozni', item_id=people[0]['record_uuid'], decision_code='bilet_bezplatny', comment='', actor=SimpleNamespace(id=None))
        for option in definition['workflow']['decision_types']:
            if option['step_id'] == 'dit_decision_urzednik':
                service.save_definition(db, code=option['code'], label=option['label'], category=option['semantic_category'], sort_order=option['sort_order'])
        form = db.get(Form, form_id)
        db.add(FormVersion(form_id=form_id, version_major=1, version_minor=1, version_label='1.1', status='draft', definition_json=definition))
        admin_app.extensions['services'].form_version_service.apply_to_legacy_editor(db, form, definition)
        db.commit()
    server = make_server('127.0.0.1', 0, admin_app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = participant_page
    page.set_viewport_size({'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        page.goto(f'{base}/admin/')
        page.locator('[name="email"]').fill('admin@example.com')
        page.locator('[name="password"]').fill('secret')
        page.locator('button[type="submit"]').click()
        assert page.goto(f'{base}/admin/forms/{form_id}/submissions/{pk}').status == 200
        card = page.locator('.admin-repeatable-decision-item').first
        assert page.locator('.admin-repeatable-decision-item').count() == 3
        assert card.locator('.admin-person-history details').evaluate('el => !el.open')
        assert card.get_by_text('Historia decyzji (1)', exact=True).is_visible()
        assert card.get_by_role('button', name='Wyślij ponownie e-mail').is_hidden()
        assert card.locator('.admin-person-summary dt').filter(has_text='Imię').count() == 0
        assert card.locator('.admin-person-current-decision .admin-badge').inner_text() == 'Bilet bezpłatny'
        assert page.locator('.admin-repeatable-decision-item').nth(1).locator('.admin-person-current-decision .admin-badge').inner_text() == 'Brak decyzji'
        for select in page.locator('[data-item-decision] select').all():
            assert select.locator('option').all_text_contents() == ['Wybierz decyzję', 'Bilet bezpłatny', 'Bilet ze zniżką', 'Brak biletów — pula wykorzystana']
        assert 90 <= card.locator('[name="comment"]').bounding_box()['height'] <= 110
        forms = page.locator('.admin-person-decision')
        assert abs(forms.nth(0).bounding_box()['y'] - forms.nth(1).bounding_box()['y']) < 2
        assert page.locator('#repeatable-decisions .admin-required-note').count() == 0
        assert card.locator('label').filter(has_text='Decyzja').locator('.required-marker').count() == 1
        card.locator('[name="decision_code"]').select_option('brak_biletow_pula_wykorzystana')
        assert card.locator('[name="comment"]').evaluate('el => el.required')
        assert card.locator('[data-reason-marker]').is_visible()
        card.locator('[name="decision_code"]').select_option('bilet_ze_znizka')
        assert not card.locator('[name="comment"]').evaluate('el => el.required')
        page.set_viewport_size({'width': 390, 'height': 900})
        assert page.locator('.admin-repeatable-decision-grid').evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ').length") == 1
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.goto(f'{base}/admin/decision-types')
        assert page.locator('.admin-decision-type').count() == 3
        assert page.locator('.admin-required-note').count() == 0
        add = page.get_by_role('button', name='Dodaj typ decyzji', exact=True)
        assert 40 <= add.bounding_box()['height'] <= 46
        assert add.bounding_box()['width'] < 250
        assert page.locator('.admin-decision-type__edit input').first.is_hidden()
        page.locator('.admin-decision-type__edit summary').first.click()
        assert page.locator('.admin-decision-type__edit input[name="label"]').first.is_visible()
        page.set_viewport_size({'width': 390, 'height': 900})
        assert page.locator('.admin-decision-types').evaluate('el => el.scrollWidth <= el.clientWidth')
        assert not errors
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_workflow_stage_editor_js_catalog_identity_groups_and_all(participant_page):
    """Exercise the actual DOM editor without the server, including legacy snapshots."""
    page = participant_page
    page.set_content('''<article data-workflow-step>
      <script type="application/json" data-workflow-step-source>{"id":"review","stage_type":"decision"}</script>
      <input data-step-id value="review"><input data-step-user-label value="Decyzja">
      <select data-step-stage-type><option value="decision">Decyzja</option></select>
      <label><select data-step-next></select></label><div class="workflow-stage-options"></div>
      <div data-workflow-step-editor></div><dl data-step-summary></dl>
    </article>''')
    page.add_script_tag(content=Path('static/js/workflow_stage_editor.js').read_text(encoding='utf-8'))
    result = page.evaluate('''() => {
      window.workflowFields = [{type: 'repeatable_group', name: 'people', label: 'Osoby',
        decision_completion: {policy: 'ALL', step_ids: ['review'], next: 'completed'}}];
      window.workflowDecisionCatalog = [
        {definition_id: 7, code: 'new_code', label: 'Bilet bezpłatny'},
        {definition_id: 8, code: 'other_code', label: 'Bilet bezpłatny'}];
      const card = document.querySelector('article');
      WorkflowStageEditor.initialize(card, {decision_types: [
        {definition_id: 7, code: 'old_code', label: 'Bilet bezpłatny', step_id: 'review', scope: 'item', target_step: 'completed'},
        {definition_id: 7, code: 'old_code', label: 'Bilet bezpłatny', step_id: 'review', scope: 'item', target_step: 'completed'}]});
      WorkflowStageEditor.sync(card, [{id: 'completed', label: 'Proces zakończony'}]);
      return {rows: card.querySelectorAll('[data-decision-option]').length,
        codes: [...card.querySelectorAll('[data-decision-option]')].map(r => r.dataset.decisionOption),
        stage: WorkflowStageEditor.collect(card), options: WorkflowStageEditor.decisions([card])};
    }''')
    assert result['rows'] == 2
    assert result['codes'] == ['old_code', 'other_code']
    assert len(result['options']) == 1
    assert result['stage']['decision_group'] == 'people'
    assert result['stage']['completion_next'] == 'completed'
    assert result['stage']['decision_scope'] == 'item'
    result = page.evaluate('''() => {
      const card = document.querySelector('article');
      const stage = {...WorkflowStageEditor.collect(card), id: 'review', admin_label: 'Decyzja', user_label: 'Decyzja', status: 'WAITING_FOR_REVIEW'};
      const options = WorkflowStageEditor.decisions([card]);
      const config = {initial_step: 'review', steps: [stage,
        {id: 'completed', admin_label: 'Koniec', user_label: 'Koniec', status: 'COMPLETED', stage_type: 'final', final: true, transitions: []}],
        decision_types: [options[0], {...options[0], code: 'different_code'}]};
      const duplicateErrors = WorkflowStageEditor.validate(config).errors;
      window.workflowFields = [];
      delete card.dataset.stageEditorReady;
      card.querySelector('[data-workflow-step-editor]').replaceChildren();
      WorkflowStageEditor.initialize(card, {flow_mode: 'explicit', decision_types: []});
      const noGroups = card.querySelector('[data-stage-field="decision_scope"] option[value="item"]').disabled;
      config.steps[0].decision_group = 'missing';
      const invalidGroup = WorkflowStageEditor.validate(config).issues.some(i => i.field === 'decision_group');
      return {duplicateErrors, noGroups, invalidGroup};
    }''')
    assert any('tylko raz' in error for error in result['duplicateErrors'])
    assert result['noGroups']
    assert result['invalidGroup']


@pytest.mark.parametrize('split_draft', [False, True])
def test_composite_document_workflow_builder(composite_env, participant_page, split_draft):
    from copy import deepcopy
    from playwright.sync_api import expect
    from test_composite_document_workflow import split_document_draft
    env = composite_env()
    create_user(env.app)
    with env.repo.session_factory() as db:
        form = db.get(Form, env.form_id)
        draft = FormVersion(form_id=env.form_id, version_major=1, version_minor=1, version_label='1.1', status='draft', definition_json=deepcopy(env.definition))
        if split_draft:
            draft.definition_json = split_document_draft(already_composite=True)
            assert len(draft.definition_json['workflow']['steps']) == 7
        db.add(draft)
        if split_draft:
            env.services.form_version_service.apply_to_legacy_editor(db, form, draft.definition_json)
        db.commit()
    server = make_server('127.0.0.1', 0, env.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = participant_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        page.goto(f'{base}/admin/')
        page.locator('[name="email"]').fill('admin@example.com')
        page.locator('[name="password"]').fill('secret')
        page.locator('button[type="submit"]').click()
        assert page.goto(f'{base}/admin/forms/{env.form_id}/edit?tab=workflow').status == 200
        cards = page.locator('[data-workflow-step-list] > [data-workflow-step]')
        assert cards.count() == 4
        assert page.locator('[data-document-conversion-notice]').count() == int(split_draft)
        card = cards.nth(1)
        card.locator('[data-edit-step-instruction]').click()
        assert not errors
        assert card.locator('[data-document-editor]').is_visible(), card.locator('[data-step-stage-type]').input_value()
        assert card.locator('[data-stage-field="document_id"]').input_value() == 'declaration'
        assert card.locator('[data-step-next]').is_visible()
        assert card.locator('[data-stage-field="action"]').is_hidden()
        assert card.locator('[data-stage-field="document_participant_signature"]').is_checked()
        card.locator('[data-stage-field="document_download"]').uncheck()
        assert card.locator('[data-stage-validity="invalid"]').count() == 0
        document_select = card.locator('[data-stage-field="document_id"]')
        document_select.select_option('')
        assert card.locator('[data-stage-validity="invalid"]').inner_text() == 'Wymaga konfiguracji'
        assert 'Wybierz definicję dokumentu.' in document_select.locator('..').inner_text()
        document_select.select_option('declaration')
        card.locator('[data-stage-field="document_download"]').uncheck()
        assert card.locator('[data-stage-validity="invalid"]').count() == 0
        card.locator('[data-stage-field="document_download"]').check()
        details = card.locator('[data-document-substate="ready"]')
        details.locator('summary').click()
        details.locator('textarea').first.fill('Pobierz dokument i zachowaj jego kopię.')
        card.locator('[data-stage-field="document_download"]').uncheck()
        assert details.is_hidden()
        assert card.locator('[data-document-substate="awaiting_office_signature"]').is_hidden()
        if split_draft:
            agreement = cards.nth(2)
            agreement.locator('[data-edit-step-instruction]').click()
            assert agreement.locator('[data-document-substate="awaiting_office_signature"]').is_visible()
        card.locator('[data-step-stage-type]').select_option('system')
        assert card.locator('[data-document-editor]').is_hidden()
        card.locator('[data-step-stage-type]').select_option('document')
        config = page.evaluate('collectWorkflow()')
        assert config['steps'][1]['document_lifecycle'] == 'composite'
        assert config['steps'][1]['document_instructions']['ready']['description'] == 'Pobierz dokument i zachowaj jego kopię.'
        assert page.evaluate('WorkflowStageEditor.validate(collectWorkflow()).errors') == []
        page.set_viewport_size({'width': 390, 'height': 900})
        assert card.evaluate('el => el.scrollWidth <= el.clientWidth')
        page.set_viewport_size({'width': 1366, 'height': 960})
        card.screenshot(path='tmp/pdfs/composite-document-stage.png')
        assert not errors
        with page.expect_response(lambda r: r.request.method == 'POST' and '/edit' in r.url) as response:
            page.get_by_role('button', name='Zapisz zmiany', exact=True).click()
        assert response.value.status == 302, page.locator('body').inner_text()[:6000]
        page.wait_for_load_state('load')
        assert page.reload().status == 200
        expect(cards).to_have_count(4)
        expected_ids = ['submission', 'declaration', 'agreement', 'completed'] if split_draft else ['intake', 'paper', 'review', 'done']
        assert cards.locator('[data-step-id]').evaluate_all('(nodes) => nodes.map(n => n.value)') == expected_ids
        assert page.evaluate('collectWorkflow().steps.map(s => s.id)') == expected_ids
        assert page.locator('[data-stage-validity="invalid"]').count() == 0
        diagram_steps = page.locator('[data-diagram-node-id]:not([data-diagram-node-id^="decision:"])')
        expect(diagram_steps).to_have_count(4)
        assert set(diagram_steps.evaluate_all('(nodes) => nodes.map(n => n.dataset.diagramNodeId)')) == set(expected_ids)
        if split_draft:
            expect(page.locator('[data-diagram-node-id]')).to_have_count(4)
        assert not errors
        with env.repo.session_factory() as db:
            saved = db.query(FormVersion).filter_by(form_id=env.form_id, status='draft').one()
            assert saved.definition_json['workflow']['steps'][1]['document_lifecycle'] == 'composite'
            assert saved.definition_json['workflow']['steps'][1]['document_instructions']['ready']['description'] == 'Pobierz dokument i zachowaj jego kopię.'
            assert saved.definition_json['workflow']['steps'][1]['document_options']['download'] is False
            assert len(saved.definition_json['workflow']['steps']) == 4
            published = db.get(FormVersion, env.row()['form_version_id'])
            assert published.definition_json == env.definition
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_document_fields_and_real_signature(composite_env, participant_page, signed_pdf_factory, monkeypatch):
    from test_composite_document_workflow import configure_document_fields
    from playwright.sync_api import expect
    from signature_verifier import verify_signed_pdf
    env = composite_env()
    configure_document_fields(env)
    env.enter()
    env.app.config.update(WTF_CSRF_ENABLED=True, PUBLIC_CSRF_ENABLED=True)
    monkeypatch.setattr(env.services.document_signing_service, 'verifier', verify_signed_pdf)
    server = make_server('127.0.0.1', 0, env.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = participant_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/do-podpisania?submission_id={env.public_id}&token={env.row()['access_token']}")
        expect(page.get_by_role('heading', name='Deklaracja uczestnictwa i wybór szkoleń')).to_be_visible()
        assert page.locator('[data-document-upload]').count() == 0
        page.locator('[name="deklaracja_18_lat"][value="Tak"]').check()
        page.locator('[name="wybor_szkolen"][value="course-a"]').check()
        page.locator('[name="declaration_note"]').fill('Dane zapisane przed generowaniem')
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.split('?')[0].endswith('/fields')) as response:
            page.get_by_role('button', name='Zapisz dane', exact=True).click()
        assert response.value.status == 302
        page.wait_for_load_state('load')
        page.reload()
        expect(page.locator('[name="declaration_note"]')).to_have_value('Dane zapisane przed generowaniem')
        expect(page.locator('[name="deklaracja_18_lat"][value="Tak"]')).to_be_checked()
        expect(page.locator('[name="wybor_szkolen"][value="course-a"]')).to_be_checked()
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.split('?')[0].endswith('/fields')) as response:
            page.get_by_role('button', name='Wygeneruj deklarację PDF', exact=True).click()
        assert response.value.status == 302
        expect(page.locator('[data-document-upload]')).to_be_visible()
        assert len(env.generated) == 1
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as signer_pool:
            signed = signer_pool.submit(signed_pdf_factory, env.pdf, 'profil_zaufany').result()
        page.locator('[name="document_pdf"]').set_input_files({'name': 'declaration.pdf', 'mimeType': 'application/pdf', 'buffer': signed})
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/upload')) as response:
            page.locator('[data-document-upload] button[type="submit"]').click()
        assert response.value.status in {200, 302}
        page.wait_for_load_state('load')
        expect(page.locator('[data-document-upload]')).to_have_count(0)
        assert env.state()['completed'] and env.row()['workflow_stage'] == 'review'
        assert not errors
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_composite_participant_actions_and_instructions(composite_env, participant_page):
    from playwright.sync_api import expect
    env = composite_env()
    env.enter()
    env.app.config.update(WTF_CSRF_ENABLED=True, PUBLIC_CSRF_ENABLED=True)
    row = env.row()
    server = make_server('127.0.0.1', 0, env.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = participant_page
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        page.goto(f"{base}/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
        card = page.locator('[data-composite-document]')
        expect(card).to_be_visible()
        expect(page.locator('.sign-actions')).to_be_hidden()
        expect(page.locator('#current-stage-description')).to_contain_text('Pobierz swój plik.')
        with page.expect_download():
            card.get_by_role('link', name='Pobierz:').click()
        expect(card.locator('.status-tile__title')).to_contain_text('podpisanie dokumentu')
        env.verification['validation_status'] = 'INVALID'
        card.locator('input[type="file"]').set_input_files({'name': 'signed.pdf', 'mimeType': 'application/pdf', 'buffer': env.pdf + b'\n% signed\n'})
        with page.expect_navigation():
            card.locator('button[type="submit"]').click()
        expect(page.locator('#current-stage-description')).to_contain_text('Podpisz ponownie.')
        expect(page.get_by_role('button', name='Wgraj ponownie: Dokument')).to_be_visible()
        page.screenshot(path='tmp/pdfs/composite-document-public.png', full_page=True)
        assert env.row()['workflow_stage'] == 'paper'
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize('scope', ['submission', 'item'])
def test_workflow_builder_collapsed_cards_decision_scope_and_stable_graph(admin_app, participant_page, scope):
    from test_admin_panel import create_form
    from test_workflow_v2_validation import explicit_workflow
    create_user(admin_app)
    form_id = create_form(admin_app, slug='workflow-editor', name='Edytor workflow')
    with create_session_factory(admin_app.config['DATABASE_URL'])() as db:
        form = db.get(Form, form_id)
        definition = {**form.definition_json, 'workflow': explicit_workflow()}
        definition['fields'] = [
            {'type': 'repeatable_group', 'name': f'people_{i}', 'label': f'Osoby {i}',
             'fields': [{'type': 'text', 'name': 'name', 'label': 'Imię'}]}
            for i in range(3)
        ]
        definition['workflow']['steps'][-1].update(admin_label='Proces zakończony', user_label='Proces zakończony')
        definition['workflow']['steps'][1].update(decision_scope=scope)
        if scope == 'item':
            definition['workflow']['steps'][1].update(decision_group='people_1', completion_policy='ALL', completion_next='completed')
        for option in definition['workflow']['decision_types']:
            option['code'] = 'option_' + option['code']
            option.update(scope=scope, label='Bilet bezpłatny')
            catalog = admin_app.extensions['services'].decision_definition_service.save_definition(db, code=option['code'], label=option['label'], category='neutral')
            option['definition_id'] = catalog.id
        db.add(FormVersion(form_id=form_id, version_major=1, version_minor=1, version_label='1.1', status='draft', definition_json=definition))
        admin_app.extensions['services'].form_version_service.apply_to_legacy_editor(db, form, definition)
        db.commit()
    server = make_server('127.0.0.1', 0, admin_app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = participant_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        page.goto(f'{base}/admin/')
        page.locator('[name="email"]').fill('admin@example.com')
        page.locator('[name="password"]').fill('secret')
        page.locator('button[type="submit"]').click()
        response = page.goto(f'{base}/admin/forms/{form_id}/edit?tab=workflow')
        assert response.status == 200
        assert not errors
        page.set_viewport_size({'width': 1366, 'height': 960})
        cards = page.locator('[data-workflow-step-list] > [data-workflow-step]')
        assert cards.count() == 3
        assert cards.locator('[data-workflow-step-editor]:visible').count() == 0
        first = cards.nth(0)
        first.locator('[data-edit-workflow-step]').click()
        assert first.locator('[data-workflow-step-editor]').is_visible()
        assert first.locator('[data-stage-decision-editor]').is_hidden()
        assert not first.locator('.workflow-stage-advanced').evaluate('el => el.open')
        assert first.locator('[data-stage-field="transitions_json"]').is_hidden()
        first.locator('[data-step-admin-label]').fill('Złożenie wniosku')
        assert first.locator('[data-step-id]').input_value() == 'submission'
        first.locator('[data-edit-workflow-step]').click()
        review = cards.nth(1)
        review.locator('[data-edit-workflow-step]').click()
        assert review.locator('[data-stage-decision-editor]').is_visible()
        assert review.locator('[data-step-next]').is_hidden()
        assert review.locator('[data-decision-option] input:checked').count() == 2
        assert review.locator('[data-decision-option]').count() == 2
        assert review.locator('[data-decision-option] code').all_text_contents() == ['option_a', 'option_b']
        assert review.locator('[data-decision-count]').inner_text() == 'Wybrano: 2'
        assert review.locator('[data-option-details][open]').count() == 0
        group = review.locator('[data-stage-field="decision_group"]')
        completion = review.locator('[data-stage-field="completion_next"]')
        assert group.locator('option[value^="people_"]').count() == 3
        if scope == 'item':
            assert group.is_visible()
            assert completion.evaluate('el => el.tagName') == 'SELECT'
            assert completion.input_value() == 'completed'
            assert completion.locator('option:checked').inner_text() == 'Proces zakończony'
            group.select_option('')
            completion.select_option('')
            assert group.get_attribute('aria-invalid') == 'true'
            assert completion.get_attribute('aria-invalid') == 'true'
            assert 'Wybierz grupę powtarzalną.' in group.locator('..').inner_text()
            assert 'Wybierz etap po zakończeniu' in completion.locator('..').inner_text()
            assert 'has-error' not in review.get_attribute('class')
            assert not page.locator('[data-workflow-preview-errors] details').evaluate('el => el.open')
            group.select_option('people_1')
            completion.select_option(label='Proces zakończony')
            assert group.get_attribute('aria-invalid') is None
            assert review.locator('[data-step-summary] dd').nth(1).inner_text() == 'Po zakończeniu ALL → Proces zakończony'
            assert page.locator('[data-workflow-diagram] .workflow-diagram__edge-label').all_text_contents().count('ALL zakończone') == 1
            assert page.locator('[data-workflow-diagram] .workflow-diagram__decision').count() == 0
        else:
            assert group.is_hidden()
            assert completion.is_hidden()
            assert review.locator('[data-stage-field="completion_policy"]').is_hidden()
        review.locator('[data-option-details] summary').first.click()
        assert review.locator('[data-stage-field="require_reason"]').first.is_visible()
        assert review.locator('[data-stage-field="target_step"]').first.is_visible() == (scope == 'submission')
        assert 'completed' not in review.locator('[data-step-summary]').inner_text()
        before = page.evaluate('collectWorkflow()')
        review.locator('[data-workflow-step-up]').click()
        after = page.evaluate('collectWorkflow()')
        assert [s['id'] for s in after['steps']] == ['review', 'submission', 'completed']
        assert {s['id']: s['next'] for s in before['steps']} == {s['id']: s['next'] for s in after['steps']}
        assert before['decision_types'] == after['decision_types']
        assert cards.locator('.workflow-stage-number').all_text_contents() == ['Etap 1', 'Etap 2', 'Etap 3']
        if scope == 'submission':
            assert page.locator('[data-workflow-diagram] polygon').count() >= 1
            assert page.evaluate('buildWorkflowDiagramDecisions(collectWorkflow().steps, [])[0].outcomes.length') == 2
        assert page.locator('[data-workflow-diagram] .has-error').count() == 0
        required_note = page.locator('.admin-required-note')
        assert required_note.count() == 1
        assert "Pola oznaczone" in required_note.inner_text()
        assert required_note.locator('[aria-hidden="true"]').count() == 1
        assert page.evaluate('WorkflowStageEditor.validate(collectWorkflow()).errors') == []
        page.locator('[data-refresh-workflow-preview]').click()
        assert page.locator('[data-workflow-diagram-wrap]').evaluate('el => el.scrollHeight <= el.clientHeight + 2')
        page.locator('[data-workflow-diagram-zoom-in]').click()
        zoom = page.evaluate('workflowDiagramState.zoom')
        cards.first.locator('[data-step-admin-label]').fill('Decyzja Dyrektora DIT')
        assert page.evaluate('workflowDiagramState.zoom') == zoom
        page.screenshot(path=f'tmp/pdfs/workflow-editor-{scope}-desktop.png', full_page=True)
        cards.first.locator('[data-edit-workflow-step]').click()
        page.locator('[data-workflow-step-list]').screenshot(path='tmp/pdfs/workflow-editor-cards.png')
        page.set_viewport_size({'width': 390, 'height': 900})
        assert cards.first.evaluate('el => el.scrollWidth <= el.clientWidth')
        assert not errors
        page.set_viewport_size({'width': 1366, 'height': 960})
        with page.expect_response(lambda r: r.request.method == 'POST' and f'/forms/{form_id}/edit' in r.url) as saved:
            page.get_by_role('button', name='Zapisz zmiany', exact=True).click()
        assert saved.value.status == 302
        with create_session_factory(admin_app.config['DATABASE_URL'])() as db:
            draft = db.query(FormVersion).filter_by(form_id=form_id, status='draft').one()
            assert [s['id'] for s in draft.definition_json['workflow']['steps']] == ['review', 'submission', 'completed']
            assert draft.definition_json['workflow']['initial_step'] == 'submission'
            if scope == 'item':
                assert draft.definition_json['workflow']['steps'][0]['completion_next'] == 'completed'
                assert draft.definition_json['workflow']['steps'][0]['decision_group'] == 'people_1'
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
