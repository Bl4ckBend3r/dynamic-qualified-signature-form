from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
import json

import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from models import FormSubmission, FormVersion, SubmissionWorkflowEvent
from services.documents.document_workflow_service import document_step_state, validate_document_step
from services.process_instruction_service import build_process_instruction_view
from services.public_submission_status_service import build_readonly_submission_status
from services.workflow_service import WorkflowTransitionError
from test_admin_panel import admin_app, create_form, create_user, login
from test_signature_verifier import signed_pdf_factory


@pytest.fixture
def composite_env(admin_app, monkeypatch):
    services = admin_app.extensions['services']
    # This fixture exercises canonical readback, like Nextcloud. The shared legacy
    # fake otherwise saves PDFs only by filename and cannot read their metadata path.
    from services.file_metadata import resolve_pdf_storage_path
    storage = services.document_service.storage
    save_pdf = storage.save_pdf
    def save_with_canonical_path(slug, filename, pdf_bytes, **kwargs):
        save_pdf(slug, filename, pdf_bytes, **kwargs)
        storage.write_bytes(resolve_pdf_storage_path(storage, slug, filename, **kwargs), pdf_bytes)
    monkeypatch.setattr(storage, 'save_pdf', save_with_canonical_path)
    pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(pdf)
    pdf_bytes = pdf.getvalue()
    generated = []

    def render(**kwargs):
        generated.append(kwargs)
        return pdf_bytes

    monkeypatch.setattr(services.document_service.pdf_render_service, 'render_document_pdf_bytes', render)
    verification = {'is_signed': True, 'cryptographically_valid': True, 'integrity_ok': True,
                    'validation_status': 'VALID', 'signature_count': 1, 'is_allowed_signature': True, 'signature_type': 'mszafir'}
    monkeypatch.setattr(services.document_signing_service, 'verifier', lambda _: dict(verification))

    def create(document_id='declaration', **options):
        slug = f'composite-{uuid4().hex[:8]}'
        form_id = create_form(admin_app, slug=slug)
        definition = {
            'title': 'Composite', 'fields': [],
            'documents': [{'id': document_id, 'label': 'Dokument testowy', 'kind': 'generated_pdf',
                           'template_html': '<p>Dokument {{ submission_id }}</p>', 'filename_pattern': '{submission_id}-document.pdf'}],
            'workflow': {'flow_mode': 'explicit', 'schema_version': 2, 'initial_step': 'intake',
                'steps': [
                    {'id': 'intake', 'admin_label': 'Złożenie', 'user_label': 'Złożenie', 'status': 'FORM_SUBMITTED', 'stage_type': 'user_action', 'type': 'form_submit', 'next': 'paper'},
                    {'id': 'paper', 'admin_label': 'Dokument', 'user_label': 'Dokument', 'status': 'DECLARATION_WAITING_FOR_SIGNATURE',
                     'stage_type': 'document', 'type': 'document', 'document_lifecycle': 'composite', 'document_id': document_id,
                     'document_options': options, 'action': 'none', 'next': 'review',
                     'document_instructions': {'ready': {'description': '<p>Pobierz swój plik.</p>'}, 'verification_failed': {'description': '<p>Podpisz ponownie.</p>'}}},
                    {'id': 'review', 'admin_label': 'Decyzja', 'user_label': 'Decyzja', 'status': 'WAITING_FOR_OFFICER_DECISION', 'stage_type': 'decision', 'type': 'manual_decision', 'decision_scope': 'submission', 'decision_name': 'Decyzja'},
                    {'id': 'done', 'admin_label': 'Koniec', 'user_label': 'Koniec', 'status': 'COMPLETED', 'stage_type': 'final', 'final': True}],
                'decision_types': [{'code': code, 'label': code, 'scope': 'submission', 'step_id': 'review', 'target_step': 'done', 'semantic_category': 'neutral'} for code in ['yes', 'no']]}}
        public_id = str(uuid4())
        with services.submission_repository.session_factory() as db:
            version = FormVersion(form_id=form_id, version_major=1, version_minor=0, version_label='1.0', status='published', definition_json=definition)
            db.add(version)
            db.flush()
            model = FormSubmission(submission_id=public_id, form_slug=slug, form_version_id=version.id,
                access_token=uuid4().hex, process_status='FORM_SUBMITTED', workflow_stage='intake', workflow_step='intake')
            db.add(model)
            db.commit()
        repo = services.submission_repository
        env = SimpleNamespace(app=admin_app, services=services, repo=repo, definition=definition, public_id=public_id, form_id=form_id,
            document=services.document_service.document_workflow, generated=generated, pdf=pdf_bytes, verification=verification)
        env.row = lambda: repo.get_by_id(public_id)
        env.state = lambda: document_step_state(env.row(), 'paper')
        env.file = lambda suffix=b'\n% participant signature\n': FileStorage(stream=BytesIO(pdf_bytes + suffix), filename='signed.pdf', content_type='application/pdf')
        env.enter = lambda: services.workflow_service.advance_after_action(env.row(), definition)
        return env
    with admin_app.app_context():
        yield create


def configure_document_fields(env):
    document = env.definition['documents'][0]
    document.update(label='Deklaracja uczestnictwa i wybór szkoleń', form_title='Deklaracja uczestnictwa i wybór szkoleń',
        fields=[{'name': 'deklaracja_18_lat', 'label': 'Ukończyłem 18 lat', 'type': 'radio', 'options': ['Tak', 'Nie'], 'required': True},
            {'name': 'wybor_szkolen', 'label': 'Wybór szkoleń', 'type': 'training_selection', 'required': True,
             'max_total_amount': '2000', 'catalog': [{'id': 'course-a', 'name': 'Szkolenie A', 'price': '100', 'capacity': 10}]},
            {'name': 'declaration_note', 'label': 'Uwagi', 'type': 'text'}],
        template_source='builder', builder_document={'version': 1, 'blocks': [
            {'id': 'text', 'type': 'paragraph', 'content': 'Wiek: {{ deklaracja_18_lat }}. Uwagi: {{ declaration_note }}.'}]})
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()['form_version_id']).definition_json = deepcopy(env.definition)
        from models import Form
        db.get(Form, env.form_id).definition_json = deepcopy(env.definition)
        db.commit()


def test_document_fields_collect_save_generate_refresh(composite_env):
    env = composite_env()
    configure_document_fields(env)
    env.repo.update(env.public_id, {'data_json': {'initial_value': 'preserved'}})
    env.enter()
    env.app.config.update(PUBLIC_CSRF_ENABLED=True)
    assert env.state()['substate'] == 'collect_data'
    assert env.generated == []
    client = env.app.test_client()
    row = env.row()
    page_url = f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}"
    fields_url = f"/document-steps/{row['form_slug']}/{env.public_id}/paper/fields?token={row['access_token']}"
    page = client.get(page_url)
    assert page.status_code == 200
    assert 'name="deklaracja_18_lat"' in page.text
    assert 'name="wybor_szkolen"' in page.text and 'Szkolenie A' in page.text
    assert '/paper/download/' not in page.text and 'data-document-upload' not in page.text
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    assert client.post(fields_url, data={}).status_code == 400
    assert client.post(fields_url.split('?')[0], data={'csrf_token': csrf}).status_code in {403, 404}
    result = client.post(fields_url, data={'csrf_token': csrf})
    assert result.status_code == 400
    assert env.generated == []
    data = {'csrf_token': csrf, 'deklaracja_18_lat': 'Tak', 'wybor_szkolen': 'course-a', 'declaration_note': 'Zapisana wartość'}
    assert client.post(fields_url, data={**data, 'document_action': 'save'}).status_code == 302
    assert env.state()['substate'] == 'collect_data' and not env.generated
    assert env.row()['data_json']['initial_value'] == 'preserved'
    from models import SubmissionTraining
    with env.repo.session_factory() as db:
        training = db.execute(select(SubmissionTraining).where(SubmissionTraining.submission_id == env.row()['id'])).scalar_one()
        assert training.training_id == 'course-a' and not training.is_locked
    assert env.row()['workflow_step'] == env.row()['workflow_stage'] == 'paper'
    assert 'Zapisana wartość' in client.get(page_url).text
    assert client.post(fields_url, data=data).status_code == 302
    assert env.state()['substate'] == 'ready'
    assert len(env.generated) == 1
    assert env.generated[0]['context']['deklaracja_18_lat'] == 'Tak'
    assert env.generated[0]['context']['declaration_note'] == 'Zapisana wartość'
    assert env.generated[0]['context']['selected_trainings'][0]['id'] == 'course-a'
    assert '/paper/download/' in client.get(page_url).text
    snapshot = deepcopy(env.row()['data_json'])
    assert client.post(fields_url, data={**data, 'declaration_note': 'Zmieniona'}).status_code == 303
    assert env.row()['data_json'] == snapshot
    assert len(env.generated) == 1
    assert len(env.definition['workflow']['steps']) == 4
    assert env.definition['fields'] == []


def test_document_fields_replace_premature_pdf_and_fail_generation(composite_env, monkeypatch):
    from werkzeug.datastructures import MultiDict
    env = composite_env()
    env.enter()
    old_file = env.state()['files'][0]['filename']
    configure_document_fields(env)
    assert env.document.view(env.row(), env.definition)['substate'] == 'collect_data'
    with pytest.raises(ValueError):
        env.document.download(env.row(), env.definition, 'paper', old_file)
    data = MultiDict({'deklaracja_18_lat': 'Tak', 'wybor_szkolen': 'course-a', 'declaration_note': 'Nowa treść'})
    original = env.services.document_service.pdf_render_service.render_document_pdf_bytes
    def fail(**kwargs):
        raise RuntimeError('Test renderer failure')
    monkeypatch.setattr(env.services.document_service.pdf_render_service, 'render_document_pdf_bytes', fail)
    with pytest.raises(RuntimeError):
        env.document.save_fields(env.row(), env.definition, 'paper', data)
    assert env.state()['substate'] == 'failed'
    assert env.document.view(env.row(), env.definition)['form_definition']
    assert not env.document.view(env.row(), env.definition)['files']
    assert env.row()['data_json']['declaration_note'] == 'Nowa treść'
    monkeypatch.setattr(env.services.document_service.pdf_render_service, 'render_document_pdf_bytes', original)
    data['declaration_note'] = 'Poprawiona treść'
    assert env.document.save_fields(env.row(), env.definition, 'paper', data).success
    assert env.state()['substate'] == 'ready'
    assert env.state()['files'][0]['filename'] != old_file
    old = next(f for f in env.services.submission_document_service.list_documents(env.public_id) if f['filename'] == old_file)
    assert old['status'] == 'superseded'
    with pytest.raises(ValueError):
        env.document.download(env.row(), env.definition, 'paper', old_file)


@pytest.mark.parametrize('provider', ['mszafir', 'profil_zaufany'])
def test_real_document_signature_raw_bytes_idempotency_transition(composite_env, signed_pdf_factory, monkeypatch, caplog, provider):
    from hashlib import sha256
    from signature_verifier import verify_signed_pdf
    env = composite_env()
    env.enter()
    signed = signed_pdf_factory(env.pdf, provider)
    digests = []
    def verify(path):
        digests.append(sha256(path.read_bytes()).hexdigest())
        return verify_signed_pdf(path)
    monkeypatch.setattr(env.services.document_signing_service, 'verifier', verify)
    records = []
    original_record = env.services.submission_document_service.record_document_metadata
    def record(**kwargs):
        records.append(kwargs)
        return original_record(**kwargs)
    monkeypatch.setattr(env.services.submission_document_service, 'record_document_metadata', record)
    def upload():
        return env.document.upload(env.row(), env.definition, 'paper', FileStorage(stream=BytesIO(signed), filename='signed.pdf', content_type='application/pdf'))
    with caplog.at_level('INFO'):
        assert upload()['result'] == 'VALID_SIGNATURE'
    assert len(records) == 1
    assert digests == [sha256(signed).hexdigest()]
    assert records[0]['file_bytes'] == signed
    verification = records[0]['signature_validation_result']
    assert verification['upload_sha256'] == verification['storage_sha256'] == verification['verifier_sha256'] == digests[0]
    assert env.state()['completed'] and env.row()['workflow_stage'] == 'review'
    assert upload()['completed']
    assert len(records) == len(digests) == 1
    files = env.services.submission_document_service.list_documents(env.public_id)
    assert len(files) == 2
    signed_file = next(f for f in files if f['signed'])
    assert env.document.download(env.row(), env.definition, 'paper', signed_file['filename']) == signed
    assert 'result=VALID_SIGNATURE' in caplog.text
    assert 'TEST COPE SZAFIR' not in caplog.text
    assert 'TEST Profil Zaufany' not in caplog.text
    for expected in ['cryptographic_valid=True', 'document_integrity_valid=True', 'certificate_chain_valid=True',
                     'timestamp_valid=None', f'detected_signature_type={provider}', 'verifier_backend=pyhanko_pdf_cms', 'provider_allowed=True']:
        assert expected in caplog.text
    with env.repo.session_factory() as db:
        events = db.execute(select(SubmissionWorkflowEvent).where(SubmissionWorkflowEvent.public_submission_id == env.public_id)).scalars().all()
        assert sum(e.source == 'DOCUMENT_STEP_COMPLETED' for e in events) == 1
        assert sum(e.previous_step == 'paper' and e.new_step == 'review' for e in events) == 1


@pytest.mark.parametrize('failure', ['corruption', 'read_error'])
def test_signed_upload_storage_integrity_failure(composite_env, monkeypatch, failure):
    env = composite_env()
    env.enter()
    calls = []
    monkeypatch.setattr(env.services.document_signing_service, 'verifier', lambda path: calls.append(path))
    def read(*args, **kwargs):
        if failure == 'read_error':
            raise TimeoutError('Test storage unavailable')
        return b'%PDF-1.4\ncorrupted stored bytes'
    monkeypatch.setattr(env.services.document_signing_service.document_storage_service, 'read_document_bytes', read)
    result = env.document.upload(env.row(), env.definition, 'paper', env.file())
    assert result['result'] == 'VERIFICATION_ERROR'
    assert not calls
    assert env.row()['workflow_stage'] == 'paper'
    files = env.services.submission_document_service.list_documents(env.public_id)
    rejected = [file for file in files if file['status'] == 'rejected']
    assert len(rejected) == 1
    verification = rejected[0]['signature_validation_result']
    assert verification['reason_code'] == ('UPLOAD_STORAGE_HASH_MISMATCH' if failure == 'corruption' else 'STORAGE_READ_ERROR')
    assert verification['verifier_sha256'] is None


def test_document_fields_generation_contains_saved_data(composite_env, monkeypatch):
    from werkzeug.datastructures import MultiDict
    from pypdf import PdfReader
    from services.documents.pdf_render_service import PdfRenderService
    from pdf_generator import generate_pdf_from_html
    env = composite_env()
    configure_document_fields(env)
    env.enter()
    monkeypatch.setattr(env.services.document_service, 'pdf_render_service', PdfRenderService(html_renderer=generate_pdf_from_html))
    assert env.document.save_fields(env.row(), env.definition, 'paper', MultiDict({
        'deklaracja_18_lat': 'Tak', 'wybor_szkolen': 'course-a', 'declaration_note': 'Saved document values'})).success
    filename = env.state()['files'][0]['filename']
    pdf = env.document.download(env.row(), env.definition, 'paper', filename)
    rendered = ''.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    assert 'Wiek: Tak' in rendered and 'Saved document values' in rendered


@pytest.mark.parametrize('failure', ['unsigned', 'timeout', 'storage', 'storage_read', 'metadata', 'unsupported'])
def test_document_signature_failure_messages(composite_env, monkeypatch, failure):
    from signature_verifier import verify_signed_pdf
    env = composite_env()
    env.enter()
    if failure == 'unsigned':
        monkeypatch.setattr(env.services.document_signing_service, 'verifier', verify_signed_pdf)
    elif failure == 'unsupported':
        env.definition['documents'][0]['allowed_signatures'] = ['profil_zaufany']
    elif failure == 'metadata':
        monkeypatch.setattr(env.services.submission_document_service, 'record_document_metadata', lambda **kwargs: False)
    else:
        def fail(*args, **kwargs):
            raise TimeoutError('Unavailable')
        if failure == 'timeout':
            monkeypatch.setattr(env.services.document_signing_service, 'verifier', fail)
        elif failure == 'storage':
            monkeypatch.setattr(env.services.document_service.document_storage_service, 'save_pdf', fail)
        else:
            monkeypatch.setattr(env.services.document_service, 'read_document_bytes_for_download', fail)
    result = env.document.upload(env.row(), env.definition, 'paper', env.file())
    expected = {'unsigned': 'NO_SIGNATURE', 'unsupported': 'SIGNATURE_TYPE_NOT_ALLOWED'}.get(failure, 'VERIFICATION_ERROR')
    assert result['result'] == expected
    assert env.row()['workflow_stage'] == 'paper'
    assert env.state()['substate'] == ('verification_failed' if failure in {'unsigned', 'unsupported'} else 'verification_error')
    page = env.app.test_client().get(f"/do-podpisania?submission_id={env.public_id}&token={env.row()['access_token']}")
    assert result['message'] in page.text
    assert env.document.view(env.row(), env.definition)['files'][0]['can_upload']


def test_composite_state_machine(composite_env):
    env = composite_env()
    env.enter()
    assert len(env.definition['workflow']['steps']) == 4
    assert env.row()['workflow_stage'] == 'paper'
    assert env.state()['substate'] == 'ready'
    assert len(env.generated) == 1
    env.services.workflow_service.run_automatic_steps(env.row(), env.definition)
    assert len(env.generated) == 1
    files = env.services.document_service.submission_document_service.list_documents(env.public_id)
    assert len(files) == 1 and files[0]['workflow_step_at_upload'] == 'paper'
    assert env.repo.claim_document_operation(env.public_id, 'paper', 'first')
    assert not env.repo.claim_document_operation(env.public_id, 'paper', 'second')
    env.repo.release_document_operation(env.public_id, 'first')


@pytest.mark.parametrize('outcome', ['INVALID', 'INDETERMINATE', 'UNSIGNED'])
def test_composite_transition_requires_verified_signature(composite_env, outcome):
    env = composite_env()
    env.enter()
    with pytest.raises(WorkflowTransitionError):
        env.services.workflow_service.advance_after_action(env.row(), env.definition)
    with pytest.raises(WorkflowTransitionError):
        env.services.workflow_service.transition_to(env.public_id, 'review')
    env.verification['validation_status'] = outcome
    assert not env.document.upload(env.row(), env.definition, 'paper', env.file())['is_valid']
    assert env.row()['workflow_stage'] == 'paper'
    assert env.state()['substate'] == ('verification_error' if outcome == 'INDETERMINATE' else 'verification_failed')
    assert env.document.view(env.row(), env.definition)['files'][0]['can_upload']
    env.verification['validation_status'] = 'VALID'
    assert env.document.upload(env.row(), env.definition, 'paper', env.file())['completed']
    assert env.row()['workflow_stage'] == 'review'
    assert env.document.upload(env.row(), env.definition, 'paper', env.file())['completed']
    with env.repo.session_factory() as db:
        events = db.execute(select(SubmissionWorkflowEvent).where(SubmissionWorkflowEvent.public_submission_id == env.public_id)).scalars().all()
        assert sum(e.source == 'DOCUMENT_STEP_COMPLETED' for e in events) == 1
        assert sum(e.previous_step == 'paper' and e.new_step == 'review' for e in events) == 1
        assert {'DOCUMENT_GENERATION_STARTED', 'DOCUMENT_GENERATED', 'SIGNED_DOCUMENT_UPLOADED', 'SIGNATURE_VERIFICATION_STARTED', 'SIGNATURE_VERIFICATION_FAILED', 'SIGNATURE_VERIFIED'} <= {e.source for e in events}


def test_composite_instructions(composite_env):
    env = composite_env()
    env.enter()
    workflow = env.definition['workflow']
    workflow['steps'][1]['document_instructions']['upload'] = {'description': 'Wgraj podpisany plik.'}
    for substate, expected in [('ready', 'Pobierz swój plik.'), ('upload', 'Wgraj podpisany plik.'), ('verification_failed', 'Podpisz ponownie.')]:
        view = build_process_instruction_view('DECLARATION_WAITING_FOR_SIGNATURE', workflow=workflow, step_id='paper', document_substate=substate)
        assert expected in view['instruction']['current_stage_description']
        assert len(view['instruction']['stages']) == 4
    status = build_readonly_submission_status(env.row(), form_config=env.definition)
    assert list(status) == ['status_label']
    assert 'gotowy do pobrania' in status['status_label']
    assert validate_document_step(workflow['steps'][1], env.definition) == []
    bad = deepcopy(workflow['steps'][1])
    bad['document_instructions']['unknown'] = {}
    assert validate_document_step(bad, env.definition)


def test_composite_declaration_download_upload_access(composite_env):
    env = composite_env()
    env.enter()
    row = env.row()
    client = env.app.test_client()
    env.app.config['WTF_CSRF_ENABLED'] = True
    filename = env.state()['files'][0]['filename']
    base = f"/document-steps/{row['form_slug']}/{env.public_id}/paper"
    assert client.get(f'{base}/download/{filename}').status_code == 404
    assert client.get(f'{base}/download/{filename}?token=wrong').status_code == 404
    other = composite_env()
    assert client.get(f"{base}/download/{filename}?token={other.row()['access_token']}").status_code == 404
    download = client.get(f"{base}/download/{filename}?token={row['access_token']}")
    assert download.status_code == 200 and download.data == env.pdf
    assert env.state()['substate'] == 'awaiting_signature'
    page = client.get(f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
    assert page.status_code == 200 and b'data-composite-document' in page.data
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    env.app.config['PUBLIC_CSRF_ENABLED'] = True
    upload = {'access_token': row['access_token'], 'document_pdf': (BytesIO(env.pdf + b'\n% signed\n'), 'signed.pdf')}
    assert client.post(f'{base}/upload', data=upload).status_code == 400
    response = client.post(f'{base}/upload', data={'access_token': row['access_token'], 'csrf_token': csrf,
        'document_pdf': (BytesIO(env.pdf + b'\n% signed\n'), 'signed.pdf')})
    assert response.status_code == 302
    assert env.row()['workflow_stage'] == 'review'
    assert env.row()['data_json']['_signed_declaration_snapshot']['form_version_id'] == row['form_version_id']


@pytest.mark.parametrize('mode', ['participant', 'office', 'both', 'unsigned', 'upload_only'])
def test_composite_agreement_and_document_capabilities(composite_env, mode):
    env = composite_env('agreement', generate=mode != 'upload_only', participant_signature=mode in {'participant', 'both'},
        upload_required=mode in {'participant', 'both', 'upload_only'}, office_signature=mode in {'office', 'both'})
    env.enter()
    if mode == 'unsigned':
        assert env.row()['workflow_stage'] == 'review'
        return
    if mode != 'office':
        result = env.document.upload(env.row(), env.definition, 'paper', env.file())
        assert result['is_valid']
    if mode in {'office', 'both'}:
        assert env.row()['workflow_stage'] == 'paper'
        env.verification['signature_count'] = 2 if mode == 'both' else 1
        result = env.document.upload(env.row(), env.definition, 'paper', env.file(b'\n% participant signature\n\n% office signature\n'), signer='office')
        assert result['is_valid']
    assert env.row()['workflow_stage'] == 'review'


def test_composite_generic_builder_and_historical_workflow(composite_env):
    env = composite_env('statement')
    env.enter()
    assert env.document.upload(env.row(), env.definition, 'paper', env.file())['completed']
    assert any(f['document_type'] == 'signed_statement' for f in env.services.document_service.submission_document_service.list_documents(env.public_id))
    html = env.services.document_service.resolve_document_template({'id': 'statement', 'template_source': 'builder',
        'builder_document': {'version': 1, 'blocks': [{'id': 'text', 'type': 'paragraph', 'content': 'Oświadczenie'}]}})
    assert 'Oświadczenie' in html
    historical = deepcopy(env.definition)
    historical['workflow']['steps'][1].pop('document_lifecycle')
    historical['workflow']['steps'][1]['action'] = 'await_signature'
    assert env.services.document_service.composite_step({'workflow_stage': 'paper'}, historical) is None
    with env.repo.session_factory() as db:
        version = db.get(FormVersion, env.row()['form_version_id'])
        assert version.definition_json == env.definition


def test_composite_verifier_failure_and_source_binding(composite_env, monkeypatch):
    env = composite_env()
    env.enter()
    verifier = env.services.document_signing_service.verifier
    def unavailable(_):
        raise RuntimeError('Verifier unavailable')
    monkeypatch.setattr(env.services.document_signing_service, 'verifier', unavailable)
    assert not env.document.upload(env.row(), env.definition, 'paper', env.file())['is_valid']
    assert env.document.view(env.row(), env.definition)['files'][0]['can_upload']
    monkeypatch.setattr(env.services.document_signing_service, 'verifier', verifier)
    unrelated = FileStorage(stream=BytesIO(env.pdf.replace(b'200', b'201') + b'\n% signed'), filename='other.pdf', content_type='application/pdf')
    assert not env.document.upload(env.row(), env.definition, 'paper', unrelated)['is_valid']
    env.definition['documents'][0]['allowed_signatures'] = ['profil_zaufany']
    assert not env.document.upload(env.row(), env.definition, 'paper', env.file())['is_valid']
    env.definition['documents'][0]['allowed_signatures'] = ['mszafir', 'profil_zaufany']
    assert env.document.upload(env.row(), env.definition, 'paper', env.file())['completed']


def test_composite_training_agreements_keep_separate_locks(composite_env):
    from models import SubmissionTraining
    env = composite_env('agreement', office_signature=True)
    env.definition['documents'][0]['generation_mode'] = 'per_training'
    snapshots = [{'id': key, 'name': f'Szkolenie {key}', 'price': '25.00', 'capacity': 3} for key in ['a', 'b']]
    with env.repo.session_factory() as db:
        model = db.execute(select(FormSubmission).where(FormSubmission.submission_id == env.public_id)).scalar_one()
        model.selected_trainings = json.dumps(snapshots)
        for snapshot in snapshots:
            db.add(env.services.submission_training_service._new_row(model.id, snapshot))
        model.form_version.definition_json = deepcopy(env.definition)
        db.commit()
    env.enter()
    assert len(env.generated) == 2 and len(env.state()['files']) == 2
    assert [item['label'] for item in env.document.view(env.row(), env.definition)['files']] == [
        'Umowa - Szkolenie a', 'Umowa - Szkolenie b'
    ]
    env.services.workflow_service.run_automatic_steps(env.row(), env.definition)
    assert len(env.generated) == 2
    for key in ['a', 'b']:
        assert env.document.upload(env.row(), env.definition, 'paper', env.file(), instance=key)['is_valid']
        assert env.row()['workflow_stage'] == 'paper'
    with env.repo.session_factory() as db:
        trainings = db.execute(select(SubmissionTraining)).scalars().all()
        assert len(trainings) == 2 and all(t.is_locked for t in trainings)
        assert all(t.training_snapshot['capacity'] == 3 for t in trainings)
    env.verification['signature_count'] = 2
    for key in ['a', 'b']:
        result = env.document.upload(env.row(), env.definition, 'paper', env.file(b'\n% participant signature\n\n% office signature\n'), signer='office', instance=key)
        assert result['completed'] is (key == 'b')
    assert env.row()['workflow_stage'] == 'review'
    assert [item['label'] for item in env.document.completed_files(env.row(), env.definition)] == [
        'Umowa - Szkolenie a', 'Umowa - Szkolenie b'
    ]
    with env.repo.session_factory() as db:
        assert all(t.status == 'agreement_signed_by_office' for t in db.execute(select(SubmissionTraining)).scalars())


def test_composite_office_route_permissions_and_csrf(composite_env):
    env = composite_env('agreement', participant_signature=False, upload_required=False, office_signature=True)
    env.enter()
    row = env.row()
    base = f"/admin/forms/{env.form_id}/submissions/{row['id']}/document-step/paper"
    client = env.app.test_client()
    assert client.post(base).status_code in {302, 400, 403}
    create_user(env.app, email='viewer@example.com', role='viewer')
    login(client, email='viewer@example.com')
    assert client.get(base).status_code == 403
    client = env.app.test_client()
    create_user(env.app)
    env.app.config['WTF_CSRF_ENABLED'] = True
    login(client)
    assert client.post(base).status_code == 400
    page = client.get(f"/admin/forms/{env.form_id}/submissions/{row['id']}")
    assert page.status_code == 200 and 'data-office-document-step' in page.text
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    filename = env.state()['files'][0]['filename']
    assert client.get(f'{base}?filename={filename}').data == env.pdf
    response = client.post(base, data={'csrf_token': csrf, 'document_pdf': (BytesIO(env.pdf + b'\n% office signature\n'), 'office.pdf')})
    assert response.status_code == 302 and env.row()['workflow_stage'] == 'review'


def test_historical_document_nodes_are_not_converted(composite_env):
    env = composite_env()
    historical = deepcopy(env.definition)
    historical['workflow']['steps'][1:2] = [
        {'id': 'paper', 'stage_type': 'document', 'action': 'generate_document', 'document_id': 'declaration', 'next': 'generated'},
        {'id': 'generated', 'stage_type': 'system', 'action': 'none', 'next': 'signature'},
        {'id': 'signature', 'stage_type': 'document', 'action': 'await_signature', 'document_id': 'declaration', 'next': 'signed'},
        {'id': 'signed', 'stage_type': 'system', 'action': 'none', 'next': 'review'},
    ]
    with env.repo.session_factory() as db:
        version = db.get(FormVersion, env.row()['form_version_id'])
        version.definition_json = historical
        db.commit()
    env.services.workflow_service.advance_after_action(env.row(), historical)
    assert env.row()['workflow_stage'] == 'signature'
    assert env.document.view(env.row(), historical) is None
    assert env.row()['declaration_filename']
    assert env.services.workflow_service.definition_for(env.row()) == historical


def test_composite_recovery_reuses_generated_file_and_verified_signature(composite_env, monkeypatch):
    env = composite_env()
    save = env.repo.save_document_step
    def interrupted_save(submission_id, step_id, state, token, event, actor='system'):
        if event == 'DOCUMENT_GENERATED':
            raise RuntimeError('Interrupted after file persistence')
        return save(submission_id, step_id, state, token, event, actor)
    monkeypatch.setattr(env.repo, 'save_document_step', interrupted_save)
    with pytest.raises(RuntimeError):
        env.enter()
    monkeypatch.setattr(env.repo, 'save_document_step', save)
    env.services.workflow_service.run_automatic_steps(env.row(), env.definition)
    assert len(env.generated) == 1 and env.state()['files'][0]['training_key'] == ''
    complete = env.document._complete
    def interrupted_completion(*args):
        raise RuntimeError('Interrupted after verification')
    monkeypatch.setattr(env.document, '_complete', interrupted_completion)
    with pytest.raises(RuntimeError):
        env.document.upload(env.row(), env.definition, 'paper', env.file())
    monkeypatch.setattr(env.document, '_complete', complete)
    assert env.document.upload(env.row(), env.definition, 'paper', env.file())['completed']
    assert env.row()['workflow_stage'] == 'review'
    with env.repo.session_factory() as db:
        events = db.execute(select(SubmissionWorkflowEvent).where(SubmissionWorkflowEvent.public_submission_id == env.public_id)).scalars().all()
        assert sum(e.source == 'SIGNATURE_VERIFIED' for e in events) == 1
        assert sum(e.source == 'DOCUMENT_STEP_COMPLETED' for e in events) == 1


def test_composite_generation_isolated_between_submissions(composite_env):
    env = composite_env()
    env.definition['documents'][0]['filename_pattern'] = 'document.pdf'
    with env.repo.session_factory() as db:
        original = db.execute(select(FormSubmission).where(FormSubmission.submission_id == env.public_id)).scalar_one()
        original.form_version.definition_json = deepcopy(env.definition)
        second_id = str(uuid4())
        db.add(FormSubmission(submission_id=second_id, form_slug=original.form_slug, form_version_id=original.form_version_id,
            process_status='FORM_SUBMITTED', workflow_stage='intake', workflow_step='intake'))
        db.commit()
    env.enter()
    env.services.workflow_service.advance_after_action(env.repo.get_by_id(second_id), env.definition)
    first = env.state()['files'][0]['filename']
    second = document_step_state(env.repo.get_by_id(second_id), 'paper')['files'][0]['filename']
    assert first != second
    assert env.document.download(env.row(), env.definition, 'paper', first) == env.pdf
    assert env.document.download(env.repo.get_by_id(second_id), env.definition, 'paper', second) == env.pdf


def test_new_draft_has_one_stage_per_document_without_implicit_decision():
    from services.form_config_service import FormConfigService
    from services.workflow_config_service import WorkflowConfigValidator
    service = FormConfigService()
    definition = service.normalize_form_config(service.build_new_draft_workflow({
        'title': 'Nowy formularz', 'fields': [], 'documents': [
            {'id': key, 'label': label, 'template_html': '<p>Dokument</p>'}
            for key, label in [('declaration', 'Deklaracja'), ('agreement', 'Umowa'), ('statement', 'Oświadczenie')]]
    }))
    steps = definition['workflow']['steps']
    assert [s['id'] for s in steps] == ['submission', 'declaration', 'agreement', 'statement', 'completed']
    assert all(s['document_lifecycle'] == 'composite' for s in steps[1:-1])
    assert not any(s['stage_type'] == 'decision' for s in steps)
    assert WorkflowConfigValidator().validate(definition['workflow'], definition) == []


@pytest.mark.parametrize('stale_mirror', [False, True])
def test_composite_draft_save_reload_uses_version_documents(admin_app, stale_mirror):
    from models import Form
    from services.form_config_service import FormConfigService
    service = FormConfigService()
    definition = service.normalize_form_config(service.build_new_draft_workflow({
        'title': 'Draft', 'fields': [], 'documents': [
            {'id': key, 'label': key, 'template_html': '<p>Dokument</p>'}
            for key in ('declaration', 'agreement')]}))
    for step in definition['workflow']['steps'][1:-1]:
        step['document_options'] = {'generate': True, 'download': False, 'participant_signature': True,
                                    'upload_required': True, 'office_signature': False, 'signature_verification': True}
        step['document_instructions'] = {key: {'description': f'<p>{key}</p>', 'next_action': 'Dalej'}
                                         for key in ('ready', 'upload', 'verification_failed')}
    definition['fields'] = [{'name': 'draft_contact', 'label': 'Kontakt', 'type': 'email',
        'availability': [{'step': 'submission', 'visible': True, 'editable': True, 'required': True}]}]
    form_id = create_form(admin_app, slug='draft-roundtrip')
    create_user(admin_app)
    repo = admin_app.extensions['services'].submission_repository
    with repo.session_factory() as db:
        form = db.get(Form, form_id)
        published = FormVersion(form_id=form_id, version_major=1, version_minor=0,
            version_label='1.0', status='published', definition_json=deepcopy(form.definition_json))
        db.add(published)
        old_published = deepcopy(published.definition_json)
        draft = FormVersion(form_id=form_id, version_major=1, version_minor=1,
            version_label='1.1', status='draft', definition_json=deepcopy(definition))
        db.add(draft)
        if not stale_mirror:
            form.definition_json = deepcopy(definition)
        db.commit()
        draft_id, published_id = draft.id, published.id
    client = admin_app.test_client()
    login(client)
    url = f'/admin/forms/{form_id}/edit?tab=workflow'
    csrf = client.get(url).text.split('name="csrf_token" value="')[1].split('"')[0]
    payload = {'csrf_token': csrf, 'active_tab': 'workflow', 'workflow_controls_present': '1',
               'workflow_builder_json': json.dumps(definition['workflow'])}
    response = client.post(url, data=payload)
    assert response.status_code == 302, __import__('re').findall(r'<li>(.*?)</li>', response.text)
    with repo.session_factory() as db:
        saved = db.get(FormVersion, draft_id).definition_json
        expected = definition['workflow']['steps']
        assert [(s['id'], s['type'], s.get('next') or '') for s in saved['workflow']['steps']] == [
            (s['id'], s['type'], s.get('next') or '') for s in expected]
        for before, after in zip(expected[1:-1], saved['workflow']['steps'][1:-1]):
            for key in ('document_id', 'document_lifecycle', 'document_options', 'document_instructions'):
                assert after[key] == before[key]
        assert [f['name'] for f in saved['fields']] == ['draft_contact']
        assert saved['fields'][0]['availability'] == definition['fields'][0]['availability']
        assert db.get(FormVersion, published_id).definition_json == old_published
        saved_snapshot = deepcopy(saved)
    assert client.get(url).status_code == 200
    bad = deepcopy(definition['workflow'])
    bad['steps'][1]['document_id'] = ''
    response = client.post(url, data={**payload, 'name': 'Must not persist', 'workflow_builder_json': json.dumps(bad)})
    assert response.status_code == 400
    assert 'declaration' in response.text
    with repo.session_factory() as db:
        assert db.get(FormVersion, draft_id).definition_json == saved_snapshot
        assert db.get(Form, form_id).name != 'Must not persist'


def test_admin_import_without_workflow_does_not_add_a_decision(admin_app):
    from models import Form
    create_user(admin_app)
    client = admin_app.test_client()
    login(client)
    csrf = client.get('/admin/forms/upload').text.split('name="csrf_token" value="')[1].split('"')[0]
    response = client.post('/admin/forms/upload', data={
        'csrf_token': csrf, 'slug': 'new-business-flow', 'form_file': (BytesIO(json.dumps({'title': 'Nowy formularz', 'fields': []}).encode()), 'form.json')})
    assert response.status_code == 302
    with admin_app.extensions['services'].submission_repository.session_factory() as db:
        form = db.execute(select(Form).where(Form.slug == 'new-business-flow')).scalar_one()
        workflow = form.definition_json['workflow']
        assert workflow['flow_mode'] == 'explicit'
        assert [s['id'] for s in workflow['steps']] == ['submission', 'completed']


def test_draft_conversion_collapses_phases_and_preserves_source_and_instructions():
    from services.workflow_config_service import collapse_draft_document_stages
    from services.form_config_service import FormConfigService
    raw = {'title': 'Draft', 'fields': [], 'documents': [
        {'id': key, 'label': label, 'template_html': '<p>Dokument</p>'} for key, label in [('declaration', 'Deklaracja'), ('agreement', 'Umowa')]],
        'workflow': {'flow_mode': 'explicit', 'initial_step': 'submission', 'steps': [
            {'id': 'submission', 'type': 'form_submit', 'next': 'declaration_ready'},
            {'id': 'declaration_ready', 'status': 'DECLARATION_READY', 'description': 'Pobierz deklarację.', 'next': 'declaration_signature'},
            {'id': 'declaration_signature', 'status': 'DECLARATION_WAITING_FOR_SIGNATURE', 'description': 'Podpisz deklarację.', 'next': 'agreement_ready'},
            {'id': 'agreement_ready', 'status': 'AGREEMENT_READY', 'next': 'agreement_signature'},
            {'id': 'agreement_signature', 'status': 'AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE', 'next': 'office_signature'},
            {'id': 'office_signature', 'status': 'AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE', 'next': 'completed'},
            {'id': 'completed', 'final': True} ]}}
    source = FormConfigService().normalize_form_config(raw)
    original = deepcopy(source)
    prepared = collapse_draft_document_stages(source)
    steps = prepared['workflow']['steps']
    assert [s['id'] for s in steps] == ['submission', 'declaration', 'agreement', 'completed']
    assert [s['admin_label'] for s in steps[1:3]] == ['Deklaracja', 'Umowa']
    assert steps[1]['document_instructions']['ready']['description'] == 'Pobierz deklarację.'
    assert steps[1]['document_instructions']['awaiting_signature']['description'] == 'Podpisz deklarację.'
    assert steps[2]['document_options']['office_signature']
    assert source == original
    assert collapse_draft_document_stages(prepared) == prepared


def test_draft_conversion_keeps_business_decisions_and_field_permissions():
    from services.workflow_config_service import collapse_draft_document_stages
    definition = {'documents': [{'id': 'statement', 'label': 'Oświadczenie'}], 'fields': [], 'workflow': {
        'flow_mode': 'explicit', 'initial_step': 'prepare', 'steps': [
            {'id': 'prepare', 'document_id': 'statement', 'action': 'generate_document', 'next': 'sign'},
            {'id': 'sign', 'document_id': 'statement', 'action': 'await_signature', 'next': 'review'},
            {'id': 'review', 'stage_type': 'decision', 'next': 'completed'}, {'id': 'completed', 'final': True}]}}
    result = collapse_draft_document_stages(definition)
    assert [s['id'] for s in result['workflow']['steps']] == ['statement', 'review', 'completed']
    definition['fields'] = [{'name': 'secret', 'availability': [{'step': 'sign', 'visible': True, 'editable': True}]}]
    result = collapse_draft_document_stages(definition)
    assert [s['id'] for s in result['workflow']['steps']] == ['prepare', 'sign', 'review', 'completed']


def split_document_draft(*, already_composite=False):
    from services.form_config_service import FormConfigService
    phases = [
        ('submission', 'FORM_SUBMITTED', ''),
        ('declaration_ready', 'DECLARATION_READY', 'declaration'),
        ('declaration_signature', 'DECLARATION_WAITING_FOR_SIGNATURE', 'declaration'),
        ('agreement_ready', 'AGREEMENT_READY', 'agreement'),
        ('agreement_participant_signature', 'AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE', 'agreement'),
        ('agreement_office_signature', 'AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE', 'agreement'),
        ('completed', 'COMPLETED', ''),
    ]
    steps = []
    for index, (key, status, document_id) in enumerate(phases):
        step = {'id': key, 'status': status, 'admin_label': key, 'user_label': status,
                'next': phases[index + 1][0] if index + 1 < len(phases) else '',
                'type': 'document' if document_id else 'form_submit' if index == 0 else 'end',
                'stage_type': 'document' if document_id else 'user_action' if index == 0 else 'final'}
        if document_id:
            step.update(document_id=document_id, action='await_signature' if 'signature' in key else 'generate_document')
            if already_composite:
                step.update(document_lifecycle='composite', action='none')
        steps.append(step)
    steps[1]['description'] = 'Pobierz deklarację'
    steps[1]['triggers'] = ['document_generated']
    definition = {'title': 'Scalany draft', 'fields': [], 'documents': [
        {'id': key, 'label': label, 'template_html': '<p>Dokument</p>'}
        for key, label in [('declaration', 'Deklaracja'), ('agreement', 'Umowa')]],
        'workflow': {'schema_version': 2, 'flow_mode': 'explicit', 'initial_step': 'submission', 'steps': steps}}
    return FormConfigService().normalize_form_config(definition)


@pytest.mark.parametrize('already_composite', [False, True])
def test_draft_conversion_really_merges_seven_nodes_and_references(already_composite):
    from services.workflow_config_service import collapse_draft_document_stages, WorkflowConfigValidator
    definition = split_document_draft(already_composite=already_composite)
    definition['workflow']['steps'][0]['transitions'] = [{'when': {'field': 'choice', 'equals': 'yes'}, 'next': 'declaration_signature'}]
    definition['workflow']['steps'][0]['next'] = ''
    definition['workflow']['decision_settings'] = [{'id': 'custom', 'stage_id': 'declaration_signature',
        'workflow_stage_id': 'declaration_signature', 'decision_stage': 'declaration_signature', 'yes_status': 'agreement_ready'}]
    definition['documents'][0]['available_at_step'] = 'declaration_signature'
    definition['correction'] = {'target_step': 'declaration_ready', 'return_step': 'agreement_participant_signature'}
    original = deepcopy(definition)
    converted = collapse_draft_document_stages(definition)
    steps = converted['workflow']['steps']
    assert [s['id'] for s in steps] == ['submission', 'declaration', 'agreement', 'completed']
    assert steps[0]['transitions'][0]['next'] == 'declaration'
    assert steps[1]['next'] == 'agreement' and steps[2]['next'] == 'completed'
    assert steps[1]['document_instructions']['ready']['description'] == 'Pobierz deklarację'
    assert steps[2]['document_options']['office_signature'] is True
    assert converted['correction'] == {'target_step': 'declaration', 'return_step': 'agreement'}
    assert converted['documents'][0]['available_at_step'] == 'declaration'
    assert converted['workflow']['decision_settings'][0]['yes_status'] == 'agreement'
    assert all(converted['workflow']['decision_settings'][0][key] == 'declaration'
        for key in ('stage_id', 'workflow_stage_id', 'decision_stage', 'step_id'))
    assert WorkflowConfigValidator().validate(converted['workflow'], converted) == []
    assert definition == original
    assert collapse_draft_document_stages(converted) == converted


@pytest.mark.parametrize('already_composite', [False, True])
@pytest.mark.parametrize('builder_converted', [False, True])
def test_split_draft_save_reload_persists_four_nodes(admin_app, already_composite, builder_converted):
    import re
    from models import Form
    from services.workflow_config_service import collapse_draft_document_stages, WorkflowConfigValidator
    definition = split_document_draft(already_composite=already_composite)
    definition['fields'] = [{'name': 'contact', 'label': 'Kontakt', 'type': 'text', 'availability': [
        {'step': step, 'visible': True, 'editable': False, 'required': False}
        for step in ('declaration_ready', 'declaration_signature')]}]
    definition['workflow']['diagram_layout'] = {'nodes': {
        'declaration_ready': {'x': 100, 'y': 200}, 'agreement_office_signature': {'x': 300, 'y': 400}}}
    form_id = create_form(admin_app, slug='split-roundtrip')
    create_user(admin_app)
    repo = admin_app.extensions['services'].submission_repository
    with repo.session_factory() as db:
        published = FormVersion(form_id=form_id, version_major=1, version_minor=0,
            version_label='1.0', status='published', definition_json=deepcopy(definition))
        draft = FormVersion(form_id=form_id, version_major=1, version_minor=1,
            version_label='1.1', status='draft', definition_json=deepcopy(definition))
        db.add_all([published, draft])
        db.flush()
        historical = FormSubmission(submission_id=str(uuid4()), form_slug='split-roundtrip', form_version_id=published.id,
            workflow_step='agreement_office_signature', workflow_stage='agreement_office_signature',
            process_status='AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE', document_states={'legacy': {'signed': True}})
        db.add(historical)
        db.commit()
        draft_id, published_id, historical_id = draft.id, published.id, historical.id
    client = admin_app.test_client()
    login(client)
    url = f'/admin/forms/{form_id}/edit?tab=workflow'
    page = client.get(url)
    assert page.status_code == 200
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    # Support both the current converted browser payload and a stale seven-node payload.
    workflow = deepcopy(definition['workflow'])
    if builder_converted:
        workflow = collapse_draft_document_stages(definition)['workflow']
        workflow['document_stage_conversion'] = True
    response = client.post(url, data={'csrf_token': csrf, 'active_tab': 'workflow',
        'workflow_controls_present': '1', 'workflow_builder_json': json.dumps(workflow)})
    assert response.status_code == 302, re.findall(r'<li>(.*?)</li>', response.text)
    expected_ids = ['submission', 'declaration', 'agreement', 'completed']
    with repo.session_factory() as db:
        saved = db.get(FormVersion, draft_id).definition_json
        steps = saved['workflow']['steps']
        assert [s['id'] for s in steps] == expected_ids
        assert [s['next'] for s in steps] == ['declaration', 'agreement', 'completed', '']
        assert steps[1]['document_instructions']['ready']['description'] == 'Pobierz deklarację'
        assert steps[2]['document_options']['office_signature'] is True
        assert [a['step'] for a in saved['fields'][0]['availability']] == ['declaration']
        assert set(saved['workflow']['diagram_layout']['nodes']) == {'declaration', 'agreement'}
        assert WorkflowConfigValidator().validate(saved['workflow'], saved) == []
        assert db.get(Form, form_id).definition_json['workflow']['steps'] == steps
        assert db.get(FormVersion, published_id).definition_json == definition
        historical = db.get(FormSubmission, historical_id)
        assert historical.form_version_id == published_id
        assert historical.workflow_step == historical.workflow_stage == 'agreement_office_signature'
        assert historical.process_status == 'AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE'
        assert historical.document_states == {'legacy': {'signed': True}}
    page = client.get(url)
    assert page.status_code == 200
    # The template includes a prototype; count actual serialized stage cards only.
    cards = [json.loads(s) for s in re.findall(r'data-workflow-step-source>(.*?)</script>', page.text) if json.loads(s)]
    assert [s['id'] for s in cards] == expected_ids
    assert 'data-document-conversion-notice' not in page.text


def test_draft_conversion_keeps_decision_and_remaps_legacy_next_step():
    from services.workflow_config_service import collapse_draft_document_stages, WorkflowConfigValidator
    definition = split_document_draft()
    steps = definition['workflow']['steps']
    steps[0]['next'] = 'review'
    steps.insert(1, {'id': 'review', 'admin_label': 'Decyzja', 'user_label': 'Decyzja',
        'stage_type': 'decision', 'type': 'manual_decision', 'status': 'WAITING_FOR_OFFICER_DECISION',
        'decision_scope': 'submission', 'decision_name': 'Decyzja'})
    definition['workflow']['decision_types'] = [
        {'code': 'yes', 'label': 'Tak', 'scope': 'submission', 'step_id': 'review', 'target_step': 'declaration_ready'},
        {'code': 'no', 'label': 'Nie', 'scope': 'submission', 'step_id': 'review', 'target_step': 'completed'}]
    for step in steps[2:-1]:
        step['next_step'] = step.pop('next')
    converted = collapse_draft_document_stages(definition)
    assert [s['id'] for s in converted['workflow']['steps']] == ['submission', 'review', 'declaration', 'agreement', 'completed']
    assert converted['workflow']['decision_types'][0]['target_step'] == 'declaration'
    assert converted['workflow']['steps'][2]['next_step'] == 'agreement'
    assert converted['workflow']['steps'][3]['next_step'] == 'completed'
    assert WorkflowConfigValidator().validate(converted['workflow'], converted) == []
    definition['workflow']['initial_step'] = 'declaration_signature'
    assert collapse_draft_document_stages(definition)['workflow']['initial_step'] == 'declaration'


@pytest.mark.parametrize('change', [
    {'document_id': 'missing'}, {'next': ''},
    {'document_options': {'participant_signature': True, 'upload_required': False}},
])
def test_composite_validator_reports_real_configuration_errors(change):
    from services.workflow_config_service import collapse_draft_document_stages, WorkflowConfigValidator
    definition = collapse_draft_document_stages(split_document_draft())
    validator = WorkflowConfigValidator()
    assert validator.validate(definition['workflow'], definition) == []
    definition['workflow']['steps'][1].update(change)
    errors = validator.validate(definition['workflow'], definition)
    assert errors
    assert any('declaration' in error for error in errors)


@pytest.mark.parametrize('failure', ['renderer', 'metadata'])
def test_generation_failure_never_exposes_ready_or_download(composite_env, monkeypatch, failure):
    env = composite_env()
    if failure == 'renderer':
        def fail(**kwargs):
            raise RuntimeError('Renderer unavailable')
        monkeypatch.setattr(env.services.document_service.pdf_render_service, 'render_document_pdf_bytes', fail)
    else:
        monkeypatch.setattr(env.services.document_service.submission_document_service, 'record_generated_document', lambda **kwargs: False)
    with pytest.raises(RuntimeError):
        env.enter()
    assert env.row()['workflow_stage'] == 'paper'
    assert env.state()['substate'] == 'failed'
    view = env.document.view(env.row(), env.definition)
    assert view['files'] == [] and view['can_retry']
    page = env.app.test_client().get(f"/do-podpisania?submission_id={env.public_id}&token={env.row()['access_token']}")
    assert page.status_code == 200 and '/paper/download/' not in page.text


def test_composite_uses_real_document_builder_pdf_and_metadata(composite_env, monkeypatch):
    from pypdf import PdfReader
    from services.documents.pdf_render_service import PdfRenderService
    from pdf_generator import generate_pdf_from_html
    env = composite_env()
    env.definition['documents'][0].update(template_source='builder', builder_document={
        'version': 1, 'blocks': [{'id': 'text', 'type': 'paragraph', 'content': 'Deklaracja uczestnika testowego'}]})
    env.definition['documents'][0].pop('template_html')
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()['form_version_id']).definition_json = deepcopy(env.definition)
        db.commit()
    monkeypatch.setattr(env.services.document_service, 'pdf_render_service', PdfRenderService(html_renderer=generate_pdf_from_html))
    env.enter()
    assert env.state()['substate'] == 'ready'
    files = env.services.document_service.submission_document_service.list_documents(env.public_id)
    assert len(files) == 1 and files[0]['document_id'] == 'declaration'
    data = env.document.download(env.row(), env.definition, 'paper', env.state()['files'][0]['filename'])
    assert data.startswith(b'%PDF-')
    assert 'Deklaracja uczestnika testowego' in ''.join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)
