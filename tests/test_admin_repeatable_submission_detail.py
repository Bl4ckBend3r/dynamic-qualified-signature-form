from uuid import uuid4

import pytest
from sqlalchemy import select

from database import create_session_factory
from form_loader import load_form_definition
from models import FormSubmission, FormVersion
from test_admin_panel import admin_app, admin_client, create_form, create_user, login


@pytest.mark.parametrize(
    ('count', 'legacy_id', 'missing_group'),
    [(1, False, False), (2, False, False), (0, False, False),
     (0, False, True), (1, True, False)],
    ids=['one-person', 'two-people', 'empty-group', 'missing-group', 'historical-id'],
)
def test_repeatable_submission_detail_renders_saved_people(
    admin_app, admin_client, count, legacy_id, missing_group,
):
    create_user(admin_app)
    form_id = create_form(admin_app, slug='loty-regression', name='Loty')
    factory = create_session_factory(admin_app.config['DATABASE_URL'])
    with factory() as db:
        version = FormVersion(
            form_id=form_id, version_major=1, version_minor=0, version_label='1.0',
            status='archived' if legacy_id else 'published',
            definition_json=load_form_definition('examples/forms/loty.json'),
        )
        db.add(version)
        db.commit()
        version_id = version.id

    people = [
        {('id' if legacy_id else 'record_uuid'): str(uuid4()), 'imie': first,
         'nazwisko': last, 'email': email}
        for first, last, email in [('Jan', 'Kowalski', 'jan@example.org'),
                                   ('Anna', 'Nowak', 'anna@example.org')][:count]
    ]
    dynamic_data = {
        'typ_wnioskodawcy': 'urzednik', 'data_wniosku': '2026-09-02',
        'rodzaj_biletu': 'nieodpłatnych', 'cel_podrozy_uzasadnienie': 'Spotkanie',
        'dane_do_faktury': 'Dane testowe',
    }
    if not missing_group:
        dynamic_data['podrozni'] = people
    repository = admin_app.extensions['services'].submission_repository
    public_id = str(uuid4())
    repository.create({
        'submission_id': public_id, 'form_slug': 'loty-regression',
        'form_version_id': version_id, 'process_status': 'FORM_SUBMITTED',
        **dynamic_data, 'data_json': dynamic_data,
    })
    # The dynamic fields are stored as JSON, even when no dedicated column exists.
    updated_data = {**dynamic_data, 'cel_podrozy_uzasadnienie': 'Spotkanie robocze'}
    assert repository.update(public_id, {**updated_data, 'data_json': updated_data})
    with factory() as db:
        submission = db.execute(select(FormSubmission).where(FormSubmission.submission_id == public_id)).scalar_one()
        assert submission.data_json == updated_data
        submission_pk = submission.id
        groups = admin_app.extensions['services'].decision_definition_service.repeatable_groups(submission)
        assert len(groups) == 1
        assert isinstance(groups[0], dict)
        assert isinstance(groups[0]['items'], list)
        assert len(groups[0]['items']) == count

    login(admin_client)
    response = admin_client.get(f'/admin/forms/{form_id}/submissions/{submission_pk}')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Assert the actual group cards, not a separate generic JSON summary.
    cards = html.split('<section id="repeatable-decisions">', 1)[1].split('<h2>Historia workflow</h2>', 1)[0]
    assert cards.count('admin-repeatable-decision-item') == count
    for person in people:
        assert person['imie'] in cards
        assert person['nazwisko'] in cards
        assert person['email'] in cards
    if count == 0:
        assert 'Brak elementów.' in cards


def test_historical_submission_without_version_or_groups_renders(admin_app, admin_client):
    create_user(admin_app)
    form_id = create_form(admin_app, slug='legacy-no-groups')
    repository = admin_app.extensions['services'].submission_repository
    public_id = str(uuid4())
    repository.create({'submission_id': public_id, 'form_slug': 'legacy-no-groups',
                       'process_status': 'FORM_SUBMITTED', 'data_json': {'imie': 'Jan'}})
    submission_pk = repository.get_by_id(public_id)['id']
    login(admin_client)
    response = admin_client.get(f'/admin/forms/{form_id}/submissions/{submission_pk}')
    assert response.status_code == 200
    assert '<section id="repeatable-decisions">' not in response.get_data(as_text=True)
