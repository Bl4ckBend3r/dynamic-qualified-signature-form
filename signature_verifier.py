from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import os
import tempfile
from typing import Any

from pypdf import PdfReader
from cryptography.hazmat.primitives import hashes
from cryptography import x509 as crypto_x509
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext

from services.signatures.trusted_profile import (
    TrustedProfileMinimalBackend,
    inspect_pdf_cms_signer_names,
)


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent


class SignatureTrustConfigurationError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SignatureTrustConfiguration:
    trust_roots: tuple[Any, ...]
    intermediate_certs: tuple[Any, ...]
    crls: tuple[Any, ...]
    allow_fetching: bool

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "trust_roots_loaded_count": len(self.trust_roots),
            "intermediate_certs_loaded_count": len(self.intermediate_certs),
            "allow_fetching": self.allow_fetching,
        }


ALLOWED_SIGNATURE_TYPES = {"mszafir", "profil_zaufany", "qualified", "qualified_other"}
SUPPORTED_SUBFILTERS = {
    "/adbe.pkcs7.detached",
    "/ETSI.CAdES.detached",
    "/adbe.pkcs7.sha1",
}

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
    "PIECZĘĆ PODPISU ZAUFANEGO",
    "PIECZEC PODPISU ZAUFANEGO",
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


def _certificate_has_revocation_pointer(certificate: crypto_x509.Certificate) -> bool:
    from cryptography.x509.oid import AuthorityInformationAccessOID

    try:
        certificate.extensions.get_extension_for_class(
            crypto_x509.CRLDistributionPoints
        )
        return True
    except crypto_x509.ExtensionNotFound:
        pass
    try:
        access = certificate.extensions.get_extension_for_class(
            crypto_x509.AuthorityInformationAccess
        ).value
        return any(
            item.access_method == AuthorityInformationAccessOID.OCSP for item in access
        )
    except crypto_x509.ExtensionNotFound:
        return False


def _extract_pdf_signature(pdf_path: Path) -> dict[str, Any] | None:
    reader = PdfReader(str(pdf_path))
    root = reader.trailer["/Root"]

    if "/AcroForm" not in root:
        return None

    acro_form = root["/AcroForm"]
    fields = acro_form.get("/Fields", [])

    pending = [(field_ref, None) for field_ref in fields]
    visited = set()
    while pending:
        field_ref, inherited_type = pending.pop(0)
        field = field_ref.get_object()
        if id(field) in visited:
            continue
        visited.add(id(field))
        field_type = field.get("/FT", inherited_type)
        pending.extend((child, field_type) for child in field.get("/Kids", []))
        if field_type != "/Sig":
            continue

        signature_dict = field.get("/V")
        if not signature_dict:
            continue
        signature_dict = signature_dict.get_object()

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
        "reason_code": "NO_SIGNATURE",
        "detected_signature_type": "unknown",
        "verifier_backend": "pyhanko_pdf_cms",
        "provider_allowed": None,
        "signature_type_allowed": None,
        "cryptographic_valid": None,
        "document_integrity_valid": None,
        "certificate_chain_valid": None,
        "timestamp_valid": None,
        "timestamp_status": "NOT_PRESENT",
        "trust_source": "configured_document_signing_roots",
        "trust_roots_loaded_count": 0,
        "intermediate_certs_loaded_count": 0,
        "signer_certificate_found": False,
        "chain_built": False,
        "chain_valid": None,
        "revocation_status": "NOT_CHECKED",
        "allow_fetching": False,
        "eutl_enabled": False,
        "result": "NO_SIGNATURE",
    }


def _classify_signature(combined_text: str) -> dict[str, Any]:
    text_upper = combined_text.upper()
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
            "provider": "eurocert" if "EUROCERT" in text_upper else "profil_zaufany",
            "signature_type": "profil_zaufany",
            "is_allowed_signature": True,
            "is_szafir_signature": False,
            "is_trusted_profile_signature": True,
            "likely_mobywatel": True,
            "reason": "Wykryto podpis Profilem Zaufanym.",
        }

    if _match_any(combined_text, QUALIFIED_PROVIDER_HINTS):
        return {
            "provider": "eurocert" if "EUROCERT" in text_upper else "qualified-provider",
            "signature_type": "qualified_other",
            "is_allowed_signature": True,
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


def _configured_paths(variable_name: str) -> list[Path]:
    paths = []
    for value in os.environ.get(variable_name, "").split(";"):
        value = value.strip()
        if not value:
            continue
        path = Path(value)
        paths.append(path if path.is_absolute() else PROJECT_ROOT / path)
    return paths


def _env_bool(variable_name: str, default: bool = False) -> bool:
    value = os.environ.get(variable_name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "tak", "on"}


def load_x509_certificate(path: str | Path) -> crypto_x509.Certificate:
    """Load one PEM or DER certificate and classify configuration failures."""
    resolved = Path(path)
    if not resolved.exists() or not resolved.is_file():
        raise SignatureTrustConfigurationError(
            "TRUST_CERTIFICATE_FILE_NOT_FOUND",
            f"Configured signature certificate file does not exist: {resolved}",
        )
    try:
        data = resolved.read_bytes()
        if b"-----BEGIN CERTIFICATE-----" in data:
            return crypto_x509.load_pem_x509_certificate(data)
        return crypto_x509.load_der_x509_certificate(data)
    except SignatureTrustConfigurationError:
        raise
    except Exception as exc:
        raise SignatureTrustConfigurationError(
            "TRUST_CERTIFICATE_PARSE_ERROR",
            f"Configured signature certificate cannot be parsed: {resolved}",
        ) from exc


def _require_ca(certificate: crypto_x509.Certificate, *, role: str, path: Path) -> None:
    try:
        basic_constraints = certificate.extensions.get_extension_for_class(
            crypto_x509.BasicConstraints
        ).value
    except crypto_x509.ExtensionNotFound as exc:
        raise SignatureTrustConfigurationError(
            "INVALID_TRUST_CONFIGURATION",
            f"Configured {role} certificate has no BasicConstraints extension: {path}",
        ) from exc
    if not basic_constraints.ca:
        raise SignatureTrustConfigurationError(
            "INVALID_TRUST_CONFIGURATION",
            f"Configured {role} certificate is not a CA certificate: {path}",
        )
    if role == "trust root" and certificate.subject != certificate.issuer:
        raise SignatureTrustConfigurationError(
            "INVALID_TRUST_CONFIGURATION",
            f"Configured trust root is an issuing/intermediate certificate: {path}",
        )


def _load_crl(path: Path):
    from asn1crypto import crl, pem

    if not path.exists() or not path.is_file():
        raise SignatureTrustConfigurationError(
            "TRUST_CERTIFICATE_FILE_NOT_FOUND",
            f"Configured signature CRL file does not exist: {path}",
        )
    try:
        data = path.read_bytes()
        if pem.detect(data):
            _, _, data = pem.unarmor(data)
        return crl.CertificateList.load(data)
    except Exception as exc:
        raise SignatureTrustConfigurationError(
            "TRUST_CERTIFICATE_PARSE_ERROR",
            f"Configured signature CRL cannot be parsed: {path}",
        ) from exc


def load_signature_trust_configuration() -> SignatureTrustConfiguration:
    from asn1crypto import x509 as asn1_x509
    from cryptography.hazmat.primitives import serialization

    root_paths = _configured_paths("SIGNATURE_TRUST_ROOTS")
    if not root_paths:
        raise SignatureTrustConfigurationError(
            "CERTIFICATE_TRUST_CONFIGURATION_MISSING",
            "SIGNATURE_TRUST_ROOTS does not contain a trust anchor.",
        )
    intermediate_paths = _configured_paths("SIGNATURE_INTERMEDIATE_CERTS")

    def load_for_pyhanko(path: Path, role: str):
        certificate = load_x509_certificate(path)
        _require_ca(certificate, role=role, path=path)
        return asn1_x509.Certificate.load(
            certificate.public_bytes(serialization.Encoding.DER)
        )

    roots = tuple(load_for_pyhanko(path, "trust root") for path in root_paths)
    intermediates = tuple(
        load_for_pyhanko(path, "intermediate") for path in intermediate_paths
    )
    crls = tuple(_load_crl(path) for path in _configured_paths("SIGNATURE_CRL_FILES"))
    return SignatureTrustConfiguration(
        trust_roots=roots,
        intermediate_certs=intermediates,
        crls=crls,
        allow_fetching=_env_bool("SIGNATURE_ALLOW_FETCHING", default=False),
    )


def build_signature_validation_context(
    configuration: SignatureTrustConfiguration | None = None,
) -> ValidationContext:
    configuration = configuration or load_signature_trust_configuration()
    # trust_roots is always explicit, so pyHanko cannot silently use OS TLS roots.
    context = ValidationContext(
        trust_roots=list(configuration.trust_roots),
        other_certs=list(configuration.intermediate_certs),
        allow_fetching=configuration.allow_fetching,
        revocation_mode="hard-fail",
        crls=list(configuration.crls),
    )
    context._signature_trust_diagnostics = configuration.diagnostics
    return context


def _validation_context():
    return build_signature_validation_context()


def check_signature_trust_configuration(log=None) -> dict[str, Any]:
    """Run a lightweight, fail-safe startup check without exposing certificate DNs."""
    target_log = log or logger
    try:
        configuration = load_signature_trust_configuration()
        build_signature_validation_context(configuration)
    except SignatureTrustConfigurationError as exc:
        target_log.warning(
            "signature_trust_configuration_invalid reason_code=%s",
            exc.reason_code,
            extra={
                "event": "signature_trust_configuration_invalid",
                "operation": "signature_trust_configuration",
                "reason_code": exc.reason_code,
            },
        )
        return {"ok": False, "reason_code": exc.reason_code}
    diagnostics = configuration.diagnostics
    target_log.info(
        "signature_trust_configuration_loaded trust_roots=%d intermediates=%d allow_fetching=%s",
        diagnostics["trust_roots_loaded_count"],
        diagnostics["intermediate_certs_loaded_count"],
        diagnostics["allow_fetching"],
        extra={
            "event": "signature_trust_configuration_loaded",
            "operation": "signature_trust_configuration",
            **diagnostics,
        },
    )
    return {"ok": True, **diagnostics}


def _qualification_signature_type(status, heuristic_type: str) -> str:
    """Prefer pyHanko's qualified-signature assessment over issuer hints."""
    qualification = getattr(status, "qualification_result", None)
    if qualification is not None:
        text = str(qualification).upper()
        if "QES" in text or "QUALIFIED" in text:
            return "qualified"
    return heuristic_type


def _policy_kind_allowed(kind: str, allowed_signatures: set[str]) -> bool:
    if kind in allowed_signatures:
        return True
    return kind in {"mszafir", "qualified_other", "qualified"} and "qualified" in allowed_signatures


def _signature_technical_passed(item: dict) -> bool:
    return (
        item.get("integrity_ok") is True
        and item.get("cryptographically_valid") is True
        and item.get("trusted") is True
        and item.get("revoked") is not True
        and item.get("docmdp_ok") is not False
        and item.get("timestamp_invalid") is not True
        and item.get("timestamp_status", "NOT_PRESENT") in {"VALID", "NOT_PRESENT"}
        and item.get("verification_error") is not True
    )


def resolve_signature_policy(verification, allowed_signatures=None):
    """Resolve admission policy independently of the selected verifier backend."""
    kind = verification.get("signature_type")
    if not kind:
        kind = "mszafir" if verification.get("is_szafir_signature") else "profil_zaufany" if verification.get("is_trusted_profile_signature") else "unknown"
    detected = kind
    signatures = verification.get("signatures") or []
    kinds = [item.get("signature_type", "unknown") for item in signatures] or [kind]
    allowed_set = set(allowed_signatures or [])
    for item in signatures:
        item["policy_allowed"] = None if allowed_signatures is None else _policy_kind_allowed(
            item.get("signature_type", "unknown"), allowed_set
        )
    candidates = [item for item in signatures if _signature_technical_passed(item)]
    evaluated_kinds = [item.get("signature_type", "unknown") for item in candidates] or kinds
    policy_allowed = None if allowed_signatures is None else any(
        value in ALLOWED_SIGNATURE_TYPES and _policy_kind_allowed(value, allowed_set)
        for value in evaluated_kinds
    )
    return {
        "detected_signature_type": detected,
        "verifier_backend": verification.get("verifier_backend") or "pyhanko_pdf_cms",
        "signature_type_allowed": policy_allowed,
        # Compatibility alias used by existing audit logs.
        "provider_allowed": policy_allowed,
    }


def signature_verification_result(verification, allowed_signatures=None):
    """Classify the verifier result separately from transport and certificate availability."""
    status = str(verification.get("validation_status") or "").upper()
    if status in {"ERROR", "INDETERMINATE"} or verification.get("verification_error"):
        return "VERIFICATION_ERROR"
    if status == "UNSUPPORTED":
        return "UNSUPPORTED_SIGNATURE"
    if status == "UNSIGNED":
        return "NO_SIGNATURE"
    if status == "INVALID":
        return "INVALID_SIGNATURE"
    if status in {"UNTRUSTED", "INDETERMINATE"}:
        return "VERIFICATION_ERROR"
    if verification.get("is_signed") is not True:
        return "VERIFICATION_ERROR"
    signatures = verification.get("signatures") or []
    if not signatures and verification.get("cryptographically_valid") is True and verification.get("integrity_ok") is True:
        signatures = [{
            "integrity_ok": True,
            "cryptographically_valid": True,
            "trusted": verification.get("trusted", True),
            "revoked": verification.get("revoked", False),
            "docmdp_ok": verification.get("docmdp_ok", True),
            "timestamp_invalid": False,
            "verification_error": False,
            "signature_type": verification.get("signature_type", "unknown"),
        }]
    technically_valid = [item for item in signatures if _signature_technical_passed(item)]
    if technically_valid:
        policy = resolve_signature_policy(verification, allowed_signatures)
        return "SIGNATURE_TYPE_NOT_ALLOWED" if policy["provider_allowed"] is False else "VALID_SIGNATURE"
    reason_code = str(verification.get("reason_code") or "")
    if status == "INVALID" or reason_code in {
        "DOCUMENT_INTEGRITY_FAILURE", "DOCUMENT_DIGEST_MISMATCH",
        "DOCUMENT_MODIFICATION_NOT_ALLOWED", "SIGNATURE_CRYPTOGRAPHIC_FAILURE",
        "CERTIFICATE_REVOKED", "CERTIFICATE_EXPIRED", "TIMESTAMP_CRYPTOGRAPHIC_FAILURE",
    }:
        return "INVALID_SIGNATURE"
    if reason_code in {
        "CERTIFICATE_TRUST_FAILURE", "REVOCATION_STATUS_UNKNOWN", "REVOCATION_CHECK_ERROR",
        "TRUST_STATUS_INDETERMINATE",
    }:
        return "VERIFICATION_ERROR"
    return "VERIFICATION_ERROR"


def _verify_signed_pdf_with_pyhanko(pdf_path: Path) -> dict:
    result = _build_default_result()
    from datetime import datetime, timezone

    result["validated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        structural_signature = _extract_pdf_signature(Path(pdf_path))
    except Exception as exc:
        result.update(validation_status="ERROR", reason_code="PDF_PARSE_ERROR", reason=f"Nie udało się odczytać PDF: {exc.__class__.__name__}.")
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
            result["is_signed"] = True
            result["is_valid_structure"] = True
            try:
                validation_context = _validation_context()
                result.update(
                    getattr(validation_context, "_signature_trust_diagnostics", {})
                )
            except SignatureTrustConfigurationError as exc:
                result.update(
                    validation_status="ERROR",
                    reason_code=exc.reason_code,
                    reason="Konfiguracja zaufania podpisów PDF jest niepełna lub nieprawidłowa.",
                )
                return result
            except Exception as exc:
                result.update(validation_status="ERROR", reason_code="TRUST_CONTEXT_ERROR",
                    reason=f"Nie udało się przygotować magazynu zaufania: {exc.__class__.__name__}.")
                return result
            signature_results = []
            for signature in embedded:
                subfilter = str(signature.sig_object.get("/SubFilter") or "")
                status = None
                cert = signature.signer_cert
                subject_text = _normalize_name_dict(cert.subject.native if cert else {})
                issuer_text = _normalize_name_dict(cert.issuer.native if cert else {})
                classification = _classify_signature(f"{subject_text} || {issuer_text}")
                fingerprint = ""
                serial = ""
                not_before = None
                not_after = None
                has_revocation_pointer = False
                if cert:
                    serial = str(cert.serial_number)
                    crypto_cert = crypto_x509.load_der_x509_certificate(cert.dump())
                    fingerprint = crypto_cert.fingerprint(hashes.SHA256()).hex()
                    not_before = crypto_cert.not_valid_before_utc.isoformat()
                    not_after = crypto_cert.not_valid_after_utc.isoformat()
                    has_revocation_pointer = _certificate_has_revocation_pointer(crypto_cert)
                try:
                    if subfilter not in SUPPORTED_SUBFILTERS:
                        signature_results.append({
                            "field_name": str(signature.field_name or ""),
                            "subfilter": subfilter,
                            "integrity_ok": None,
                            "cryptographically_valid": None,
                            "trusted": None,
                            "revoked": None,
                            "revocation_checked": False,
                            "docmdp_ok": None,
                            "signer_subject": subject_text,
                            "signer_issuer": issuer_text,
                            "certificate_serial": serial,
                            "certificate_sha256": fingerprint,
                            "certificate_not_before": not_before,
                            "certificate_not_after": not_after,
                            "signing_time": None,
                            "qualification": "",
                            "coverage": "",
                            "modification_level": "",
                            "warning": "unsupported_subfilter",
                            "accepted": False,
                            "verification_error": False,
                            "unsupported_format": True,
                            "provider": classification["provider"],
                            "signature_type": classification["signature_type"],
                            "certificate_chain_valid": None,
                            "signer_certificate_found": cert is not None,
                            "chain_built": False,
                            "chain_valid": None,
                            "revocation_status": "NOT_CHECKED",
                            "timestamp_valid": None,
                            "timestamp_status": "NOT_PRESENT",
                            "timestamp_invalid": False,
                            "pkcs7_signature_mechanism": None,
                            "md_algorithm": None,
                            "trust_problem_indic": None,
                            "ades_subindication": "FORMAT_FAILURE",
                            "bottom_line": False,
                            "signed_attrs_present": None,
                            "message_digest_matches": None,
                        })
                        continue
                    status = validate_pdf_signature(signature, signer_validation_context=validation_context)
                    logger.info(
                                    "pyhanko_signature_status "
                                    "intact=%s valid=%s trusted=%s bottom_line=%s",
                                    getattr(status, "intact", None),
                                    getattr(status, "valid", None),
                                    getattr(status, "trusted", None),
                                    getattr(status, "bottom_line", None),
                                )

                    intact = bool(status.intact)
                    valid = bool(status.valid)
                    trusted = bool(status.trusted)
                    revoked = bool(status.revoked)
                    docmdp_ok = status.docmdp_ok is not False
                    # revocation_details only describes a revoked certificate. With hard-fail,
                    # a trusted result plus a signer revocation pointer is positive evidence
                    # that pyHanko obtained a conclusive OCSP/CRL result.
                    revocation_checked = revoked or (trusted and has_revocation_pointer)
                    revocation_status = (
                        "REVOKED" if revoked else "VALID" if revocation_checked else "NOT_CHECKED"
                    )
                    accepted = bool(status.bottom_line)
                    verification_error = False
                    trust_problem = getattr(status.trust_problem_indic, "name", None)
                    error = trust_problem or ""
                    signing_time = status.signer_reported_dt.isoformat() if status.signer_reported_dt else None
                    qualification = str(status.qualification_result or "")
                    coverage = str(status.coverage or "")
                    modification_level = str(status.modification_level or "")
                    chain_built = status.validation_path is not None
                    chain_valid = bool(trusted)
                    timestamps = [value for value in (status.timestamp_validity, status.content_timestamp_validity) if value is not None]
                    timestamp_valid = all(value.valid and value.intact and value.trusted for value in timestamps) if timestamps else None
                    timestamp_invalid = any(not value.valid or not value.intact for value in timestamps)
                    timestamp_status = "VALID" if timestamp_valid else "INVALID" if timestamp_invalid else "INDETERMINATE" if timestamps else "NOT_PRESENT"
                    signer_info = signature.signer_info
                    signed_attrs = signer_info["signed_attrs"]
                    signed_attrs_present = signed_attrs.native is not None
                    message_digest_matches = intact if signed_attrs_present else None
                    signature_type = _qualification_signature_type(status, classification["signature_type"])
                except Exception as exc:
                    intact = valid = trusted = revocation_checked = False
                    revoked = False
                    docmdp_ok = False
                    error = exc.__class__.__name__
                    verification_error = True
                    accepted = False
                    signing_time = None
                    qualification = coverage = modification_level = ""
                    chain_valid = timestamp_valid = None
                    timestamp_invalid = False
                    timestamp_status = "NOT_PRESENT"
                    trust_problem = None
                    chain_built = False
                    revocation_status = "CHECK_ERROR"
                    signed_attrs_present = message_digest_matches = None
                    signature_type = classification["signature_type"]
                signature_results.append({
                    "field_name": str(signature.field_name or ""), "integrity_ok": intact,
                    "subfilter": subfilter,
                    "cryptographically_valid": valid, "trusted": trusted, "revoked": revoked,
                    "revocation_checked": revocation_checked, "docmdp_ok": docmdp_ok,
                    "signer_subject": subject_text, "signer_issuer": issuer_text,
                    "certificate_serial": serial, "certificate_sha256": fingerprint,
                    "certificate_not_before": not_before, "certificate_not_after": not_after,
                    "signing_time": signing_time, "qualification": qualification,
                    "coverage": coverage, "modification_level": modification_level, "warning": error,
                    "accepted": accepted, "verification_error": verification_error,
                    "provider": classification["provider"], "signature_type": signature_type,
                    "certificate_chain_valid": chain_valid, "timestamp_valid": timestamp_valid,
                    "signer_certificate_found": cert is not None,
                    "chain_built": chain_built,
                    "chain_valid": chain_valid,
                    "revocation_status": revocation_status,
                    "timestamp_status": timestamp_status, "timestamp_invalid": timestamp_invalid,
                    "pkcs7_signature_mechanism": getattr(status, "pkcs7_signature_mechanism", None),
                    "md_algorithm": getattr(status, "md_algorithm", None),
                    "trust_problem_indic": trust_problem,
                    "ades_subindication": trust_problem,
                    "bottom_line": accepted,
                    "signed_attrs_present": signed_attrs_present,
                    "message_digest_matches": message_digest_matches,
                    "unsupported_format": False,
                })
    except Exception as exc:
        result.update(validation_status="ERROR", reason_code="SIGNATURE_PROCESSING_ERROR", reason=f"Nie udało się odczytać podpisów PDF: {exc.__class__.__name__}.")
        return result

    result["signatures"] = signature_results
    result["is_signed"] = True
    result["is_valid_structure"] = True
    supported_results = [item for item in signature_results if not item["unsupported_format"]]
    result["integrity_ok"] = any(item["integrity_ok"] and item["docmdp_ok"] for item in supported_results)
    result["cryptographically_valid"] = any(item["cryptographically_valid"] for item in supported_results)
    result["trusted"] = any(item["trusted"] for item in supported_results)
    result["revocation_checked"] = any(item["revocation_checked"] for item in supported_results)
    result["signer_subject"] = signature_results[0]["signer_subject"] or None
    result["signer_issuer"] = signature_results[0]["signer_issuer"] or None
    result["pdf_signature_date"] = signature_results[0]["signing_time"]
    primary = next((item for item in reversed(signature_results) if _signature_technical_passed(item)), signature_results[-1])
    result.update({
        "provider": primary["provider"],
        "signature_type": primary["signature_type"],
        "is_allowed_signature": primary["signature_type"] in ALLOWED_SIGNATURE_TYPES,
        "is_szafir_signature": primary["signature_type"] == "mszafir",
        "is_trusted_profile_signature": primary["signature_type"] == "profil_zaufany",
    })
    result["cryptographically_valid"] = primary["cryptographically_valid"]
    result["cryptographic_valid"] = primary["cryptographically_valid"]
    result["integrity_ok"] = bool(primary["integrity_ok"] and primary["docmdp_ok"] is not False)
    result["document_integrity_valid"] = primary["integrity_ok"]
    result["trusted"] = primary["trusted"]
    result["certificate_chain_valid"] = primary["certificate_chain_valid"]
    result["signer_certificate_found"] = primary["signer_certificate_found"]
    result["chain_built"] = primary["chain_built"]
    result["chain_valid"] = primary["chain_valid"]
    result["revocation_status"] = primary["revocation_status"]
    result["timestamp_valid"] = primary["timestamp_valid"]
    result.update(resolve_signature_policy(result))
    technical_passes = [item for item in supported_results if _signature_technical_passed(item)]
    if technical_passes:
        result.update(validation_status="VALID", reason_code="OK", result="VALID_SIGNATURE", reason="Podpis i integralność dokumentu zostały zweryfikowane.")
    elif not supported_results:
        result.update(validation_status="UNSUPPORTED", reason_code="UNSUPPORTED_SIGNATURE_FORMAT", result="UNSUPPORTED_SIGNATURE", reason="Nieobsługiwany format podpisu PDF.")
    elif all(item["verification_error"] for item in supported_results):
        result.update(validation_status="ERROR", reason_code="VERIFIER_EXCEPTION", result="VERIFICATION_ERROR", reason="Weryfikator nie był w stanie sprawdzić podpisu.")
    elif not any(item["integrity_ok"] for item in supported_results):
        result.update(validation_status="INVALID", reason_code="DOCUMENT_INTEGRITY_FAILURE", result="INVALID_SIGNATURE", reason="Skrót podpisanej treści PDF nie zgadza się z podpisem.")
    elif not any(item["cryptographically_valid"] for item in supported_results):
        result.update(validation_status="INVALID", reason_code="SIGNATURE_CRYPTOGRAPHIC_FAILURE", result="INVALID_SIGNATURE", reason="Biblioteka potwierdziła niepoprawny podpis kryptograficzny CMS.")
    elif not any(item["docmdp_ok"] is not False for item in supported_results):
        result.update(validation_status="INVALID", reason_code="DOCUMENT_MODIFICATION_NOT_ALLOWED", result="INVALID_SIGNATURE", reason="Dokument zawiera zmiany niedozwolone przez politykę podpisu.")
    elif any(item["revoked"] for item in signature_results):
        result.update(validation_status="INVALID", reason_code="CERTIFICATE_REVOKED", result="INVALID_SIGNATURE", reason="Certyfikat podpisu został unieważniony.")
    elif any(item["timestamp_invalid"] for item in signature_results):
        result.update(validation_status="INVALID", reason_code="TIMESTAMP_CRYPTOGRAPHIC_FAILURE", result="INVALID_SIGNATURE", reason="Znacznik czasu jest niepoprawny kryptograficznie.")
    elif any(item["timestamp_status"] == "INDETERMINATE" for item in supported_results):
        result.update(validation_status="INDETERMINATE", reason_code="TRUST_STATUS_INDETERMINATE", result="VERIFICATION_ERROR", reason="Nie udało się potwierdzić zaufania znacznika czasu.")
    elif any(item["trust_problem_indic"] == "EXPIRED" for item in supported_results):
        result.update(validation_status="UNTRUSTED", reason_code="CERTIFICATE_EXPIRED", result="INVALID_SIGNATURE", reason="Certyfikat podpisu wygasł.")
    elif any(item["trust_problem_indic"] in {"TRY_LATER", "REVOCATION_OUT_OF_BOUNDS_NO_POE"} for item in supported_results):
        result.update(validation_status="INDETERMINATE", reason_code="REVOCATION_STATUS_UNKNOWN", result="VERIFICATION_ERROR", reason="Nie udało się jednoznacznie ustalić statusu unieważnienia certyfikatu.")
    else:
        result.update(validation_status="UNTRUSTED", reason_code="CERTIFICATE_TRUST_FAILURE", result="VERIFICATION_ERROR", reason="Podpis jest poprawny kryptograficznie, ale łańcuch certyfikatu nie jest zaufany.")
    result["timestamp_status"] = primary["timestamp_status"]
    return result


class PyHankoSignatureBackend:
    name = "pyhanko_pdf_cms"

    def verify(self, pdf_bytes: bytes) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = Path(handle.name)
            handle.write(pdf_bytes)
        try:
            return _verify_signed_pdf_with_pyhanko(path)
        finally:
            path.unlink(missing_ok=True)


def _detect_signature_classification(pdf_path: Path) -> dict[str, Any]:
    """Classify signer identity for routing only; it is never proof of validity."""
    fallback = _classify_signature("")
    try:
        with Path(pdf_path).open("rb") as stream:
            signatures = list(PdfFileReader(stream).embedded_signatures)
        for signature in signatures:
            certificate = signature.signer_cert
            subject = _normalize_name_dict(certificate.subject.native if certificate else {})
            issuer = _normalize_name_dict(certificate.issuer.native if certificate else {})
            classification = _classify_signature(f"{subject} || {issuer}")
            if classification["signature_type"] == "profil_zaufany":
                return classification
            if fallback["signature_type"] == "unsupported":
                fallback = classification
    except Exception:
        logger.info(
            "signature_backend_classification_failed",
            extra={"event": "signature_backend_classification_failed", "operation": "signature_verification"},
        )
    try:
        for subject, issuer in inspect_pdf_cms_signer_names(Path(pdf_path).read_bytes()):
            classification = _classify_signature(f"{subject} || {issuer}")
            if classification["signature_type"] == "profil_zaufany":
                return classification
            if fallback["signature_type"] == "unsupported":
                fallback = classification
    except Exception:
        logger.info(
            "signature_cms_fallback_classification_failed",
            extra={"event": "signature_backend_classification_failed", "operation": "signature_verification"},
        )
    return fallback


def _normalize_trusted_profile_result(
    payload: dict[str, Any], classification: dict[str, Any]
) -> dict[str, Any]:
    result = _build_default_result()
    from datetime import datetime, timezone

    count = int(payload.get("signature_count") or 0)
    timestamp_valid = payload.get("timestamp_valid")
    revocation_status = str(payload.get("revocation_status") or "NOT_CHECKED")
    cryptographic_valid = payload.get("cryptographic_valid")
    integrity_valid = payload.get("document_integrity_valid")
    chain_valid = payload.get("certificate_chain_valid")
    signature_item = {
        "field_name": "",
        "subfilter": "/ETSI.CAdES.detached",
        "integrity_ok": integrity_valid,
        "cryptographically_valid": cryptographic_valid,
        "trusted": chain_valid,
        "revoked": False,
        "revocation_checked": False,
        "docmdp_ok": integrity_valid,
        "timestamp_valid": timestamp_valid,
        "timestamp_status": "NOT_PRESENT",
        "timestamp_invalid": False,
        "verification_error": payload.get("reason_code") == "VERIFICATION_ERROR",
        "unsupported_format": payload.get("reason_code") in {
            "UNSUPPORTED_SIGNATURE_FORMAT",
            "UNSUPPORTED_SIGNATURE_ALGORITHM",
        },
        "provider": classification.get("provider") or "eurocert",
        "signature_type": "profil_zaufany",
        "certificate_chain_valid": chain_valid,
        "signer_certificate_found": payload.get("signer_certificate_found") is True,
        "chain_built": chain_valid is True,
        "chain_valid": chain_valid,
        "revocation_status": revocation_status,
        "accepted": payload.get("valid") is True,
        "bottom_line": payload.get("valid") is True,
        "md_algorithm": payload.get("digest_algorithm"),
        "pkcs7_signature_mechanism": payload.get("signature_algorithm"),
        "signed_attrs_present": True if payload.get("signer_certificate_found") else None,
        "message_digest_matches": integrity_valid,
    }
    signatures = [signature_item] if count else []
    result.update(
        validated_at=datetime.now(timezone.utc).isoformat(),
        verifier_backend="trusted_profile_minimal_cms",
        signature_count=count,
        signatures=signatures,
        is_signed=count > 0,
        is_valid_structure=count > 0,
        provider=classification.get("provider") or "eurocert",
        signature_type="profil_zaufany",
        detected_signature_type="profil_zaufany",
        is_allowed_signature=True,
        is_szafir_signature=False,
        is_trusted_profile_signature=True,
        likely_mobywatel=True,
        cryptographically_valid=cryptographic_valid,
        cryptographic_valid=cryptographic_valid,
        integrity_ok=integrity_valid,
        document_integrity_valid=integrity_valid,
        trusted=chain_valid,
        certificate_chain_valid=chain_valid,
        signer_certificate_found=payload.get("signer_certificate_found") is True,
        chain_built=chain_valid is True,
        chain_valid=chain_valid,
        revocation_checked=False,
        revocation_status=revocation_status,
        timestamp_valid=timestamp_valid,
        timestamp_status="NOT_PRESENT",
        trust_source="configured_document_signing_roots",
        trust_roots_loaded_count=payload.get("trust_roots_loaded_count", 0),
        intermediate_certs_loaded_count=payload.get("intermediate_certs_loaded_count", 0),
        eutl_enabled=False,
    )
    reason_code = str(payload.get("reason_code") or "VERIFICATION_ERROR")
    if reason_code == "NO_SIGNATURE":
        result.update(validation_status="UNSIGNED", reason_code="NO_SIGNATURE", result="NO_SIGNATURE", reason="Brak podpisu PDF/PAdES.")
    elif reason_code in {"UNSUPPORTED_SIGNATURE_FORMAT", "UNSUPPORTED_SIGNATURE_ALGORITHM"}:
        result.update(validation_status="UNSUPPORTED", reason_code=reason_code, result="UNSUPPORTED_SIGNATURE", reason="Format lub algorytm podpisu nie jest obsługiwany przez minimalny verifier.")
    elif reason_code == "DOCUMENT_INTEGRITY_FAILURE":
        result.update(validation_status="INVALID", reason_code="DOCUMENT_INTEGRITY_FAILURE", result="INVALID_SIGNATURE", reason="Integralność podpisanego dokumentu jest nieprawidłowa.")
    elif reason_code == "SIGNER_CERTIFICATE_NOT_FOUND":
        result.update(validation_status="INVALID", reason_code=reason_code, result="INVALID_SIGNATURE", reason="SignerInfo.sid nie wskazuje certyfikatu osadzonego w CMS.")
    elif reason_code == "SIGNATURE_CRYPTOGRAPHIC_FAILURE":
        result.update(validation_status="INVALID", reason_code="SIGNATURE_CRYPTOGRAPHIC_FAILURE", result="INVALID_SIGNATURE", reason="Podpis kryptograficzny jest nieprawidłowy.")
    elif reason_code in {"CERTIFICATE_TRUST_FAILURE", "CERTIFICATE_EXPIRED"}:
        result.update(validation_status="INVALID", reason_code=reason_code, result="INVALID_SIGNATURE", reason="Lokalny łańcuch certyfikacji lub okres ważności certyfikatu jest nieprawidłowy.")
    elif payload.get("valid") is True:
        result.update(validation_status="VALID", reason_code="VALID_SIGNATURE", result="VALID_SIGNATURE", reason="Minimalny verifier potwierdził CMS/RSA, integralność PDF i lokalny łańcuch certyfikacji.")
    else:
        result.update(validation_status="ERROR", reason_code="VERIFICATION_ERROR", result="VERIFICATION_ERROR", reason="Minimalny verifier nie był w stanie sprawdzić podpisu.")
    return result


def _pyhanko_diagnostic_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "backend": "pyhanko_pdf_cms",
        "validation_status": result.get("validation_status"),
        "cryptographic_valid": result.get("cryptographic_valid"),
        "document_integrity_valid": result.get("document_integrity_valid"),
        "certificate_chain_valid": result.get("certificate_chain_valid"),
        "result": signature_verification_result(result),
    }


def verify_signed_pdf(pdf_path: Path) -> dict:
    """Route Profil Zaufany to the local CMS verifier and retain pyHanko elsewhere."""
    path = Path(pdf_path)
    try:
        pdf_bytes = path.read_bytes()
    except OSError as exc:
        result = _build_default_result()
        result.update(validation_status="ERROR", reason_code="PDF_READ_ERROR", result="VERIFICATION_ERROR", reason=f"Nie udało się odczytać PDF: {exc.__class__.__name__}.")
        return result

    classification = _detect_signature_classification(path)
    if classification.get("signature_type") != "profil_zaufany":
        return PyHankoSignatureBackend().verify(pdf_bytes)

    payload = TrustedProfileMinimalBackend(
        trust_roots=_configured_paths("SIGNATURE_TRUST_ROOTS"),
        intermediates=_configured_paths("SIGNATURE_INTERMEDIATE_CERTS"),
    ).verify(pdf_bytes)
    result = _normalize_trusted_profile_result(payload, classification)
    logger.info(
        "signature_verification_result backend=%s count=%s integrity=%s crypto=%s chain=%s provider_allowed=%s reason_code=%s",
        result.get("verifier_backend"),
        result.get("signature_count"),
        result.get("document_integrity_valid"),
        result.get("cryptographic_valid"),
        result.get("certificate_chain_valid"),
        result.get("provider_allowed"),
        result.get("reason_code"),
        extra={
            "event": "signature_verification_result",
            "operation": "signature_verification",
            "verifier_backend": result.get("verifier_backend"),
            "signature_count": result.get("signature_count"),
            "document_integrity_valid": result.get("document_integrity_valid"),
            "cryptographic_valid": result.get("cryptographic_valid"),
            "certificate_chain_valid": result.get("certificate_chain_valid"),
            "provider_allowed": result.get("provider_allowed"),
            "reason_code": result.get("reason_code"),
        },
    )

    if _env_bool("TRUSTED_PROFILE_PYHANKO_DIAGNOSTIC", default=True):
        try:
            diagnostic = PyHankoSignatureBackend().verify(pdf_bytes)
            primary_outcome = signature_verification_result(result)
            pyhanko_outcome = signature_verification_result(diagnostic)
            disagreement = primary_outcome != pyhanko_outcome
            result["backend_disagreement"] = disagreement
            result["secondary_backend"] = _pyhanko_diagnostic_summary(diagnostic)
            if disagreement:
                logger.warning(
                    "signature_backend_disagreement primary=%s secondary=%s",
                    primary_outcome,
                    pyhanko_outcome,
                    extra={
                        "event": "signature_backend_disagreement",
                        "operation": "signature_verification",
                        "primary_backend": "trusted_profile_minimal_cms",
                        "secondary_backend": "pyhanko_pdf_cms",
                        "primary_result": primary_outcome,
                        "secondary_result": pyhanko_outcome,
                    },
                )
        except Exception:
            logger.warning(
                "signature_secondary_backend_unavailable",
                extra={
                    "event": "signature_secondary_backend_unavailable",
                    "operation": "signature_verification",
                    "secondary_backend": "pyhanko_pdf_cms",
                },
            )
    return result


def verify_signed_pdf_bytes(pdf_bytes: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        path = Path(handle.name)
        handle.write(pdf_bytes)
    try:
        return verify_signed_pdf(path)
    finally:
        path.unlink(missing_ok=True)
