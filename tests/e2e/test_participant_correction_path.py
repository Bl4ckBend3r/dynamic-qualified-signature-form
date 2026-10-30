from __future__ import annotations


def test_participant_correction_preserves_history_and_returns_to_submission_stage(
    chromium_browser,
    participant_page,
    live_server,
    e2e_environment,
    submit_participant_form,
):
    submission_id = submit_participant_form(participant_page)
    submission = e2e_environment.submission_snapshot(submission_id)
    access_token = submission["access_token"]

    second_context = chromium_browser.new_context()
    try:
        second_page = second_context.new_page()
        other_submission_id = submit_participant_form(
            second_page,
            first_name="Ewa",
            last_name="Obca",
            email="ewa@example.invalid",
        )
    finally:
        second_context.close()

    e2e_environment.return_for_correction(submission_id)
    correction_url = (
        f"{live_server}/form/participant-e2e/correction/{submission_id}"
        f"?token={access_token}"
    )
    participant_page.goto(correction_url)

    assert participant_page.locator('[name="imie"]').input_value() == "Jan"
    assert participant_page.locator('[name="nazwisko"]').input_value() == "Nowak"
    assert not participant_page.locator('[name="readonly_note"]').is_editable()

    idor_response = participant_page.goto(
        f"{live_server}/form/participant-e2e/correction/{other_submission_id}"
        f"?token={access_token}"
    )
    assert idor_response.status == 404

    participant_page.goto(correction_url)
    participant_page.locator('[name="imie"]').fill("Jan Poprawiony")
    participant_page.get_by_role(
        "button", name="Wyślij poprawione zgłoszenie", exact=True
    ).click()
    participant_page.get_by_test_id("submission-id").wait_for(state="visible")
    assert "Zgłoszenie poprawiono i przesłano ponownie" in participant_page.locator(
        "body"
    ).inner_text()

    corrected = e2e_environment.submission_snapshot(submission_id)
    assert corrected["process_status"] == "FORM_SUBMITTED"
    assert corrected["workflow_step"] == "submission"
    assert corrected["data_json"]["imie"] == "Jan Poprawiony"
    assert corrected["data_json"]["nazwisko"] == "Nowak"
    assert corrected["data_json"]["_correction_history"][0]["reason"] == "Korekta E2E"
    assert "returned_for_correction" in e2e_environment.workflow_event_sources(submission_id)
    assert "correction_resubmitted" in e2e_environment.workflow_event_sources(submission_id)
