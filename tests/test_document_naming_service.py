import pytest

from services.document_naming_service import (
    build_agreement_filename,
    build_declaration_filename,
    build_signed_filename,
    build_signed_submission_pdf_filename,
    build_submission_pdf_filename,
    document_type_directory,
    normalize_output_dir,
    resolve_pdf_storage_path,
)


def test_build_declaration_filename_matches_existing_pattern_behavior():
    filename = build_declaration_filename({"imiona": "Jan", "nazwisko": "Kowalski"})

    assert filename == "Jan_Kowalski-deklaracja.pdf"


def test_build_agreement_filename_supports_training_placeholders():
    filename = build_agreement_filename(
        {
            "first_name": "Anna",
            "last_name": "Nowak",
            "training_id": "python",
            "agreement_sequence": 2,
            "generated_date": "2026-06-08",
        },
        {"filename_pattern": "{first_name}_{last_name}-{training_id}-{agreement_sequence}-{generated_date}.pdf"},
    )

    assert filename == "Anna_Nowak-python-2-2026-06-08.pdf"


def test_signed_and_unsigned_submission_names_match_existing_names():
    assert build_submission_pdf_filename("formularz", "abc") == "formularz-abc.pdf"
    assert build_signed_submission_pdf_filename("formularz", "abc") == "formularz-abc-signed.pdf"
    assert build_signed_filename("deklaracja.pdf") == "deklaracja-signed.pdf"


def test_resolve_pdf_storage_path_distinguishes_signed_document_directories():
    unsigned = resolve_pdf_storage_path(
        output_dir="output",
        slug="formularz",
        filename="deklaracja.pdf",
        document_type="declaration",
        signed=False,
    )
    signed = resolve_pdf_storage_path(
        output_dir="output",
        slug="formularz",
        filename="deklaracja-signed.pdf",
        document_type="declaration",
        signed=True,
    )

    assert unsigned == "output/formularz/pdf/deklaracja/niepodpisane/deklaracja.pdf"
    assert signed == "output/formularz/pdf/deklaracja/podpisane/deklaracja-signed.pdf"


def test_resolve_pdf_storage_path_rejects_path_traversal_filename():
    with pytest.raises(ValueError):
        resolve_pdf_storage_path(output_dir="output", slug="formularz", filename="../secret.pdf")


def test_output_dir_and_document_type_helpers_are_normalized():
    assert normalize_output_dir("output/") == "output"
    assert document_type_directory("training_agreement", signed=True) == "umowy/podpisane"
    with pytest.raises(ValueError):
        normalize_output_dir("../output")
