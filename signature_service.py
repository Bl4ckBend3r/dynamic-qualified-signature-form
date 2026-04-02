import json
import shutil
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any
from uuid import uuid4


class QualifiedSignatureProvider(ABC):
    def __init__(self, config):
        self.config = config
        self.work_dir = Path(config["SIGNATURE_WORK_DIR"])
        self.work_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def create_signature_request(
        self,
        submission_id: str,
        pdf_path: Path,
        form_name: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def submit_document_for_signature(self, request_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_signature_status(self, request_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def save_signed_document(self, request_id: str, target_dir: Path) -> Path:
        raise NotImplementedError


class MockQualifiedSignatureProvider(QualifiedSignatureProvider):
    ALLOWED_STATUSES = {"pending", "signed", "failed"}

    def __init__(self, config):
        super().__init__(config)
        mode = str(config["SIGNATURE_MOCK_MODE"]).lower()
        self.mock_mode = mode if mode in self.ALLOWED_STATUSES else "signed"

    def create_signature_request(
        self,
        submission_id: str,
        pdf_path: Path,
        form_name: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        request_id = str(uuid4())
        created_at = datetime.now(timezone.utc).astimezone().isoformat()

        request_data = {
            "request_id": request_id,
            "submission_id": submission_id,
            "form_name": form_name,
            "source_pdf_path": str(pdf_path),
            "signed_pdf_path": "",
            "status": "pending",
            "provider": "mock",
            "created_at": created_at,
            "updated_at": created_at,
            "metadata": metadata,
        }

        self._save_request_data(request_id, request_data)
        return request_data

    def submit_document_for_signature(self, request_id: str) -> None:
        request_data = self._load_request_data(request_id)
        now = datetime.now(timezone.utc).astimezone().isoformat()

        if self.mock_mode == "pending":
            request_data["status"] = "pending"
            request_data["updated_at"] = now
            self._save_request_data(request_id, request_data)
            return

        if self.mock_mode == "failed":
            request_data["status"] = "failed"
            request_data["updated_at"] = now
            self._save_request_data(request_id, request_data)
            return

        source_pdf_path = Path(request_data["source_pdf_path"])
        signed_storage_dir = self.work_dir / "signed_mock"
        signed_storage_dir.mkdir(parents=True, exist_ok=True)

        signed_filename = f"{request_id}_signed.pdf"
        signed_path = signed_storage_dir / signed_filename
        shutil.copy2(source_pdf_path, signed_path)

        request_data["status"] = "signed"
        request_data["signed_pdf_path"] = str(signed_path)
        request_data["updated_at"] = now
        self._save_request_data(request_id, request_data)

    def get_signature_status(self, request_id: str) -> str:
        request_data = self._load_request_data(request_id)
        return request_data["status"]

    def save_signed_document(self, request_id: str, target_dir: Path) -> Path:
        request_data = self._load_request_data(request_id)

        if request_data["status"] != "signed":
            raise ValueError("Nie można zapisać podpisanego dokumentu dla statusu innego niż 'signed'.")

        signed_source = Path(request_data["signed_pdf_path"])
        target_dir.mkdir(parents=True, exist_ok=True)

        final_filename = f"{request_data['submission_id']}_signed.pdf"
        final_path = target_dir / final_filename
        shutil.copy2(signed_source, final_path)

        request_data["signed_pdf_path"] = str(final_path)
        request_data["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat()
        self._save_request_data(request_id, request_data)

        return final_path

    def _request_file_path(self, request_id: str) -> Path:
        return self.work_dir / f"{request_id}.json"

    def _save_request_data(self, request_id: str, data: Dict[str, Any]) -> None:
        with open(self._request_file_path(request_id), "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def _load_request_data(self, request_id: str) -> Dict[str, Any]:
        with open(self._request_file_path(request_id), "r", encoding="utf-8") as file:
            return json.load(file)


class RestQualifiedSignatureProvider(QualifiedSignatureProvider):
    """
    Szkielet integracji z rzeczywistym dostawcą podpisu kwalifikowanego przez REST API.
    Implementacja metod powinna zostać dostosowana do konkretnego dostawcy.
    """

    def create_signature_request(
        self,
        submission_id: str,
        pdf_path: Path,
        form_name: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError("Zaadaptuj tę metodę do API dostawcy podpisu kwalifikowanego.")

    def submit_document_for_signature(self, request_id: str) -> None:
        raise NotImplementedError("Zaadaptuj tę metodę do API dostawcy podpisu kwalifikowanego.")

    def get_signature_status(self, request_id: str) -> str:
        raise NotImplementedError("Zaadaptuj tę metodę do API dostawcy podpisu kwalifikowanego.")

    def save_signed_document(self, request_id: str, target_dir: Path) -> Path:
        raise NotImplementedError("Zaadaptuj tę metodę do API dostawcy podpisu kwalifikowanego.")


def build_signature_provider(config):
    provider_name = str(config["SIGNATURE_PROVIDER"]).lower()

    if provider_name == "mock":
        return MockQualifiedSignatureProvider(config)

    if provider_name == "rest":
        return RestQualifiedSignatureProvider(config)

    raise ValueError(f"Nieobsługiwany provider podpisu: {provider_name}")