from __future__ import annotations

from pathlib import Path
from typing import Any

from asn1crypto import cms
from pypdf import PdfReader


SZAFIR_ISSUER_HINTS = [
    "KRAJOWA IZBA ROZLICZENIOWA",
    "COPE SZAFIR",
    "SZAFIR - KWALIFIKOWANY",
    "KIR",
]

MOBYWATEL_PROVIDER_HINTS = [
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
    "PWPW",
    "DOPODPISU",
    "EUROCERT",
]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_name_dict(name_dict: dict | None) -> str:
    if not name_dict:
        return ""
    parts = []
    for _, value in name_dict.items():
        if value:
            parts.append(str(value))
    return " | ".join(parts)


def _match_any(text: str, hints: list[str]) -> bool:
    text_upper = text.upper()
    return any(hint in text_upper for hint in hints)


def _extract_pdf_signature_contents(pdf_path: Path) -> bytes | None:
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
        if contents:
            return bytes(contents)

    return None


def verify_signed_pdf(pdf_path: Path) -> dict:
    result = {
        "is_signed": False,
        "is_valid_structure": False,
        "likely_mobywatel": False,
        "provider": None,
        "is_szafir_signature": False,
        "signer_subject": None,
        "signer_issuer": None,
        "reason": None,
    }

    signature_contents = _extract_pdf_signature_contents(pdf_path)
    if not signature_contents:
        result["reason"] = "Brak pola podpisu PDF."
        return result

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
    combined_text = f"{subject_text} || {issuer_text}"

    result["is_signed"] = True
    result["is_valid_structure"] = True
    result["signer_subject"] = subject_text or None
    result["signer_issuer"] = issuer_text or None

    if _match_any(combined_text, SZAFIR_ISSUER_HINTS):
        result["provider"] = "szafir"
        result["is_szafir_signature"] = True
        result["likely_mobywatel"] = True
        result["reason"] = "Wykryto podpis kwalifikowany Szafir / KIR."
        return result

    if _match_any(combined_text, MOBYWATEL_PROVIDER_HINTS):
        result["provider"] = "qualified-provider"
        result["likely_mobywatel"] = True
        result["reason"] = "Wykryto podpis od dostawcy używanego przez mObywatel."
        return result

    result["provider"] = "other"
    result["reason"] = "Wykryto podpis, ale nie jest to podpis Szafir / KIR."
    return result