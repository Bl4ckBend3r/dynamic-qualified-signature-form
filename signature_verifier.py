from __future__ import annotations

from pathlib import Path
from typing import Any

from asn1crypto import cms
from pypdf import PdfReader


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

    pdf_signature = _extract_pdf_signature(pdf_path)
    if not pdf_signature:
        result["reason"] = "Brak pola podpisu PDF."
        return result

    signature_contents = pdf_signature.get("contents")
    if not signature_contents:
        result["reason"] = "Brak zawartości podpisu PDF."
        return result

    result["pdf_signature_name"] = pdf_signature.get("name") or None
    result["pdf_signature_reason"] = pdf_signature.get("reason") or None
    result["pdf_signature_date"] = pdf_signature.get("date") or None
    result["pdf_signature_subfilter"] = pdf_signature.get("subfilter") or None

    try:
        content_info = cms.ContentInfo.load(signature_contents)
    except Exception as exc:
        result["reason"] = f"Nie udało się odczytać CMS podpisu: {exc}"
        return result

    if content_info["content_type"].native != "signed_data":
        result["reason"] = "PDF zawiera pole podpisu, ale nie jest to CMS signed_data."
        return result

    signed_data = content_info["content"]
    certificates = signed_data["certificates"]

    if not certificates:
        result["reason"] = "Brak certyfikatów w podpisie."
        return result

    signer_infos = signed_data["signer_infos"]
    if not signer_infos:
        result["reason"] = "Brak signer_info w podpisie."
        return result

    signer_info = signer_infos[0]
    sid = signer_info["sid"]

    signer_cert = None

    if sid.name == "issuer_and_serial_number":
        sid_issuer = sid.chosen["issuer"].native
        sid_serial = sid.chosen["serial_number"].native

        for cert_choice in certificates:
            cert = cert_choice.chosen
            if cert.issuer.native == sid_issuer and cert.serial_number == sid_serial:
                signer_cert = cert
                break

    if signer_cert is None:
        signer_cert = certificates[-1].chosen

    subject_text = _normalize_name_dict(signer_cert.subject.native)
    issuer_text = _normalize_name_dict(signer_cert.issuer.native)
    pdf_metadata_text = " || ".join(
        part
        for part in [
            pdf_signature.get("name"),
            pdf_signature.get("reason"),
            pdf_signature.get("filter"),
            pdf_signature.get("subfilter"),
        ]
        if part
    )
    combined_text = f"{subject_text} || {issuer_text} || {pdf_metadata_text}"

    result["is_signed"] = True
    result["is_valid_structure"] = True
    result["signer_subject"] = subject_text or None
    result["signer_issuer"] = issuer_text or None
    result.update(_classify_signature(combined_text))

    return result
