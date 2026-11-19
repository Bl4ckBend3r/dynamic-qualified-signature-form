"""Regression checks for PDF revision identity after trusted signatures."""
from io import BytesIO
from hashlib import sha256

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from services.documents.pdf_revision_service import compare_pdf_revision
from services.document_service import DocumentService
from signature_verifier import signature_verification_result, verify_signed_pdf
from test_signature_verifier import certificate_chain_signed_pdf_factory, signed_pdf_factory


def _agreement(text="Agreement A"):
    stream = BytesIO()
    document = canvas.Canvas(stream, pagesize=(300, 400), pageCompression=0)
    document.drawString(40, 320, text)
    document.save()
    return stream.getvalue()


def _rewrite(source):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(source)))
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def _valid_signature(signed, tmp_path):
    path = tmp_path / "signed.pdf"
    path.write_bytes(signed)
    return signature_verification_result(verify_signed_pdf(path), ["mszafir"])


def test_original_agreement_with_incremental_signature(signed_pdf_factory, tmp_path):
    original = _agreement()
    signed = signed_pdf_factory(original, "mszafir")
    assert _valid_signature(signed, tmp_path) == "VALID_SIGNATURE"
    assert compare_pdf_revision(original, signed) == (True, "OK", "incremental_bytes_and_pdf")


def test_rewritten_agreement_with_valid_mszafir_signature(signed_pdf_factory, tmp_path):
    original = _agreement()
    rewritten = _rewrite(original)
    assert rewritten != original and not rewritten.startswith(original)
    signed = signed_pdf_factory(rewritten, "mszafir")
    assert _valid_signature(signed, tmp_path) == "VALID_SIGNATURE"
    assert compare_pdf_revision(original, signed) == (True, "OK", "canonical_pdf")


def test_valid_signature_on_different_agreement_is_rejected(signed_pdf_factory, tmp_path):
    original = _agreement("Agreement A")
    signed = signed_pdf_factory(_agreement("Agreement B"), "mszafir")
    assert _valid_signature(signed, tmp_path) == "VALID_SIGNATURE"
    assert compare_pdf_revision(original, signed)[:2] == (False, "DOCUMENT_REVISION_MISMATCH")


def test_changed_contract_content_in_rewritten_pdf_is_rejected(signed_pdf_factory, tmp_path):
    original = _agreement("Price 100")
    signed = signed_pdf_factory(_rewrite(_agreement("Price 900")), "mszafir")
    assert _valid_signature(signed, tmp_path) == "VALID_SIGNATURE"
    assert compare_pdf_revision(original, signed)[:2] == (False, "DOCUMENT_REVISION_MISMATCH")


def test_cryptographically_invalid_signature_is_not_admitted(signed_pdf_factory, tmp_path):
    original = _agreement()
    signed = signed_pdf_factory(original, "mszafir")
    tampered = signed.replace(b"Agreement A", b"Agreement B", 1)
    assert tampered != signed
    assert _valid_signature(tampered, tmp_path) == "INVALID_SIGNATURE"


def test_unreadable_rebuilt_pdf_cannot_confirm_identity():
    assert compare_pdf_revision(_agreement(), b"%PDF-1.7\ninvalid")[:2] == (
        False, "DOCUMENT_IDENTITY_UNCONFIRMED")


def test_document_service_checks_named_source_and_stored_checksum(monkeypatch):
    source = _agreement()
    selected = []
    service = DocumentService()
    monkeypatch.setattr(service, "read_document_bytes_for_download",
                        lambda row, filename, *, signed: selected.append((filename, signed)) or source)
    monkeypatch.setattr(service, "_file_metadata",
                        lambda row, filename, *, signed: {"checksum_sha256": sha256(source).hexdigest()})
    assert service.compare_signed_revision({}, "old-version.pdf", _rewrite(source), signed=False) == (
        True, "VALID", "OK", "canonical_pdf")
    assert selected == [("old-version.pdf", False)]
    monkeypatch.setattr(service, "_file_metadata",
                        lambda row, filename, *, signed: {"checksum_sha256": "0" * 64})
    assert service.compare_signed_revision({}, "old-version.pdf", _rewrite(source), signed=False) == (
        False, "ERROR", "DOCUMENT_SOURCE_HASH_MISMATCH", "")


def test_profile_signature_is_bound_to_stored_declaration_revision(
    certificate_chain_signed_pdf_factory, monkeypatch
):
    material = certificate_chain_signed_pdf_factory
    source = material["source_pdf"]
    signed = material["signed_path"].read_bytes()
    service = DocumentService()
    selected = []
    monkeypatch.setattr(service, "read_document_bytes_for_download",
                        lambda row, filename, *, signed: selected.append((filename, signed)) or source)
    monkeypatch.setattr(service, "_file_metadata",
                        lambda row, filename, *, signed: {"checksum_sha256": sha256(source).hexdigest()})

    assert service.compare_signed_revision({}, "historical-declaration.pdf", signed, signed=False)[:2] == (
        True, "VALID")
    assert selected == [("historical-declaration.pdf", False)]

    different = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=200)
    writer.write(different)
    monkeypatch.setattr(service, "read_document_bytes_for_download",
                        lambda row, filename, *, signed: different.getvalue())
    monkeypatch.setattr(service, "_file_metadata",
                        lambda row, filename, *, signed: {"checksum_sha256": sha256(different.getvalue()).hexdigest()})
    assert service.compare_signed_revision({}, "another-declaration.pdf", signed, signed=False)[:3] == (
        False, "INVALID", "DOCUMENT_REVISION_MISMATCH")
