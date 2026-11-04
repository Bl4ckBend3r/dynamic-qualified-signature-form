from statuses import COMPLETED, REVIEW_ACCEPTED, REVIEW_REJECTED, WAITING_FOR_SIGNATURE, normalize_status


def test_normalize_legacy_statuses():
    assert normalize_status("DECLARATION_WAITING_FOR_SIGNATURE") == WAITING_FOR_SIGNATURE
    assert normalize_status("AGREEMENT_WAITING_FOR_SIGNATURE") == WAITING_FOR_SIGNATURE
    assert normalize_status("OFFICER_ACCEPTED") == REVIEW_ACCEPTED
    assert normalize_status("OFFICER_REJECTED") == REVIEW_REJECTED
    assert normalize_status("PARTICIPANT_ACCEPTED") == COMPLETED


def test_normalize_keeps_target_status():
    assert normalize_status("WAITING_FOR_REVIEW") == "WAITING_FOR_REVIEW"
