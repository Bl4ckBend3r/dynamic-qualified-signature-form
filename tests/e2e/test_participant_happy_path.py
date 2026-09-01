from __future__ import annotations


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
