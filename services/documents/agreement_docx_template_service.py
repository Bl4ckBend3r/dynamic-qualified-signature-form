from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Mapping

from docx import Document
from jinja2.sandbox import SandboxedEnvironment

from services.documents.agreement_template_context_service import (
    agreement_sample_context,
    agreement_variable_catalog,
)
from services.documents.docx_template_parser import PARSER_VERSION, parse_docx_template


DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class AgreementDocxTemplateService:
    def __init__(self, storage):
        self.storage = storage

    def upload(
        self,
        *,
        form_slug: str,
        filename: str,
        content: bytes,
        fields: Iterable[Any],
        uploaded_by_user_id: int | None,
    ) -> dict:
        if Path(filename or "").suffix.casefold() != ".docx":
            raise ValueError("Szablon umowy musi być plikiem DOCX.")
        parsed = parse_docx_template(content)
        catalog = agreement_variable_catalog(fields)
        known = {item["name"] for item in catalog}
        unknown = sorted(set(parsed.variables) - known)
        storage_path = self.storage_path(form_slug)
        self._ensure_directory(form_slug)
        self.storage.write_bytes(storage_path, content, DOCX_MIME_TYPE)
        return {
            "storage_path": storage_path,
            "original_filename": Path(filename).name,
            "mime_type": DOCX_MIME_TYPE,
            "size_bytes": len(content),
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "uploaded_by_user_id": uploaded_by_user_id,
            "parser_version": PARSER_VERSION,
            "variables": list(parsed.variables),
            "warnings": list(parsed.warnings),
            "unknown_variables": unknown,
            "valid": not unknown,
            "html": parsed.html,
        }

    def download(self, metadata: Mapping[str, Any]) -> bytes:
        path = str(metadata.get("storage_path") or "").strip()
        if not path:
            raise FileNotFoundError("Brak zapisanego szablonu DOCX.")
        return self.storage.read_bytes(path)

    def delete(self, metadata: Mapping[str, Any]) -> None:
        path = str(metadata.get("storage_path") or "").strip()
        if path:
            self.storage.delete(path, missing_ok=True)

    def preview_html(self, metadata: Mapping[str, Any], fields: Iterable[Any], form_definition: Mapping[str, Any]) -> str:
        template = str(metadata.get("html") or "")
        if not template:
            return ""
        environment = SandboxedEnvironment(autoescape=True)
        return environment.from_string(template).render(
            **agreement_sample_context(fields, form_definition=form_definition)
        )

    def sample_docx(self, fields: Iterable[Any]) -> bytes:
        known = {item["name"] for item in agreement_variable_catalog(fields)}
        document = Document()
        document.add_heading("UMOWA O DOFINANSOWANIE SZKOLENIA", level=1)
        document.add_paragraph("Numer umowy: {{ agreement_number }}")
        document.add_paragraph("zawarta z uczestnikiem {{ participant_name }}, PESEL {{ pesel }}.")
        document.add_heading("Przedmiot umowy", level=2)
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Szkolenie"
        table.cell(0, 1).text = "Cena"
        table.cell(1, 0).text = "{{ training_name }}"
        table.cell(1, 1).text = "{{ training_price_formatted }}"
        if "all_selected_trainings_total_formatted" in known:
            document.add_paragraph("Łączna wartość wszystkich wybranych szkoleń: {{ all_selected_trainings_total_formatted }}")
        document.add_paragraph("")
        document.add_paragraph("Podpis uczestnika: ........................................................")
        buffer = BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    def storage_path(self, form_slug: str) -> str:
        return f"{self.storage.output_dir}/{form_slug}/templates/agreement/agreement-template.docx"

    def _ensure_directory(self, form_slug: str) -> None:
        self.storage.mkdir(f"{self.storage.output_dir}/{form_slug}")
        self.storage.mkdir(f"{self.storage.output_dir}/{form_slug}/templates")
        self.storage.mkdir(f"{self.storage.output_dir}/{form_slug}/templates/agreement")
