from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

from asn1crypto import cms
from pypdf import PdfReader
from cryptography.hazmat.primitives import hashes
from cryptography import x509 as crypto_x509
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext


ALLOWED_SIGNATURE_TYPES = {"mszafir", "profil_zaufany"}

SZAFIR_ISSUER_HINTS = [
    "KRAJOWA IZBA ROZLICZENIOWA",
    "COPE SZAFIR",
    "SZAFIR - KWALIFIKOWANY",
    "SZAFIR",
    "MSZAFIR",
    "KIR",
]

TRUSTED_PROFILE_HINTS = [
    "PROFIL ZAUFANY",
    "PODPIS ZAUFANY",
    "PODPIS OSOBISTY",
    "EPUAP",
    "MINISTER CYFRYZACJI",
    "MINISTER WŁAŚCIWY DO SPRAW INFORMATYZACJI",
    "MINISTRA WŁAŚCIWEGO DO SPRAW INFORMATYZACJI",
    "MINISTRY OF DIGITAL AFFAIRS",
    "CENTRALNY OSRODEK INFORMATYKI",
    "CENTRALNY OŚRODEK INFORMATYKI",
    "COI",
    "PWPW",
    "PIECZĘCIĄ MINISTRA",
    "PIECZECIA MINISTRA",
    "PZ ID",
]

QUALIFIED_PROVIDER_HINTS = [
    "KRAJOWA IZBA ROZLICZENIOWA",
    "COPE SZAFIR",
    "SZAFIR",
    "MSZAFIR",
    "KIR",
    "CENCERT",
    "ENIGMA",
    "SIMPLYSIGN",
    "ASSECO",
    "SIGILLUM",
    "DOPODPISU",
    "EUROCERT",
]


def _normalize_name_dict(name_dict: dict | None) -> str:
    if not name_dict:
        return ""

    parts = []
    for _, value in name_dict.items():
        if value:
            parts.append(str(value))

    return " | ".join(parts)


def _normalize_pdf_value(value: Any) -> str:
    if value is None:
        return ""

    try:
        return str(value)
    except Exception:
        return ""


def _match_any(text: str, hints: list[str]) -> bool:
    text_upper = text.upper()
    return any(hint in text_upper for hint in hints)


def _extract_pdf_signature(pdf_path: Path) -> dict[str, Any] | None:
    reader = PdfReader(str(pdf_path))
    root = reader.trailer["/Root"]

    if "/AcroForm" not in root:
        return None

    acro_form = root["/AcroForm"]
    fields = acro_form.get("/Fields", [])

    for field_ref in fields:
        field = field_ref.get_object()

        if field.get("/FT") != "/Sig":
            continue

        signature_dict = field.get("/V")
        if not signature_dict:
            continue

        contents = signature_dict.get("/Contents")
        if not contents:
            continue

        byte_range = signature_dict.get("/ByteRange")

        return {
            "contents": bytes(contents),
            "name": _normalize_pdf_value(signature_dict.get("/Name")),
            "reason": _normalize_pdf_value(signature_dict.get("/Reason")),
            "date": _normalize_pdf_value(signature_dict.get("/M")),
            "filter": _normalize_pdf_value(signature_dict.get("/Filter")),
            "subfilter": _normalize_pdf_value(signature_dict.get("/SubFilter")),
            "byte_range": list(byte_range) if byte_range else None,
        }

    return None


def _extract_pdf_signature_contents(pdf_path: Path) -> bytes | None:
    signature = _extract_pdf_signature(pdf_path)
    if not signature:
        return None

    return signature.get("contents")


def _build_default_result() -> dict:
    return {
        "validation_status": "UNSIGNED",
        "validated_at": None,
        "signature_count": 0,
        "signatures": [],
        "cryptographically_valid": False,
        "integrity_ok": False,
        "trusted": False,
        "revocation_checked": False,
        "is_signed": False,
        "is_valid_structure": False,
        "likely_mobywatel": False,
        "provider": None,
        "signature_type": "unknown",
        "is_allowed_signature": False,
        "is_szafir_signature": False,
        "is_trusted_profile_signature": False,
        "signer_subject": None,
        "signer_issuer": None,
        "pdf_signature_name": None,
        "pdf_signature_reason": None,
        "pdf_signature_date": None,
        "pdf_signature_subfilter": None,
        "reason": None,
    }


def _classify_signature(combined_text: str) -> dict[str, Any]:
    if _match_any(combined_text, SZAFIR_ISSUER_HINTS):
        return {
            "provider": "szafir",
            "signature_type": "mszafir",
            "is_allowed_signature": True,
            "is_szafir_signature": True,
            "is_trusted_profile_signature": False,
            "likely_mobywatel": True,
            "reason": "Wykryto podpis mSzafir / Szafir / KIR.",
        }

    if _match_any(combined_text, TRUSTED_PROFILE_HINTS):
        return {
            "provider": "profil_zaufany",
            "signature_type": "profil_zaufany",
            "is_allowed_signature": True,
            "is_szafir_signature": False,
            "is_trusted_profile_signature": True,
            "likely_mobywatel": True,
            "reason": "Wykryto podpis Profilem Zaufanym.",
        }

    if _match_any(combined_text, QUALIFIED_PROVIDER_HINTS):
        return {
            "provider": "qualified-provider",
            "signature_type": "unsupported",
            "is_allowed_signature": False,
            "is_szafir_signature": False,
            "is_trusted_profile_signature": False,
            "likely_mobywatel": True,
            "reason": "Wykryto podpis od dostawcy kwalifikowanego, ale nie rozpoznano go jako mSzafir ani Profil Zaufany.",
        }

    return {
        "provider": "other",
        "signature_type": "unsupported",
        "is_allowed_signature": False,
        "is_szafir_signature": False,
        "is_trusted_profile_signature": False,
        "likely_mobywatel": False,
        "reason": "Wykryto podpis, ale nie jest to dopuszczalny podpis mSzafir ani Profil Zaufany.",
    }


def verify_signed_pdf(pdf_path: Path) -> dict:
    result = _build_default_result()
    from datetime import datetime, timezone

    result["validated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        structural_signature = _extract_pdf_signature(Path(pdf_path))
    except Exception as exc:
        result.update(validation_status="INVALID", reason=f"Nie udało się odczytać PDF: {exc.__class__.__name__}.")
        return result
    if not structural_signature:
        result["reason"] = "Brak pola podpisu PDF."
        return result
    result["pdf_signature_name"] = structural_signature.get("name") or None
    result["pdf_signature_reason"] = structural_signature.get("reason") or None
    result["pdf_signature_subfilter"] = structural_signature.get("subfilter") or None
    try:
        with Path(pdf_path).open("rb") as stream:
            reader = PdfFileReader(stream)
            embedded = list(reader.embedded_signatures)
            result["signature_count"] = len(embedded)
            if not embedded:
                result["reason"] = "Brak kryptograficznego podpisu PDF/PAdES."
                return result
            validation_context = ValidationContext(allow_fetching=False, revocation_mode="soft-fail")
            signature_results = []
            for signature in embedded:
                cert = signature.signer_cert
                subject_text = _normalize_name_dict(cert.subject.native if cert else {})
                issuer_text = _normalize_name_dict(cert.issuer.native if cert else {})
                fingerprint = ""
                serial = ""
                not_before = None
                not_after = None
                if cert:
                    serial = str(cert.serial_number)
                    crypto_cert = crypto_x509.load_der_x509_certificate(cert.dump())
                    fingerprint = crypto_cert.fingerprint(hashes.SHA256()).hex()
                    not_before = crypto_cert.not_valid_before_utc.isoformat()
                    not_after = crypto_cert.not_valid_after_utc.isoformat()
                try:
                    status = validate_pdf_signature(signature, signer_validation_context=validation_context)
                    intact = bool(status.intact)
                    valid = bool(status.valid)
                    trusted = bool(status.trusted)
                    revoked = bool(status.revoked)
                    docmdp_ok = status.docmdp_ok is not False
                    revocation_checked = status.revocation_details is not None
                    error = str(status.trust_problem_indic or "")
                    signing_time = status.signer_reported_dt.isoformat() if status.signer_reported_dt else None
                    qualification = str(status.qualification_result or "")
                    coverage = str(status.coverage or "")
                    modification_level = str(status.modification_level or "")
                except Exception as exc:
                    intact = valid = trusted = revocation_checked = False
                    revoked = False
                    docmdp_ok = False
                    error = f"{exc.__class__.__name__}: {exc}"
                    signing_time = None
                    qualification = coverage = modification_level = ""
                signature_results.append({
                    "field_name": str(signature.field_name or ""), "integrity_ok": intact,
                    "cryptographically_valid": valid, "trusted": trusted, "revoked": revoked,
                    "revocation_checked": revocation_checked, "docmdp_ok": docmdp_ok,
                    "signer_subject": subject_text, "signer_issuer": issuer_text,
                    "certificate_serial": serial, "certificate_sha256": fingerprint,
                    "certificate_not_before": not_before, "certificate_not_after": not_after,
                    "signing_time": signing_time, "qualification": qualification,
                    "coverage": coverage, "modification_level": modification_level, "warning": error,
                })
    except Exception as exc:
        result.update(validation_status="INVALID", reason=f"Nie udało się odczytać podpisów PDF: {exc.__class__.__name__}.")
        return result

    result["signatures"] = signature_results
    result["is_signed"] = True
    result["is_valid_structure"] = True
    result["integrity_ok"] = all(item["integrity_ok"] and item["docmdp_ok"] for item in signature_results)
    result["cryptographically_valid"] = all(item["cryptographically_valid"] for item in signature_results)
    result["trusted"] = all(item["trusted"] for item in signature_results)
    result["revocation_checked"] = all(item["revocation_checked"] for item in signature_results)
    result["signer_subject"] = signature_results[0]["signer_subject"] or None
    result["signer_issuer"] = signature_results[0]["signer_issuer"] or None
    result["pdf_signature_date"] = signature_results[0]["signing_time"]
    result.update(_classify_signature(f"{result['signer_subject']} || {result['signer_issuer']}"))
    if not result["integrity_ok"] or not result["cryptographically_valid"] or any(item["revoked"] for item in signature_results):
        result["validation_status"] = "INVALID"
        result["reason"] = "Podpis jest niepoprawny kryptograficznie albo dokument został zmieniony po podpisaniu."
    elif not result["trusted"] or not result["revocation_checked"]:
        result["validation_status"] = "INDETERMINATE"
        result["reason"] = "Integralność i kryptografia są poprawne, ale offline nie potwierdzono pełnego zaufania lub statusu unieważnienia."
    else:
        result["validation_status"] = "VALID"
        result["reason"] = "Podpis i integralność dokumentu zostały zweryfikowane."
    return result


def verify_signed_pdf_bytes(pdf_bytes: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        path = Path(handle.name)
        handle.write(pdf_bytes)
    try:
        return verify_signed_pdf(path)
    finally:
        path.unlink(missing_ok=True)
