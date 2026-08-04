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
            "Umowa oczekuje na podpis beneficjenta",
            "Wgraj podpisaną umowę. Dopiero wtedy szkolenie i miejsce zostaną zablokowane.",
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


def test_training_agreement_upload_is_enabled_only_after_its_download():
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


def test_status_messages_are_deduplicated_and_follow_business_order():
    status = build_public_submission_status(
        accepted_row(
            process_status="AGREEMENT_BLOCKED",
            declaration_signature_valid="Tak",
            agreement_blocked="Tak",
            agreement_block_reason="Brak spełnionych warunków.",
        )
    )
    messages = status["status_messages"]

    assert [item["type"] for item in messages] == [
        "application",
        "declaration",
        "agreement",
        "blocking_reason",
        "next_action",
    ]
    assert len({(item["type"], item["text"]) for item in messages}) == len(messages)
    assert sum("Etap zakończony poprawnie" in item["text"] for item in messages) == 1
