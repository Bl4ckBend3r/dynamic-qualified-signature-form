from uuid import uuid4

from test_admin_panel import admin_app, admin_client, create_form


def test_public_status_returns_only_label_and_actions_stay_protected(admin_app, admin_client):
    create_form(admin_app, slug='public-status')
    public_id = str(uuid4())
    repository = admin_app.extensions['services'].submission_repository
    repository.create({'submission_id': public_id, 'form_slug': 'public-status',
        'access_token': 'private-status-credential', 'email': 'private@example.org',
        'process_status': 'WAITING_FOR_OFFICER_DECISION', 'workflow_step': 'officer_review',
        'data_json': {'private': 'sensitive-value'}})
    response = admin_client.get(f'/api/public/submissions/{public_id}/status')
    assert response.status_code == 200
    assert set(response.json) == {'status_label'}
    assert response.json['status_label'] == 'Wniosek oczekuje na decyzję'
    assert response.headers['Cache-Control'] == 'no-store'
    assert admin_client.get(f'/api/submissions/{public_id}/acceptance-status').status_code == 404
    html = admin_client.get('/sprawdz-status').get_data(as_text=True)
    assert 'Sprawdź status' in html
    for forbidden in ('private-status-credential', 'private@example.org', 'access_token', 'type="file"', 'sign-documents-form'):
        assert forbidden not in html


def test_public_status_has_neutral_errors_and_shared_rate_limit(admin_app, admin_client):
    missing = admin_client.get(f'/api/public/submissions/{uuid4()}/status')
    malformed = admin_client.get('/api/public/submissions/123/status')
    assert missing.status_code == malformed.status_code == 404
    assert missing.json == malformed.json
    for _ in range(8):
        assert admin_client.get(f'/api/public/submissions/{uuid4()}/status').status_code == 404
    limited = admin_client.get(f'/api/public/submissions/{uuid4()}/status')
    assert limited.status_code == 429
    assert limited.headers['Retry-After'] == '60'
