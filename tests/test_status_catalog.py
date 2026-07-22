from services.status_catalog import (
    ProcessStatusCode,
    can_transition,
    export_status_catalog_for_frontend,
    get_status_label,
    is_final_status,
    is_rejected_status,
    normalize_status,
)


def test_normalize_status_returns_catalog_code_for_legacy_status():
    assert normalize_status("OFFICER_REJECTED") == ProcessStatusCode.REVIEW_REJECTED
    assert normalize_status("AGREEMENT_SIGNED") == ProcessStatusCode.COMPLETED
    assert normalize_status("AGREEMENT_UPLOADED") == ProcessStatusCode.WAITING_FOR_REVIEW
    assert normalize_status("BENEFICIARY_AGREEMENT_CONFIRMED") == ProcessStatusCode.COMPLETED
    assert normalize_status("AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE") == ProcessStatusCode.WAITING_FOR_REVIEW
    assert normalize_status("AGREEMENT_SIGNED_BY_OFFICE") == ProcessStatusCode.COMPLETED


def test_status_flags_and_labels_are_shared():
    assert get_status_label("accepted_waiting_for_additional_fields") == "Wniosek zaakceptowany - uzupełnij dodatkowe informacje"
    assert is_rejected_status("OFFICER_REJECTED")
    assert is_final_status("AGREEMENT_SIGNED")
    assert not is_final_status("AGREEMENT_UPLOADED")
    assert get_status_label("BENEFICIARY_AGREEMENT_REJECTED") == "Umowa wymaga poprawy"
    assert get_status_label("BENEFICIARY_AGREEMENT_CONFIRMED") == "Umowa podpisana przez urząd"
    assert get_status_label("AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE") == "Umowa oczekuje na podpis po stronie urzędu"


def test_transition_matrix_for_target_statuses():
    assert can_transition("SUBMITTED", "WAITING_FOR_REVIEW")
    assert not can_transition("SUBMITTED", "COMPLETED")


def test_export_status_catalog_for_frontend_contains_legacy_mappings():
    catalog = export_status_catalog_for_frontend()

    assert catalog["statuses"]["COMPLETED"]["final"] is True
    assert catalog["legacy_mappings"]["FORM_SUBMITTED"] == "SUBMITTED"
