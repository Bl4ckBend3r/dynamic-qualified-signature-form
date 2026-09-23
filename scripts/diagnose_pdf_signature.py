"""Read-only, privacy-safe PDF/CMS signature diagnostics for development."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from asn1crypto import cms
from asn1crypto import x509 as asn1_x509
from cryptography import x509 as crypto_x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
from cryptography.x509.oid import ExtensionOID
from pyhanko.pdf_utils.reader import PdfFileReader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from signature_verifier import resolve_signature_policy, verify_signed_pdf  # noqa: E402


def _hash(name: str, payload: bytes) -> bytes:
    return hashlib.new(name.replace("-", "").lower(), payload).digest()


def _signed_attribute(signer_info, name: str):
    for attribute in signer_info["signed_attrs"]:
        if attribute["type"].native == name:
            return attribute["values"][0]
    return None


def _sid_matches_certificate(signer_info, certificate) -> bool:
    sid = signer_info["sid"]
    if sid.name == "issuer_and_serial_number":
        value = sid.chosen
        return value["issuer"] == certificate.issuer and value["serial_number"].native == certificate.serial_number
    if sid.name == "subject_key_identifier":
        try:
            return sid.chosen.native == certificate.key_identifier
        except (KeyError, ValueError):
            return False
    return False


def _signing_certificate_v2_matches(signer_info, certificate) -> bool | None:
    attribute = _signed_attribute(signer_info, "signing_certificate_v2")
    if attribute is None or not attribute["certs"]:
        return None
    cert_id = attribute["certs"][0]
    algorithm = cert_id["hash_algorithm"]["algorithm"].native or "sha256"
    return _hash(algorithm, certificate.dump()) == cert_id["cert_hash"].native


def _normalise_cms_der(cms_bytes: bytes) -> bytes:
    """Parse one DER ContentInfo and discard only bytes after its DER length."""
    try:
        cms_der = cms.ContentInfo.load(cms_bytes).dump()
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_CMS_DER") from exc
    trailing = cms_bytes[len(cms_der):]
    if cms_bytes[:len(cms_der)] != cms_der or any(trailing):
        raise ValueError("INVALID_CMS_DER")
    return cms_der


def _optional_extension(certificate, oid):
    try:
        return certificate.extensions.get_extension_for_oid(oid).value
    except crypto_x509.ExtensionNotFound:
        return None


def _certificate_time(certificate, utc_name: str, legacy_name: str) -> str:
    try:
        value = getattr(certificate, utc_name)
    except AttributeError:
        value = getattr(certificate, legacy_name)
    return value.isoformat()


def _public_key_description(public_key) -> dict:
    if isinstance(public_key, rsa.RSAPublicKey):
        key_type = "RSA"
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        key_type = "EC"
    else:
        key_type = public_key.__class__.__name__
    return {"type": key_type, "key_size": getattr(public_key, "key_size", None)}


def _certificate_details(certificate, signer_info, index: int) -> dict:
    if not isinstance(certificate, crypto_x509.Certificate):
        raise TypeError("UNSUPPORTED_CERTIFICATE_TYPE")
    asn1_certificate = asn1_x509.Certificate.load(certificate.public_bytes(Encoding.DER))
    is_signer = _sid_matches_certificate(signer_info, asn1_certificate)
    key_usage = _optional_extension(certificate, ExtensionOID.KEY_USAGE)
    if key_usage is None:
        key_usage_values = None
    else:
        key_usage_values = [
            name for name in (
                "digital_signature",
                "content_commitment",
                "key_encipherment",
                "key_cert_sign",
                "crl_sign",
            ) if getattr(key_usage, name)
        ]
    extended_key_usage = _optional_extension(certificate, ExtensionOID.EXTENDED_KEY_USAGE)
    san = _optional_extension(certificate, ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    basic_constraints = _optional_extension(certificate, ExtensionOID.BASIC_CONSTRAINTS)
    return {
        "index": index,
        "is_signer_certificate": is_signer,
        "version": certificate.version.name,
        "serial_number": str(certificate.serial_number),
        "subject": certificate.subject.rfc4514_string(),
        "issuer": certificate.issuer.rfc4514_string(),
        "not_valid_before": _certificate_time(certificate, "not_valid_before_utc", "not_valid_before"),
        "not_valid_after": _certificate_time(certificate, "not_valid_after_utc", "not_valid_after"),
        "public_key": _public_key_description(certificate.public_key()),
        "key_usage": key_usage_values,
        "extended_key_usage": None if extended_key_usage is None else [
            {"oid": oid.dotted_string, "name": getattr(oid, "_name", None)} for oid in extended_key_usage
        ],
        "san": None if san is None else [str(value.value) for value in san],
        "basic_constraints": None if basic_constraints is None else {
            "ca": basic_constraints.ca,
            "path_length": basic_constraints.path_length,
        },
        "signing_certificate_v2_matches": (
            _signing_certificate_v2_matches(signer_info, asn1_certificate) if is_signer else None
        ),
    }


def extract_pkcs7_certificate_diagnostics(cms_bytes: bytes, signer_info) -> dict:
    """Extract all X.509 certificates from CMS without assuming their order."""
    try:
        cms_der = _normalise_cms_der(cms_bytes)
    except ValueError:
        return {"certificate_count": 0, "signer_certificate_found": False,
                "reason_code": "INVALID_CMS_DER", "certificates": []}
    try:
        content_info = cms.ContentInfo.load(cms_der)
        if content_info["content_type"].native != "signed_data":
            raise ValueError
        if content_info["content"]["certificates"].native is None:
            return {"certificate_count": 0, "signer_certificate_found": False,
                    "reason_code": "NO_CERTIFICATES_IN_CMS", "certificates": []}
    except (KeyError, TypeError, ValueError):
        return {"certificate_count": 0, "signer_certificate_found": False,
                "reason_code": "INVALID_CMS_DER", "certificates": []}
    try:
        certificates = pkcs7.load_der_pkcs7_certificates(cms_der)
    except ValueError:
        return {"certificate_count": 0, "signer_certificate_found": False,
                "reason_code": "CERTIFICATE_PARSE_ERROR", "certificates": []}
    if not certificates:
        return {"certificate_count": 0, "signer_certificate_found": False,
                "reason_code": "NO_CERTIFICATES_IN_CMS", "certificates": []}
    try:
        details = [
            _certificate_details(certificate, signer_info, index)
            for index, certificate in enumerate(certificates, start=1)
        ]
    except TypeError:
        return {"certificate_count": len(certificates), "signer_certificate_found": False,
                "reason_code": "UNSUPPORTED_CERTIFICATE_TYPE", "certificates": []}
    except (ValueError, crypto_x509.DuplicateExtension):
        return {"certificate_count": len(certificates), "signer_certificate_found": False,
                "reason_code": "CERTIFICATE_PARSE_ERROR", "certificates": []}
    return {
        "certificate_count": len(details),
        "signer_certificate_found": any(item["is_signer_certificate"] for item in details),
        "reason_code": "OK",
        "certificates": details,
    }


def _openssl_diagnostic(cms_der: bytes, signed_content: bytes) -> dict:
    executable = shutil.which("openssl")
    if not executable:
        return {"available": False, "cms_signature_valid": None, "error": "openssl_not_available"}
    with tempfile.TemporaryDirectory(prefix="pdf-signature-diagnostic-") as directory:
        root = Path(directory)
        cms_path = root / "signature.der"
        content_path = root / "signed-content.bin"
        cms_path.write_bytes(cms_der)
        content_path.write_bytes(signed_content)
        completed = subprocess.run(
            [executable, "cms", "-verify", "-inform", "DER", "-in", str(cms_path),
             "-content", str(content_path), "-noverify", "-out", "NUL" if sys.platform == "win32" else "/dev/null"],
            capture_output=True,
            check=False,
            timeout=30,
        )
    error_text = completed.stderr.decode("utf-8", "replace").lower()
    if completed.returncode == 0:
        error = None
    elif "bad signature" in error_text or "verification failure" in error_text:
        error = "bad_signature"
    else:
        error = "verification_command_failed"
    return {"available": True, "cms_signature_valid": completed.returncode == 0, "error": error}


def _cryptography_diagnostic(signer_info, certificate, signed_content: bytes) -> dict:
    """Independent diagnostic only; production validation remains in pyHanko."""
    digest_name = signer_info["digest_algorithm"]["algorithm"].native.replace("-", "").lower()
    hash_types = {
        "sha256": hashes.SHA256,
        "sha384": hashes.SHA384,
        "sha512": hashes.SHA512,
    }
    hash_type = hash_types.get(digest_name)
    if hash_type is None:
        return {"cms_signature_valid": None, "error": "unsupported_digest_algorithm"}
    signed_attrs = signer_info["signed_attrs"]
    signed_material = signed_attrs.untag().dump() if signed_attrs.native is not None else signed_content
    signature_value = signer_info["signature"].native
    signature_algorithm = signer_info["signature_algorithm"]["algorithm"].native
    public_key = crypto_x509.load_der_x509_certificate(certificate.dump()).public_key()
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            if signature_algorithm == "rsassa_pss":
                parameters = signer_info["signature_algorithm"]["parameters"]
                salt_length = parameters["salt_length"].native
                public_key.verify(
                    signature_value,
                    signed_material,
                    padding.PSS(mgf=padding.MGF1(hash_type()), salt_length=salt_length),
                    hash_type(),
                )
            else:
                public_key.verify(signature_value, signed_material, padding.PKCS1v15(), hash_type())
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature_value, signed_material, ec.ECDSA(hash_type()))
        else:
            return {"cms_signature_valid": None, "error": "unsupported_public_key_algorithm"}
    except InvalidSignature:
        return {"cms_signature_valid": False, "error": "bad_signature"}
    except (KeyError, TypeError, ValueError):
        return {"cms_signature_valid": None, "error": "diagnostic_verification_error"}
    return {"cms_signature_valid": True, "error": None}


def diagnose_pdf_signature(
    pdf_path: Path,
    *,
    original_path: Path | None = None,
    use_openssl: bool = True,
    allowed_signatures: list[str] | None = None,
) -> dict:
    pdf_bytes = pdf_path.read_bytes()
    app_result = verify_signed_pdf(pdf_path)
    app_result.update(resolve_signature_policy(app_result, allowed_signatures))
    signatures = []
    with pdf_path.open("rb") as stream:
        embedded_signatures = list(PdfFileReader(stream).embedded_signatures)
        for index, embedded in enumerate(embedded_signatures):
            signer_info = embedded.signer_info
            byte_range = [int(value) for value in embedded.sig_object["/ByteRange"]]
            signed_content = b"".join(
                pdf_bytes[start:start + length]
                for start, length in zip(byte_range[::2], byte_range[1::2])
            )
            digest_algorithm = signer_info["digest_algorithm"]["algorithm"].native
            message_digest = _signed_attribute(signer_info, "message_digest")
            calculated_digest = _hash(digest_algorithm, signed_content)
            cms_der = cms.ContentInfo.load(bytes(embedded.pkcs7_content)).dump()
            certificate_diagnostics = extract_pkcs7_certificate_diagnostics(
                bytes(embedded.pkcs7_content), signer_info
            )
            signer_certificate = embedded.signer_cert
            verifier_item = app_result.get("signatures", [])[index]
            signatures.append({
                "index": index,
                "subfilter": str(embedded.sig_object.get("/SubFilter") or "").lstrip("/"),
                "byte_range": byte_range,
                "byte_range_digest_matches_message_digest": (
                    message_digest is not None and calculated_digest == message_digest.native
                ),
                "digest_algorithm": digest_algorithm,
                "signature_algorithm": signer_info["signature_algorithm"]["algorithm"].native,
                "signed_attributes_present": signer_info["signed_attrs"].native is not None,
                "signer_info_sid_type": signer_info["sid"].name,
                "sid_matches_signer_certificate": _sid_matches_certificate(signer_info, signer_certificate),
                "signing_certificate_v2_matches_signer_certificate": _signing_certificate_v2_matches(
                    signer_info, signer_certificate
                ),
                "public_key_source_matches_signer_certificate": _sid_matches_certificate(signer_info, signer_certificate),
                "certificate_diagnostics": certificate_diagnostics,
                "pyhanko": {
                    "intact": verifier_item.get("integrity_ok"),
                    "valid": verifier_item.get("cryptographically_valid"),
                    "trusted": verifier_item.get("trusted"),
                    "bottom_line": verifier_item.get("bottom_line"),
                    "trust_problem_indic": verifier_item.get("trust_problem_indic"),
                },
                "cryptography": _cryptography_diagnostic(signer_info, signer_certificate, signed_content),
                "openssl": (
                    _openssl_diagnostic(cms_der, signed_content)
                    if use_openssl else
                    {"available": None, "cms_signature_valid": None, "error": "not_requested"}
                ),
            })

    original_comparison = None
    if original_path is not None:
        original_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()
        uploaded_hash = hashlib.sha256(pdf_bytes).hexdigest()
        original_comparison = {
            "original_pdf_sha256": original_hash,
            "uploaded_pdf_sha256": uploaded_hash,
            "identical": original_hash == uploaded_hash,
        }

    crypto_invalid = any(
        item["pyhanko"]["intact"] is True and item["pyhanko"]["valid"] is False
        for item in signatures
    )
    return {
        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "signature_count": len(signatures),
        "detected_signature_type": app_result.get("detected_signature_type"),
        "provider": app_result.get("provider"),
        "signature_type_allowed": app_result.get("signature_type_allowed"),
        "provider_allowed": app_result.get("provider_allowed"),
        "reason_code": app_result.get("reason_code"),
        "signatures": signatures,
        "original_comparison": original_comparison,
        "conclusion": "CMS_SIGNATURE_VALUE_INVALID" if crypto_invalid else app_result.get("reason_code"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--original", type=Path, help="Original file downloaded directly from the signing service")
    parser.add_argument("--allowed-signature", action="append", dest="allowed_signatures")
    parser.add_argument("--no-openssl", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        diagnose_pdf_signature(
            args.pdf,
            original_path=args.original,
            use_openssl=not args.no_openssl,
            allowed_signatures=args.allowed_signatures,
        ),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
