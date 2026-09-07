from io import BytesIO
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from asn1crypto import cms
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pypdf import PdfWriter
from pyhanko.pdf_utils.reader import PdfFileReader

from scripts.diagnose_pdf_signature import diagnose_pdf_signature, extract_pkcs7_certificate_diagnostics
from test_signature_verifier import signed_pdf_factory


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _pkcs7_with_signer_and_intermediate():
    now = datetime.now(timezone.utc)
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Intermediate")])
    root = (
        x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
        .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(root_key, hashes.SHA256())
    )
    signer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signer_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Controlled Test Signer")])
    signer = (
        x509.CertificateBuilder().subject_name(signer_name).issuer_name(root_name)
        .public_key(signer_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=True, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("signer.test.invalid")]), critical=False)
        .sign(root_key, hashes.SHA256())
    )
    der = (
        pkcs7.PKCS7SignatureBuilder().set_data(b"controlled diagnostic payload")
        .add_signer(signer, signer_key, hashes.SHA256()).add_certificate(root)
        .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary])
    )
    content_info = cms.ContentInfo.load(der)
    return der, content_info["content"]["signer_infos"][0]


def test_diagnostic_confirms_signed_attributes_and_signer_certificate(signed_pdf_factory, tmp_path):
    path = tmp_path / "valid.pdf"
    path.write_bytes(signed_pdf_factory(_blank_pdf(), "profil_zaufany"))

    report = diagnose_pdf_signature(
        path,
        original_path=path,
        use_openssl=False,
        allowed_signatures=["profil_zaufany"],
    )

    signature = report["signatures"][0]
    assert report["provider_allowed"] is True
    assert report["original_comparison"]["identical"] is True
    assert signature["byte_range_digest_matches_message_digest"] is True
    assert signature["sid_matches_signer_certificate"] is True
    assert signature["signing_certificate_v2_matches_signer_certificate"] is True
    assert signature["public_key_source_matches_signer_certificate"] is True
    assert signature["pyhanko"]["intact"] is True
    assert signature["pyhanko"]["valid"] is True
    assert signature["cryptography"] == {"cms_signature_valid": True, "error": None}
    certificates = signature["certificate_diagnostics"]
    assert certificates["reason_code"] == "OK"
    assert certificates["certificate_count"] == 1
    assert certificates["signer_certificate_found"] is True


def test_pkcs7_certificate_extraction_handles_multiple_certificates_and_matches_signer():
    der, signer_info = _pkcs7_with_signer_and_intermediate()

    result = extract_pkcs7_certificate_diagnostics(der, signer_info)

    assert result["reason_code"] == "OK"
    assert result["certificate_count"] == 2
    signers = [certificate for certificate in result["certificates"] if certificate["is_signer_certificate"]]
    assert len(signers) == 1
    signer = signers[0]
    assert signer["public_key"] == {"type": "RSA", "key_size": 2048}
    assert signer["key_usage"] == ["digital_signature", "content_commitment"]
    assert signer["extended_key_usage"][0]["oid"] == ExtendedKeyUsageOID.CODE_SIGNING.dotted_string
    assert signer["san"] == ["signer.test.invalid"]
    assert signer["basic_constraints"] == {"ca": False, "path_length": None}


def test_pkcs7_certificate_extraction_uses_der_length_to_remove_pdf_padding():
    der, signer_info = _pkcs7_with_signer_and_intermediate()

    result = extract_pkcs7_certificate_diagnostics(der + (b"\x00" * 256), signer_info)

    assert result["reason_code"] == "OK"
    assert result["certificate_count"] == 2
    assert extract_pkcs7_certificate_diagnostics(der + b"\x01", signer_info)["reason_code"] == "INVALID_CMS_DER"


def test_pkcs7_certificate_extraction_reports_missing_certificates():
    der, _ = _pkcs7_with_signer_and_intermediate()
    content_info = cms.ContentInfo.load(der)
    content_info["content"]["certificates"] = None
    signer_info = content_info["content"]["signer_infos"][0]

    result = extract_pkcs7_certificate_diagnostics(content_info.dump(), signer_info)

    assert result == {
        "certificate_count": 0,
        "signer_certificate_found": False,
        "reason_code": "NO_CERTIFICATES_IN_CMS",
        "certificates": [],
    }


def test_pkcs7_certificate_extraction_reports_invalid_cms_without_traceback():
    result = extract_pkcs7_certificate_diagnostics(b"not DER CMS", None)

    assert result["reason_code"] == "INVALID_CMS_DER"
    assert result["certificates"] == []


def test_diagnostic_rejects_bad_signature_value_even_when_message_digest_matches(
    signed_pdf_factory, tmp_path, monkeypatch
):
    signed = signed_pdf_factory(_blank_pdf(), "profil_zaufany")
    embedded = PdfFileReader(BytesIO(signed)).embedded_signatures[0]
    signature_hex = embedded.signer_info["signature"].native.hex().encode()
    offset = signed.lower().index(signature_hex)
    damaged = signed[:offset] + (b"0" if signed[offset:offset + 1] != b"0" else b"1") + signed[offset + 1:]
    path = tmp_path / "invalid-signature-value.pdf"
    path.write_bytes(damaged)

    monkeypatch.setattr("scripts.diagnose_pdf_signature.shutil.which", lambda command: "openssl")
    monkeypatch.setattr(
        "scripts.diagnose_pdf_signature.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=4, stderr=b"CMS Verification failure: bad signature"),
    )

    report = diagnose_pdf_signature(path, allowed_signatures=["profil_zaufany"])

    signature = report["signatures"][0]
    assert report["detected_signature_type"] == "profil_zaufany"
    assert report["provider_allowed"] is True
    assert report["reason_code"] == "SIGNATURE_CRYPTOGRAPHIC_FAILURE"
    assert report["conclusion"] == "CMS_SIGNATURE_VALUE_INVALID"
    assert signature["byte_range_digest_matches_message_digest"] is True
    assert signature["pyhanko"]["intact"] is True
    assert signature["pyhanko"]["valid"] is False
    assert signature["cryptography"] == {
        "cms_signature_valid": False,
        "error": "bad_signature",
    }
    assert signature["openssl"] == {
        "available": True,
        "cms_signature_valid": False,
        "error": "bad_signature",
    }
