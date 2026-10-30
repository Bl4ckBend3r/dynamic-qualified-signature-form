from __future__ import annotations

import secrets
from pathlib import Path

from models import FormRegulation, ServiceDocument
from services.logo_service import safe_asset_filename
from services.upload_validation import validate_document_upload, validate_form_import_instruction_upload


SERVICE_DOCUMENT_TYPES = {
    "terms": "Regulamin serwisu",
    "privacy": "Polityka prywatności",
    "rodo": "Klauzula RODO",
}
FORM_IMPORT_INSTRUCTION_TYPE = "form_import_instruction"
FORM_IMPORT_INSTRUCTION_TITLE = "Instrukcja przygotowania plików formularza"


def get_form_import_instruction(db) -> ServiceDocument | None:
    from sqlalchemy import select

    return db.execute(
        select(ServiceDocument).where(ServiceDocument.document_type == FORM_IMPORT_INSTRUCTION_TYPE)
    ).scalar_one_or_none()


def save_document_upload(*, temp_dir: str | Path, uploaded_filename: str, uploaded_bytes: bytes, uploaded_mimetype: str | None) -> dict:
    mime_type = validate_document_upload(uploaded_filename, uploaded_bytes, uploaded_mimetype)
    document_dir = Path(temp_dir) / "site_documents"
    document_dir.mkdir(parents=True, exist_ok=True)
    storage_name = f"{secrets.token_hex(8)}_{safe_asset_filename(uploaded_filename)}"
    storage_path = document_dir / storage_name
    storage_path.write_bytes(uploaded_bytes)
    return {
        "original_filename": Path(uploaded_filename).name,
        "storage_path": str(storage_path),
        "mime_type": mime_type,
        "size_bytes": len(uploaded_bytes),
    }


def save_form_import_instruction_upload(
    *,
    temp_dir: str | Path,
    uploaded_filename: str,
    uploaded_bytes: bytes,
    uploaded_mimetype: str | None,
) -> dict:
    mime_type = validate_form_import_instruction_upload(uploaded_filename, uploaded_bytes, uploaded_mimetype)
    document_dir = Path(temp_dir) / "site_documents"
    document_dir.mkdir(parents=True, exist_ok=True)
    storage_name = f"{secrets.token_hex(8)}_{safe_asset_filename(uploaded_filename)}"
    storage_path = document_dir / storage_name
    storage_path.write_bytes(uploaded_bytes)
    return {
        "original_filename": Path(uploaded_filename).name,
        "storage_path": str(storage_path),
        "mime_type": mime_type,
        "size_bytes": len(uploaded_bytes),
    }


def update_form_regulation_from_upload(regulation: FormRegulation, metadata: dict, *, uploaded_by_user_id: int | None) -> FormRegulation:
    regulation.original_filename = metadata["original_filename"]
    regulation.storage_path = metadata["storage_path"]
    regulation.mime_type = metadata["mime_type"]
    regulation.size_bytes = metadata["size_bytes"]
    regulation.uploaded_by_user_id = uploaded_by_user_id
    return regulation


def update_service_document_from_upload(document: ServiceDocument, metadata: dict, *, uploaded_by_user_id: int | None) -> ServiceDocument:
    document.original_filename = metadata["original_filename"]
    document.storage_path = metadata["storage_path"]
    document.mime_type = metadata["mime_type"]
    document.size_bytes = metadata["size_bytes"]
    document.updated_by_user_id = uploaded_by_user_id
    return document
