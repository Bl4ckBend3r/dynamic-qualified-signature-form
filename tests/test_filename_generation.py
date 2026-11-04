from services.document_service import DocumentService


def test_build_filename_from_allowed_placeholders():
    filename = DocumentService().build_filename(
        "{first_name}_{last_name}-{submission_id}.pdf",
        {"first_name": "Jan", "last_name": "Kowalski", "submission_id": "abc-123"},
    )

    assert filename == "Jan_Kowalski-abc-123.pdf"


def test_build_filename_falls_back_on_unknown_placeholder():
    filename = DocumentService().build_filename("{unknown}.pdf", {"submission_id": "abc"})

    assert filename == "abc.pdf"
