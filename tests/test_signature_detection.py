from pathlib import Path

import pytest


def test_unsigned_pdf_is_not_detected_as_mobywatel_signed(tmp_path):
    try:
        from services.signature_detector import is_pdf_signed_by_mobywatel
    except ImportError:
        pytest.skip("Brak services.signature_detector.is_pdf_signed_by_mobywatel")

    pdf_path = tmp_path / "unsigned.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nUnsigned test PDF\n%%EOF")

    result = is_pdf_signed_by_mobywatel(pdf_path)

    assert result is False


def test_pdf_with_mobywatel_signature_marker_is_detected(tmp_path):
    try:
        from services.signature_detector import is_pdf_signed_by_mobywatel
    except ImportError:
        pytest.skip("Brak services.signature_detector.is_pdf_signed_by_mobywatel")

    pdf_path = tmp_path / "signed.pdf"
    pdf_path.write_bytes(
        b"%PDF-1.4\n"
        b"/Type /Sig\n"
        b"/Filter /Adobe.PPKLite\n"
        b"/SubFilter /adbe.pkcs7.detached\n"
        b"mObywatel\n"
        b"%%EOF"
    )

    result = is_pdf_signed_by_mobywatel(pdf_path)

    assert result is True