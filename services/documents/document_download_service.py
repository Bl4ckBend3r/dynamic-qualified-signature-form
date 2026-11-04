from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import mimetypes

from services.upload_validation import validate_upload_filename


@dataclass(frozen=True)
class DocumentDownload:
    pdf_bytes: bytes
    download_name: str
    mimetype: str = "application/pdf"


class DocumentDownloadService:
    def __init__(self, access_service=None) -> None:
        from services.documents.document_access_service import DocumentAccessService

        self.access_service = access_service or DocumentAccessService()

    def clean_pdf_filename(self, filename: str) -> str:
        return validate_upload_filename(filename, allowed_suffixes={".pdf"})

    def verify_access(self, *, document_service, submission: dict, token: str | None) -> bool:
        return self.access_service.verify_download_access(
            document_service=document_service,
            submission=submission,
            token=token,
        )

    def prepare_download(self, *, document_service, submission: dict, filename: str, signed: bool) -> DocumentDownload:
        clean_filename = self.clean_pdf_filename(filename)
        pdf_bytes = document_service.read_document_bytes_for_download(
            submission,
            clean_filename,
            signed=signed,
        )
        return DocumentDownload(
            pdf_bytes=pdf_bytes,
            download_name=Path(clean_filename).name,
        )

    @staticmethod
    def normalize_asset_path(asset_path: str, *, forms_dir: str, output_dir: str) -> str:
        normalized = str(asset_path or "").replace("\\", "/").strip().strip("/")
        forms_root = forms_dir.strip("/")
        output_root = output_dir.strip("/")
        if normalized.startswith((f"{forms_root}/", f"{output_root}/")):
            return normalized
        return f"{forms_root}/{normalized}"

    def prepare_asset(self, *, storage, asset_path: str, forms_dir: str, output_dir: str) -> DocumentDownload:
        resolved_path = self.normalize_asset_path(asset_path, forms_dir=forms_dir, output_dir=output_dir)
        if hasattr(storage, "read_bytes"):
            file_bytes = storage.read_bytes(resolved_path)
        else:
            file_bytes = storage.get_file_bytes(resolved_path)
        mime_type, _ = mimetypes.guess_type(Path(resolved_path).name)
        return DocumentDownload(
            pdf_bytes=file_bytes,
            download_name=Path(resolved_path).name,
            mimetype=mime_type or "application/octet-stream",
        )
