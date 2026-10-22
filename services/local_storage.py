from __future__ import annotations

import json
import shutil
from pathlib import Path

from services.nextcloud_storage import NextcloudStorage


class LocalStorage(NextcloudStorage):
    """Filesystem-backed storage compatible with the Nextcloud storage interface."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        forms_dir: str = "Formularze",
        output_dir: str = "output",
        csv_filename: str = "dane.csv",
    ) -> None:
        # Nie wywołujemy NextcloudStorage.__init__(), ponieważ wymaga
        # adresu URL i poświadczeń WebDAV.
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.forms_dir = str(forms_dir or "Formularze").replace("\\", "/").strip("/")
        self.output_dir = str(output_dir or "output").replace("\\", "/").strip("/")
        self.csv_filename = str(csv_filename or "dane.csv").strip()

        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _local_path(self, path: str) -> Path:
        normalized = str(path or "").replace("\\", "/").strip("/")

        parts = [
            part
            for part in normalized.split("/")
            if part not in {"", "."}
        ]

        if any(part == ".." for part in parts):
            raise ValueError("Ścieżka storage nie może wychodzić poza katalog główny.")

        resolved = self.root_dir.joinpath(*parts).resolve()

        try:
            resolved.relative_to(self.root_dir)
        except ValueError as exc:
            raise ValueError(
                "Ścieżka storage nie może wychodzić poza katalog główny."
            ) from exc

        return resolved

    def exists(self, path: str) -> bool:
        if self._local_path(path).exists():
            return True

        for fallback_path in self._pdf_lookup_paths_from_storage_path(path):
            if self._local_path(fallback_path).exists():
                return True

        return False

    def mkdir(self, path: str) -> None:
        self._local_path(path).mkdir(parents=True, exist_ok=True)

    def ensure_base_structure(self) -> None:
        self.mkdir(self.forms_dir)
        self.mkdir(self.output_dir)

    def ensure_form_output_structure(self, slug: str) -> None:
        pdf_root = f"{self.output_dir}/{slug}/pdf"

        self.mkdir(f"{self.output_dir}/{slug}")
        self.mkdir(pdf_root)
        self.mkdir(f"{pdf_root}/{self.PDF_DECLARATION_DIR}")
        self.mkdir(
            f"{pdf_root}/{self.PDF_DECLARATION_DIR}/{self.PDF_SIGNED_DIR}"
        )
        self.mkdir(
            f"{pdf_root}/{self.PDF_DECLARATION_DIR}/{self.PDF_UNSIGNED_DIR}"
        )
        self.mkdir(f"{pdf_root}/{self.PDF_AGREEMENT_DIR}")
        self.mkdir(
            f"{pdf_root}/{self.PDF_AGREEMENT_DIR}/{self.PDF_SIGNED_DIR}"
        )
        self.mkdir(
            f"{pdf_root}/{self.PDF_AGREEMENT_DIR}/{self.PDF_UNSIGNED_DIR}"
        )

    def list_form_files(self) -> list[str]:
        self.ensure_base_structure()

        forms_path = self._local_path(self.forms_dir)

        return sorted(
            path.name
            for path in forms_path.glob("*.json")
            if path.is_file()
        )

    def read_form_json(self, filename: str) -> dict:
        path = self._local_path(f"{self.forms_dir}/{filename}")

        with path.open("r", encoding="utf-8-sig") as handle:
            value = json.load(handle)

        if not isinstance(value, dict):
            raise ValueError(
                f"Definicja formularza {filename!r} musi być obiektem JSON."
            )

        return value

    def read_text_or_empty(self, path: str) -> str:
        local_path = self._local_path(path)

        if not local_path.exists():
            return ""

        return local_path.read_text(encoding="utf-8")

    def read_bytes(self, path: str) -> bytes:
        return self._local_path(path).read_bytes()

    def write_text(
        self,
        path: str,
        content: str,
        content_type: str | None = None,
    ) -> None:
        local_path = self._local_path(path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(content, encoding="utf-8")

    def write_bytes(
        self,
        path: str,
        content: bytes,
        content_type: str | None = None,
    ) -> None:
        local_path = self._local_path(path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(bytes(content))

    def delete(self, path: str, *, missing_ok: bool = False) -> None:
        local_path = self._local_path(path)

        if not local_path.exists():
            if missing_ok:
                return
            raise FileNotFoundError(path)

        if local_path.is_dir():
            shutil.rmtree(local_path)
        else:
            local_path.unlink()

    def get_pdf_bytes(self, slug: str, filename: str) -> bytes:
        last_error: Exception | None = None

        for path in self._pdf_lookup_paths(slug, filename):
            try:
                return self.read_bytes(path)
            except FileNotFoundError as exc:
                last_error = exc

        if last_error:
            raise last_error

        raise FileNotFoundError(filename)

    def get_file_bytes(self, path: str) -> bytes:
        normalized = str(path or "").replace("\\", "/").lstrip("/")
        return self.read_bytes(normalized)