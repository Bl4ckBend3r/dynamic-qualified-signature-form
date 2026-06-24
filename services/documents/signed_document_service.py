from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from services.process_service import ProcessStatus, is_agreement_required


class SignedDocumentError(ValueError):
    pass


def _parse_json_list(value: str | list | None) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    raw = str(value or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _serialize_json_list(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)


class SignedDocumentService:
    @staticmethod
    def validate_pdf_bytes(pdf_bytes: bytes) -> None:
        if not bytes(pdf_bytes or b"").startswith(b"%PDF"):
            raise SignedDocumentError("Podpisany dokument musi byc plikiem PDF.")

    @staticmethod
    def build_signed_filename(filename: str) -> str:
        source = Path(filename)
        return f"{source.stem or 'dokument'}-signed{source.suffix or '.pdf'}"

    def build_target(self, row: Mapping[str, Any], document_id: str, instance_id: str | None) -> tuple[str, str, dict | None]:
        if document_id == "declaration":
            source_filename = str(row.get("declaration_filename") or "").strip()
            if not source_filename:
                raise ValueError("Najpierw wygeneruj deklaracje do podpisu.")
            return source_filename, self.build_signed_filename(source_filename), None

        agreements = _parse_json_list(row.get("training_agreements"))
        if agreements and instance_id:
            agreement = next((item for item in agreements if str(item.get("id") or "") == str(instance_id)), None)
            if not agreement:
                raise ValueError("Nie znaleziono umowy dla wybranego szkolenia.")
            source_filename = agreement.get("filename") or f"{instance_id}-umowa.pdf"
            return source_filename, self.build_signed_filename(source_filename), {"agreements": agreements, "agreement": agreement}

        source_filename = str(row.get("agreement_filename") or "").strip()
        if not source_filename:
            raise ValueError("Najpierw wygeneruj umowe do podpisu.")
        return source_filename, self.build_signed_filename(source_filename), None

    def build_updates(
        self,
        row: Mapping[str, Any],
        document_id: str,
        signed_filename: str,
        verification: Mapping[str, Any],
        is_signed: bool,
        is_valid: bool,
        update_target: dict | None,
    ) -> dict[str, Any]:
        signature_type = verification.get("signature_type") or "unknown"
        signature_error = "" if is_valid else verification.get("reason", "Niepoprawny podpis dokumentu.")

        if document_id == "declaration":
            return {
                "declaration_signed": "Tak" if is_signed else "Nie",
                "declaration_signed_filename": signed_filename if is_valid else "",
                "declaration_signature_type": signature_type,
                "declaration_signature_valid": "Tak" if is_valid else "Nie",
                "declaration_signature_error": signature_error,
                "process_status": (
                    ProcessStatus.AGREEMENT_READY.value
                    if is_valid and is_agreement_required(row)
                    else ProcessStatus.PARTICIPANT_ACCEPTED.value
                    if is_valid
                    else ProcessStatus.DECLARATION_SIGNATURE_INVALID.value
                ),
            }

        if update_target:
            agreement = update_target["agreement"]
            agreements = update_target["agreements"]
            agreement.update(
                {
                    "signed": is_signed,
                    "signature_valid": is_valid,
                    "signed_filename": signed_filename if is_valid else "",
                    "signature_type": signature_type,
                    "signature_error": signature_error,
                }
            )
            all_valid = all(bool(item.get("signature_valid")) for item in agreements)
            return {
                "training_agreements": _serialize_json_list(agreements),
                "agreement_signed": "Tak" if all_valid else "",
                "agreement_signature_valid": "Tak" if all_valid else "",
                "agreement_signed_filename": signed_filename if all_valid else "",
                "process_status": (
                    ProcessStatus.AGREEMENT_SIGNED.value
                    if all_valid
                    else ProcessStatus.AGREEMENT_WAITING_FOR_SIGNATURE.value
                ),
            }

        return {
            "agreement_signed": "Tak" if is_signed else "Nie",
            "agreement_signed_filename": signed_filename if is_valid else "",
            "agreement_signature_type": signature_type,
            "agreement_signature_valid": "Tak" if is_valid else "Nie",
            "agreement_signature_error": signature_error,
            "process_status": (
                ProcessStatus.AGREEMENT_SIGNED.value
                if is_valid
                else ProcessStatus.AGREEMENT_SIGNATURE_INVALID.value
            ),
        }

