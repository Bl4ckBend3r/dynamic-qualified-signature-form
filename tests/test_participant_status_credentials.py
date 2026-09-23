import logging
from uuid import uuid4

from test_admin_panel import admin_app, admin_client, create_form


def test_database_public_status_requires_matching_credential(admin_app, admin_client, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger='routes.participant_access')
    create_form(admin_app, slug='status-test')
    services = admin_app.extensions['services']
    ids = [str(uuid4()), str(uuid4())]
    tokens = [services.access_token_service.generate_token() for _ in ids]
    for public_id, token in zip(ids, tokens):
        services.submission_repository.create({
            'submission_id': public_id, 'form_slug': 'status-test', 'form_name': 'Status test',
            'access_token': token, 'process_status': 'WAITING_FOR_OFFICER_DECISION',
            'workflow_step': 'officer_review', 'workflow_stage': 'officer_review',
        })
    url = f'/api/submissions/{ids[0]}/acceptance-status'
    def unavailable_metadata(*args, **kwargs):
        raise ConnectionError('Optional form metadata is unavailable')
    monkeypatch.setattr(services.form_config_service, 'get_form_meta', unavailable_metadata)
    response = admin_client.get(url, headers={'Authorization': f'Bearer {tokens[0]}'})
    assert response.status_code == 200
    assert response.json['exists'] is True
    assert response.json['status_title'] == 'Wniosek oczekuje na decyzję'
    missing = admin_client.get(url)
    wrong = admin_client.get(url, headers={'Authorization': f'Bearer {tokens[1]}'})
    absent = admin_client.get(f'/api/submissions/{uuid4()}/acceptance-status')
    assert missing.status_code == wrong.status_code == absent.status_code == 404
    assert missing.json == wrong.json == absent.json
    for category in ('missing_credential', 'invalid_credential', 'submission_not_found'):
        assert f'category={category}' in caplog.text
    assert not any(token in caplog.text for token in tokens)
