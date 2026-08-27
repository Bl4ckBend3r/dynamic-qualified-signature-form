import json

import pytest

from services.public_submission_status_service import build_public_submission_status


def accepted_row(**updates):
    row = {
        "officer_decision": "TAK",
        "acceptance_required": "TAK",
        "declaration_required": "Tak",
        "agreement_required": "Tak",
        "declaration_generated": "",
        "declaration_filename": "",
        "declaration_signature_valid": "",
        "agreement_blocked": "",
        "agreement_block_reason": "",
        "agreement_generated": "",
        "agreement_filename": "",
        "agreement_signature_valid": "",
    }
    row.update(updates)
    return row


@pytest.mark.parametrize(
    ("row", "title", "next_action"),
    [
        (
            accepted_row(
                process_status="DECLARATION_WAITING_FOR_SIGNATURE",
                declaration_generated="Tak",
                declaration_filename="deklaracja.pdf",
            ),
            "Deklaracja jest gotowa do podpisania",
            "Podpisz i wgraj deklarację.",
        ),
        (
            accepted_row(
                process_status="AGREEMENT_READY",
                declaration_signature_valid="Tak",
            ),
            "Umowa jest gotowa do wygenerowania",
            "Wygeneruj umowę.",
        ),
        (
            accepted_row(
                process_status="AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE",
                declaration_signature_valid="Tak",
                agreement_generated="Tak",
                agreement_filename="umowa.pdf",
            ),
            "Umowa jest gotowa do podpisania",
            "Pobierz umowę, podpisz ją i wgraj podpisany plik w sekcji „Wgraj podpisane umowy”.",
        ),
        (
            accepted_row(
                process_status="AGREEMENT_UPLOADED_BY_BENEFICIARY",
                declaration_signature_valid="Tak",
                agreement_generated="Tak",
                agreement_filename="umowa.pdf",
                agreement_signature_valid="Tak",
            ),
            "Umowa oczekuje na podpis urzędu",
            "Poczekaj na podpis i potwierdzenie urzędu.",
        ),
        (
            accepted_row(
                process_status="AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
                declaration_signature_valid="Tak",
                agreement_generated="Tak",
                agreement_filename="umowa.pdf",
                agreement_signature_valid="Tak",
            ),
            "Umowa oczekuje na podpis urzędu",
            "Poczekaj na podpis i potwierdzenie urzędu.",
        ),
        (
            accepted_row(
                process_status="PROCESS_COMPLETED",
                declaration_signature_valid="Tak",
                agreement_generated="Tak",
                agreement_filename="umowa.pdf",
                agreement_signature_valid="Tak",
            ),
            "Proces został zakończony",
            "Nie musisz wykonywać kolejnych czynności.",
        ),
    ],
)
def test_public_status_scenarios_are_consistent(row, title, next_action):
    status = build_public_submission_status(row)

    assert status["status_title"] == title
    assert status["next_action"] == next_action


def test_blocked_agreement_overrides_stale_positive_status_and_disables_actions():
    status = build_public_submission_status(
        accepted_row(
            process_status="AGREEMENT_READY",
            declaration_signature_valid="Tak",
            agreement_blocked="Tak",
            agreement_block_reason="Warunki deklaracji nie zostały spełnione.",
            agreement_generated="Tak",
            agreement_filename="umowa.pdf",
        )
    )

    assert status["effective_process_status"] == "AGREEMENT_BLOCKED"
    assert status["status_title"] == "Umowa nie może zostać wygenerowana"
    assert status["status_description"] == (
        "Deklaracja została zweryfikowana, ale warunki wymagane do wygenerowania umowy nie zostały spełnione."
    )
    assert status["application_status"] == "Wniosek zaakceptowany przez urzędnika"
    assert status["declaration_status"] == "Etap zakończony poprawnie"
    assert status["agreement_status"] == "Umowa zablokowana"
    assert status["blocking_reason"] == "Warunki deklaracji nie zostały spełnione."
    assert status["can_generate_agreement"] is False
    assert status["can_download_agreement"] is False
    assert status["can_upload_signed_agreement"] is False
    assert "podpis" not in status["next_action"].lower()


def test_training_agreement_upload_is_available_only_after_download():
    agreement = {
        "id": "python",
        "filename": "python.pdf",
        "signature_valid": False,
        "participant_status": "agreement_generated",
        "agreement_downloaded": False,
    }
    row = accepted_row(
        process_status="AGREEMENT_READY",
        declaration_signature_valid="Tak",
        agreement_generated="Tak",
        agreement_filename="python.pdf",
        training_agreements=json.dumps([agreement]),
    )
    assert build_public_submission_status(row)["can_upload_signed_agreement"] is False

    agreement.update(participant_status="agreement_waiting_for_beneficiary_signature", agreement_downloaded=True)
    row["training_agreements"] = json.dumps([agreement])
    row["process_status"] = "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE"
    assert build_public_submission_status(row)["can_upload_signed_agreement"] is True


def test_public_presenter_exposes_only_one_current_status_message():
    status = build_public_submission_status(
        accepted_row(
            process_status="AGREEMENT_BLOCKED",
            declaration_signature_valid="Tak",
            agreement_blocked="Tak",
            agreement_block_reason="Brak spełnionych warunków.",
        )
    )
    messages = status["status_messages"]

    assert messages == [{"type": "current", "text": "Umowa nie może zostać wygenerowana"}]
    assert status["status"]["title"] == "Umowa nie może zostać wygenerowana"
    assert status["status"]["reason"] == "Brak spełnionych warunków."


@pytest.mark.parametrize(
    ("row", "expected_title", "expected_step"),
    [
        (
            accepted_row(
                workflow_step="agreement_signature",
                process_status="OFFICER_ACCEPTED",
                declaration_signature_valid="Tak",
                agreement_generated="Tak",
                agreement_filename="umowa.pdf",
            ),
            "Umowa jest gotowa do podpisania",
            "agreement_signature",
        ),
        (
            {
                "workflow_step": "officer_review",
                "process_status": "FORM_SUBMITTED",
                "officer_decision": "",
                "acceptance_required": "",
            },
            "Wniosek oczekuje na decyzję",
            "officer_review",
        ),
        (
            accepted_row(
                workflow_step="declaration_signature",
                process_status="OFFICER_ACCEPTED",
                declaration_generated="Tak",
                declaration_filename="deklaracja.pdf",
            ),
            "Deklaracja jest gotowa do podpisania",
            "declaration_signature",
        ),
        (
            accepted_row(
                workflow_step="waiting_for_correction",
                process_status="OFFICER_ACCEPTED",
                correction_message="Popraw dane kontaktowe.",
            ),
            "Wniosek wymaga poprawy",
            "waiting_for_correction",
        ),
        (
            {
                "workflow_step": "end_rejected",
                "process_status": "FORM_SUBMITTED",
                "officer_decision": "NIE",
                "officer_decision_reason": "Brak wymaganych danych.",
            },
            "Wniosek został odrzucony",
            "end_rejected",
        ),
        (
            accepted_row(
                workflow_step="completed",
                process_status="OFFICER_ACCEPTED",
                declaration_signature_valid="Tak",
                agreement_signature_valid="Tak",
            ),
            "Proces został zakończony",
            "completed",
        ),
    ],
)
def test_current_workflow_step_selects_exactly_one_public_status(row, expected_title, expected_step):
    status = build_public_submission_status(row)

    assert status["status"]["title"] == expected_title
    assert status["status"]["step"] == expected_step
    assert status["status_title"] == expected_title
    assert len(status["status_messages"]) == 1


def test_legacy_submission_without_workflow_step_uses_existing_process_fallback():
    status = build_public_submission_status(
        accepted_row(
            process_status="AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE",
            declaration_signature_valid="Tak",
            agreement_generated="Tak",
            agreement_filename="legacy-umowa.pdf",
        ),
        current_step="submission",
    )

    assert status["status"]["title"] == "Umowa jest gotowa do podpisania"
    assert status["status"]["step"] == "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE"
