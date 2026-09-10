from __future__ import annotations


def test_authorized_result_does_not_leak_access_link_without_session_storage(participant_page, live_server, e2e_environment, submit_participant_form):
    page = participant_page
    public_id = submit_participant_form(page)
    token = e2e_environment.submission_snapshot(public_id)['access_token']
    page.evaluate('sessionStorage.clear()')
    page.add_init_script("Object.defineProperty(window, 'sessionStorage', {get() {throw new Error('Storage disabled');}})")
    page.goto(f'{live_server}/result/participant-e2e/{public_id}?token={token}')
    link = page.get_by_role('link', name='Przejdź do dokumentów do podpisania')
    assert link.count() == 0
    assert token not in page.content()
    assert page.locator('input[name="access_token"]').count() == 0


def test_status_navigation_is_readonly_and_does_not_use_participant_credential(participant_page, live_server, e2e_environment, submit_participant_form, tmp_path):
    page = participant_page
    public_id = submit_participant_form(page)
    token = e2e_environment.submission_snapshot(public_id)['access_token']
    page.get_by_role('link', name='Sprawdź status', exact=True).click()
    assert page.get_by_role('heading', name='Sprawdź status', exact=True).is_visible()
    page.get_by_label('Numer zgłoszenia', exact=True).fill(public_id)
    with page.expect_response(f'**/api/public/submissions/{public_id}/status') as received:
        page.get_by_role('button', name='Sprawdź status', exact=True).click()
    assert received.value.status == 200
    assert not received.value.request.headers.get('authorization')
    assert set(received.value.json()) == {'status_label'}
    page.get_by_role('heading', name='Aktualny status', exact=True).wait_for()
    assert page.locator('.status-result__badge').inner_text() == received.value.json()['status_label']
    assert public_id in page.locator('#public-status-result').inner_text()
    assert token not in page.content()
    assert page.locator('input[type="file"], #sign-documents-form').count() == 0
    assert page.locator('#public-status-result a').count() == 0
    assert 'jan@example.invalid' not in page.content()
    assert 'access_token' not in page.content()
    assert page.locator('#public-status-result').locator('..').get_attribute('aria-live') == 'polite'

    field = page.get_by_label('Numer zgłoszenia', exact=True)
    submit = page.get_by_role('button', name='Sprawdź status', exact=True)
    copy = page.get_by_role('button', name='Kopiuj numer zgłoszenia', exact=True)
    for width in (1366, 1024, 768, 390):
        page.set_viewport_size({'width': width, 'height': 900})
        card_box = page.locator('.status-page').bounding_box()
        input_box, button_box = field.bounding_box(), submit.bounding_box()
        assert card_box['width'] <= 820
        assert abs(card_box['x'] - (width - card_box['width']) / 2) < 2
        HEIGHT_TOLERANCE = 0.1

        assert 46 - HEIGHT_TOLERANCE <= input_box['height'] <= 48 + HEIGHT_TOLERANCE
        assert 46 - HEIGHT_TOLERANCE <= button_box['height'] <= 48 + HEIGHT_TOLERANCE
        assert copy.bounding_box()['height'] >= 44
        assert field.evaluate('el => parseFloat(getComputedStyle(el).fontSize)') >= 16
        if width > 600:
            assert abs(input_box['y'] - button_box['y']) < 2
            assert button_box['x'] >= input_box['x'] + input_box['width']
        else:
            assert button_box['y'] >= input_box['y'] + input_box['height']
            assert abs(input_box['width'] - button_box['width']) < 2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        field.focus()
        page.keyboard.press('Tab')
        assert submit.evaluate("el => el === document.activeElement && el.matches(':focus-visible') && getComputedStyle(el).outlineStyle === 'solid'")
        if width in (1366, 390):
            page.screenshot(path=str(tmp_path / f'public-status-{width}.png'), full_page=True)
    print(f'Public status screenshots: {tmp_path}')

    page.context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=live_server)
    copy.click()
    page.get_by_text('Skopiowano numer zgłoszenia.', exact=True).wait_for()
    assert page.evaluate('navigator.clipboard.readText()') == public_id

    # Hold one response to verify the loading state and duplicate-submit guard.
    held = []
    page.route('**/api/public/submissions/*/status', lambda route: held.append(route))
    field.fill('invalid-id')
    with page.expect_request('**/api/public/submissions/invalid-id/status'):
        submit.click()
    loading = page.get_by_role('button', name='Sprawdzanie…', exact=True)
    assert loading.is_disabled()
    page.locator('#public-status-form').evaluate('form => { form.requestSubmit(); form.requestSubmit(); }')
    assert len(held) == 1
    held.pop().fulfill(status=404, json={})
    message = page.get_by_text('Nie można odnaleźć statusu dla podanego numeru zgłoszenia.', exact=True)
    message.wait_for()
    assert message.count() == 1
    assert page.locator('#public-status-result').is_hidden()
    assert submit.is_enabled()

    # Editing the ID while a response is pending must not display the old result.
    field.fill(public_id)
    with page.expect_request(f'**/api/public/submissions/{public_id}/status'):
        submit.click()
    field.fill('another-id')
    held.pop().fulfill(status=200, json={'status_label': 'Poprzedni wynik'})
    page.wait_for_function("!document.querySelector('#public-status-form button').disabled")
    assert page.locator('#public-status-result').is_hidden()
    assert 'Poprzedni wynik' not in page.content()


def test_status_without_credential_or_with_invalid_credential_has_one_access_message(participant_page, live_server, e2e_environment, submit_participant_form):
    page = participant_page
    public_id = submit_participant_form(page)
    page.evaluate('sessionStorage.clear()')
    requests = []
    page.on('request', lambda request: requests.append(request.url) if '/acceptance-status' in request.url else None)
    for suffix in ('', f'?submission_id={public_id}', f'?submission_id={public_id}&token=invalid'):
        page.goto(f'{live_server}/do-podpisania{suffix}')
        page.wait_for_load_state('networkidle')
        assert page.locator('#submission_id').is_hidden()
        assert page.locator('[data-public-current-status]').count() == 1
        assert page.get_by_text('Otwórz link dostępu', exact=True).is_visible()
        assert 'Nie znaleziono wniosku' not in page.locator('body').inner_text()
        assert not requests
    # A stale saved credential reaches the API, but produces the same single message.
    page.evaluate("id => {sessionStorage.setItem(`participant-last-submission:${window.APP_BASE_PATH}`, id); sessionStorage.setItem(`participant-access:${window.APP_BASE_PATH}:${id}`, 'invalid');}", public_id)
    with page.expect_response(f'**/api/submissions/{public_id}/acceptance-status') as denied:
        page.goto(f'{live_server}/do-podpisania')
    assert denied.value.status == 404
    page.get_by_text('Otwórz link dostępu', exact=True).wait_for()
    assert page.locator('[data-public-current-status]').count() == 1
    assert page.locator('#acceptance-status').inner_text() == ''


def test_public_wording_keyboard_mobile_and_contrast(participant_page, live_server, submit_participant_form):
    page = participant_page
    contrast_check = """(selector) => {
        const rgb = value => value.match(/[\\d.]+/g).slice(0, 3).map(Number);
        const luminance = color => rgb(color).map(v => {
            v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
        }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
        return [...document.querySelectorAll(selector)].filter(el => el.getClientRects().length).map(el => {
            let parent = el, background;
            while (parent) {
                background = getComputedStyle(parent).backgroundColor;
                if (background !== 'rgba(0, 0, 0, 0)' && background !== 'transparent') break;
                parent = parent.parentElement;
            }
            const style = getComputedStyle(el);
            const a = luminance(style.color), b = luminance(background || 'rgb(255, 255, 255)');
            return {ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05), size: parseFloat(style.fontSize)};
        });
    }"""

    for width in (1280, 390):
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto(live_server)
        tile = page.get_by_role('link', name='Wypełnij formularz: Formularz E2E uczestnika')
        assert tile.locator('.form-tile__action').inner_text() == 'Wypełnij formularz'
        assert tile.locator('.form-tile__action').bounding_box()['height'] >= 44
        page.locator('body').click(position={'x': 1, 'y': 1})
        for _ in range(12):
            page.keyboard.press('Tab')
            if tile.evaluate("el => el === document.activeElement"):
                break
        assert tile.evaluate("el => el.matches(':focus-visible') && getComputedStyle(el).outlineStyle === 'solid'")
        for item in page.evaluate(contrast_check, '.forms-showcase__header p, .form-tile__action'):
            assert item['ratio'] >= 4.5
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.keyboard.press('Enter')
        page.wait_for_url('**/form/participant-e2e')
        for item in page.evaluate(contrast_check, '.form-description, label, .form-required-note, .btn-primary'):
            assert item['ratio'] >= 4.5
            assert item['size'] >= 14
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.goto(f'{live_server}/do-podpisania')
        assert page.locator('#signing-status-help').inner_text() == 'Sprawdzimy status wniosku przed pokazaniem dokumentów do podpisu.'
        for item in page.evaluate(contrast_check, '.card-header p, .documents-instructions, .status-tile__description'):
            assert item['ratio'] >= 4.5
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

    submit_participant_form(page)
    assert page.get_by_role('heading', name='Potwierdzamy Twoje zgłoszenie').is_visible()
    for item in page.evaluate(contrast_check, '.card-header p, .result-label, .btn-primary'):
        assert item['ratio'] >= 4.5


def test_participant_happy_path_uses_real_browser_http_and_security_guards(
    chromium_browser,
    participant_page,
    live_server,
    e2e_environment,
    submit_participant_form,
):
    submission_id = submit_participant_form(participant_page)
    submission = e2e_environment.submission_snapshot(submission_id)
    access_token = submission["access_token"]

    assert access_token
    assert access_token not in participant_page.locator("body").inner_text()
    assert submission_id in e2e_environment.dispatched_mail

    participant_page.get_by_role(
        "link", name="Przejdź do dokumentów do podpisania"
    ).click()
    participant_page.wait_for_url("**/do-podpisania**")

    e2e_environment.complete_submission(submission_id)
    participant_page.goto(
        f"{live_server}/api/submissions/{submission_id}/workflow-status?token={access_token}"
    )
    assert "COMPLETED" in participant_page.locator("body").inner_text()

    second_context = chromium_browser.new_context()
    try:
        second_page = second_context.new_page()
        other_submission_id = submit_participant_form(
            second_page,
            first_name="Anna",
            last_name="Inna",
            email="anna@example.invalid",
        )
    finally:
        second_context.close()

    idor_response = participant_page.goto(
        f"{live_server}/result/participant-e2e/{other_submission_id}?token={access_token}"
    )
    assert idor_response.status == 404

    csrf_response = participant_page.request.post(
        f"{live_server}/additional-fields/participant-e2e/{submission_id}?token={access_token}",
        data={},
    )
    assert csrf_response.status == 400
