import logging

from services.workflow_state_service import (
    DocumentState,
    FinalOutcome,
    layered_state_from_legacy,
)


def test_legacy_agreement_status_is_split_into_layers():
    state = layered_state_from_legacy("AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE")

    assert state.workflow_stage == "office_agreement_signature"
    assert state.document_states["agreement"] == DocumentState.SIGNED.value
    assert state.final_outcome is FinalOutcome.ACTIVE
    assert state.legacy_process_status == "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE"


def test_explicit_stage_and_document_state_win_over_legacy_mapping():
    state = layered_state_from_legacy(
        "AGREEMENT_READY",
        workflow_step="custom_agreement_review",
        document_states={"agreement": "GENERATED"},
    )

    assert state.workflow_stage == "custom_agreement_review"
    assert state.document_states == {"agreement": "GENERATED"}


def test_terminal_statuses_only_set_final_outcome():
    assert layered_state_from_legacy("PROCESS_COMPLETED").final_outcome is FinalOutcome.COMPLETED
    assert layered_state_from_legacy("OFFICER_REJECTED").final_outcome is FinalOutcome.REJECTED
    assert layered_state_from_legacy("CANCELLED").final_outcome is FinalOutcome.CANCELLED


def test_unknown_legacy_status_has_controlled_fallback(caplog):
    with caplog.at_level(logging.WARNING):
        state = layered_state_from_legacy("UNLISTED_LEGACY_VALUE")

    assert state.workflow_stage == "legacy_unknown"
    assert state.final_outcome is FinalOutcome.ACTIVE
    assert state.used_fallback is True
    assert "UNLISTED_LEGACY_VALUE" in caplog.text

