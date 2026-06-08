from __future__ import annotations

import re
import tempfile
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from flask import Flask, current_app, request, url_for

from pdf_generator import generate_pdf, generate_pdf_from_html
from form_loader import build_consents_view, build_submission_view
from signature_verifier import verify_signed_pdf
from services.access_token_service import AccessTokenService
from services import document_naming_service as naming
from services.file_metadata import record_submission_file
from services.process_service import ProcessStatus, is_agreement_required
from services.upload_validation import UploadValidationError, validate_pdf_upload


FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ_-]+")
PDF_LOGO_FOOTER_PATTERN = re.compile(
    r"<footer\b[^>]*class=[\"'][^\"']*pdf-logo-footer[^\"']*[\"'][^>]*>.*?</footer>",
    re.IGNORECASE | re.DOTALL,
)
PDF_LOGO_HEADER_PATTERN = re.compile(
    r"<header\b[^>]*class=[\"'][^\"']*pdf-logo-header[^\"']*[\"'][^>]*>.*?</header>",
    re.IGNORECASE | re.DOTALL,
)
DOCUMENT_LOGO_HEADER_PATTERN = re.compile(
    r"<header\b[^>]*class=[\"'][^\"']*document-logo-header[^\"']*[\"'][^>]*>.*?</header>",
    re.IGNORECASE | re.DOTALL,
)
FORM_HEADER_IMAGE_PATTERN = re.compile(
    r"<div\b[^>]*class=[\"'][^\"']*form-header-image[^\"']*[\"'][^>]*>.*?</div>",
    re.IGNORECASE | re.DOTALL,
)
BODY_OPEN_PATTERN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


class DocumentType:
    DECLARATION = "declaration"
    AGREEMENT = "agreement"
    TRAINING_AGREEMENT = "training_agreement"


DEFAULT_DOCUMENT_CONFIG = {
    "enabled": False,
    "template": "",
    "filename_pattern": "",
    "signature_required": True,
}


class DocumentService:
    def __init__(
        self,
        storage=None,
        submission_repository=None,
        audit_log_service=None,
        access_token_service: AccessTokenService | None = None,
    ) -> None:
        self.storage = storage
        self.submission_repository = submission_repository
        self.audit_log_service = audit_log_service
        self.access_token_service = access_token_service or AccessTokenService()

    def get_documents_config(self, form_config: dict) -> list[dict]:
        from services.form_config_service import FormConfigService

        return FormConfigService().normalize_documents_config(form_config)

    def generate_document(
        self,
        submission: dict,
        form_config: dict,
        document_id: str,
        context_extra: dict | None = None,
        force: bool = False,
    ) -> dict:
        document = self.get_document_by_id(form_config, document_id)
        if not document:
            raise ValueError(f"Unknown document_id: {document_id}")
        if not document.get("enabled", True):
            updates = self._not_required_updates(document_id, form_config)
            self._update_submission(submission, updates)
            return {"enabled": False, "filename": "", "created": False, "document_id": document_id}

        row = self._row(submission)
        slug = self._slug(submission)
        submission_id = self._submission_id(submission)
        existing_filename = str(row.get(f"{document_id}_filename") or "").strip()
        generated_field = f"{document_id}_generated"

        if document_id == DocumentType.DECLARATION:
            existing_filename = str(row.get("declaration_filename") or "").strip()
            generated_field = "declaration_generated"
        if document_id == DocumentType.AGREEMENT:
            existing_filename = str(row.get("agreement_filename") or "").strip()
            generated_field = "agreement_generated"

        if not force and row.get(generated_field, "").strip().lower() == "tak" and existing_filename:
            try:
                self.storage.get_pdf_bytes(slug, existing_filename)
            except Exception:
                current_app.logger.warning("Brak PDF %s w storage, regeneruję.", existing_filename)
            else:
                return {
                    "enabled": True,
                    "filename": existing_filename,
                    "created": False,
                    "document_id": document_id,
                }

        render_row = {**row, **(context_extra or {})}
        filename = self.build_filename_for_document(document, render_row, document_id)
        context = build_document_pdf_context(
            form_definition=form_config,
            submission_id=submission_id,
            row=render_row,
            submission_view=build_submission_view(form_config, row),
            consents_view=build_consents_view(form_config, row),
            pdf_image_url=self.resolve_pdf_image_url(form_config),
            document_type=document_id,
        )
        self._add_collection_context(context, render_row)
        context.update(context_extra or {})
        document_bytes = generate_document_pdf_bytes(
            app=current_app._get_current_object(),
            template_name="declaration_template.html",
            template_html=document.get("template_html") or self.resolve_template_html(document.get("template", "")),
            context=context,
        )
        self.storage.save_pdf(
            slug,
            filename,
            document_bytes,
            document_type=self._storage_document_type(document_id),
            signed=False,
        )
        current_app.logger.info("Upload dokumentu do Nextcloud zakonczony sukcesem: %s", filename)
        record_submission_file(
            submission_repository=self.submission_repository,
            submission_id=submission_id,
            form_slug=slug,
            filename=filename,
            storage=self.storage,
            file_bytes=document_bytes,
            document_id=document_id,
            document_type=self._storage_document_type(document_id) or "",
            signed=False,
        )
        updates = self._generated_updates(document_id, filename)
        self._update_submission(submission, updates)
        self._audit("DOCUMENT_GENERATED", submission, metadata={"document_id": document_id, "filename": filename})
        return {
            "enabled": True,
            "document_id": document_id,
            "kind": document.get("kind"),
            "filename": filename,
            "generated": True,
            "created": True,
            "document": document,
        }

    def generate_documents_for_collection(
        self,
        submission: dict,
        form_config: dict,
        document_id: str,
        collection_field: str,
        item_alias: str,
        context_extra: dict | None = None,
    ) -> list[dict]:
        document = self.get_document_by_id(form_config, document_id)
        if not document:
            raise ValueError(f"Unknown document_id: {document_id}")
        row = self._row(submission)
        slug = self._slug(submission)
        submission_id = self._submission_id(submission)
        items = parse_json_list(row.get(collection_field))
        if not items:
            raise RuntimeError("Nie wybrano elementów do wygenerowania dokumentów.")

        generated_date = (context_extra or {}).get("generated_date") or date.today().isoformat()
        template_html = self.resolve_template_html(document.get("template", ""))
        generated_documents = []

        for sequence, item in enumerate(items, start=1):
            item_id = item.get("id") or item.get("value") or f"{item_alias}_{sequence}"
            agreement_number = self.build_document_number(
                document,
                submission_id=submission_id,
                sequence=sequence,
                generated_date=generated_date,
            )
            render_row = {
                **row,
                **(context_extra or {}),
                item_alias: item,
                "training": item,
                "training_id": item_id,
                "training_name": item.get("name", item.get("label", "")),
                "training_price": item.get("price", ""),
                "agreement_sequence": sequence,
                "agreement_number": agreement_number,
                "generated_date": generated_date,
                "agreement_generated_at": generated_date,
            }
            filename = self.build_filename_for_document(document, render_row, document_id)
            context = build_document_pdf_context(
                form_definition=form_config,
                submission_id=submission_id,
                row=render_row,
                submission_view=build_submission_view(form_config, row),
                consents_view=build_consents_view(form_config, row),
                pdf_image_url=self.resolve_pdf_image_url(form_config),
                document_type=document_id,
            )
            self._add_collection_context(context, render_row)
            context.update(render_row)
            document_bytes = generate_document_pdf_bytes(
                app=current_app._get_current_object(),
                template_name="declaration_template.html",
                template_html=template_html,
                context=context,
            )
            self.storage.save_pdf(
                slug,
                filename,
                document_bytes,
                document_type=self._storage_document_type(document_id),
                signed=False,
            )
            current_app.logger.info("Upload dokumentu do Nextcloud zakonczony sukcesem: %s", filename)
            record_submission_file(
                submission_repository=self.submission_repository,
                submission_id=submission_id,
                form_slug=slug,
                filename=filename,
                storage=self.storage,
                file_bytes=document_bytes,
                document_id=document_id,
                document_type=self._storage_document_type(document_id) or "",
                signed=False,
            )
            generated_documents.append(
                {
                    "id": str(item_id),
                    "training_id": str(item_id),
                    "training_name": item.get("name", item.get("label", "")),
                    "training_price": item.get("price", ""),
                    "sequence": sequence,
                    "number": agreement_number,
                    "generated_at": generated_date,
                    "filename": filename,
                    "signed": False,
                    "signature_valid": False,
                    "signed_filename": "",
                    "signature_type": "",
                    "signature_error": "",
                }
            )

        updates = {
            "agreement_generated": "Tak",
            "agreement_filename": generated_documents[0]["filename"] if generated_documents else "",
            "agreement_generated_at": generated_date,
            "training_agreements": serialize_json_list(generated_documents),
            "process_status": ProcessStatus.AGREEMENT_WAITING_FOR_SIGNATURE.value,
        }
        self._update_submission(submission, updates)
        self._audit(
            "DOCUMENT_GENERATED",
            submission,
            metadata={"document_id": document_id, "count": len(generated_documents)},
        )
        return generated_documents

    def upload_signed_document(
        self,
        submission: dict,
        document_id: str,
        uploaded_file,
        instance_id: str | None = None,
    ) -> dict:
        if not uploaded_file or not uploaded_file.filename:
            raise ValueError("Nie wybrano podpisanego pliku PDF.")
        row = self._row(submission)
        slug = self._slug(submission)
        uploaded_bytes = uploaded_file.read()
        try:
            validate_pdf_upload(uploaded_file.filename, uploaded_bytes, getattr(uploaded_file, "mimetype", None))
        except UploadValidationError as exc:
            raise ValueError(str(exc)) from exc
        source_filename, signed_filename, update_target = self._signed_document_target(row, document_id, instance_id)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=current_app.config["TEMP_DIR"]) as tmp_signed:
            tmp_signed_path = Path(tmp_signed.name)
            tmp_signed.write(uploaded_bytes)

        try:
            verification = verify_signed_pdf(tmp_signed_path)
        finally:
            tmp_signed_path.unlink(missing_ok=True)

        is_signed = bool(verification.get("is_signed"))
        is_valid = bool(verification.get("is_allowed_signature") or verification.get("is_szafir_signature"))
        if is_valid:
            self.storage.save_pdf(
                slug,
                signed_filename,
                uploaded_bytes,
                document_type=self._storage_document_type(document_id),
                signed=True,
            )
            current_app.logger.info("Upload podpisanego dokumentu do Nextcloud zakonczony sukcesem: %s", signed_filename)
            record_submission_file(
                submission_repository=self.submission_repository,
                submission_id=self._submission_id(submission),
                form_slug=slug,
                filename=signed_filename,
                storage=self.storage,
                file_bytes=uploaded_bytes,
                document_id=document_id,
                document_type=self._storage_document_type(document_id) or "",
                signed=True,
            )

        updates = self._signed_updates(
            row,
            document_id,
            signed_filename,
            verification,
            is_signed,
            is_valid,
            update_target,
        )
        self._update_submission(submission, updates)
        self._audit("SIGNED_DOCUMENT_UPLOADED", submission, metadata={"document_id": document_id, "filename": signed_filename})
        self._audit("SIGNATURE_VERIFIED" if is_valid else "SIGNATURE_INVALID", submission, metadata=verification)
        return {
            "source_filename": source_filename,
            "signed_filename": signed_filename if is_valid else "",
            "verification": verification,
            "is_signed": is_signed,
            "is_valid": is_valid,
            "updates": updates,
        }

    def build_download_url(self, submission: dict, filename: str, signed: bool = False) -> str:
        values = {"slug": self._slug(submission), "filename": filename}
        token = self.ensure_access_token(submission)
        if token:
            values["token"] = token
        endpoint = "documents.download_signed_pdf" if signed else "documents.download_pdf"
        return url_for(endpoint, **values)

    def ensure_access_token(self, submission: dict) -> str:
        row = self._row(submission)
        token = str(row.get("access_token") or "").strip()
        if token:
            return token
        token = self.access_token_service.generate_token()
        row["access_token"] = token
        if self.submission_repository and row.get("submission_id"):
            self.submission_repository.update(row["submission_id"], {"access_token": token})
        return token

    def verify_download_token(self, submission: dict, token: str | None) -> bool:
        expected = self.ensure_access_token(submission)
        if not expected:
            return False
        return self.access_token_service.verify_token({"access_token": expected}, token)

    def read_document_bytes_for_download(self, submission: dict, filename: str, *, signed: bool) -> bytes:
        clean_filename = Path(filename).name
        metadata = self._file_metadata(submission, clean_filename, signed=signed)
        if metadata and metadata.get("storage_path"):
            storage_path = str(metadata["storage_path"])
            if hasattr(self.storage, "read_bytes"):
                return self.storage.read_bytes(storage_path)
            if hasattr(self.storage, "get_file_bytes"):
                return self.storage.get_file_bytes(storage_path)

        current_app.logger.warning(
            "Legacy PDF lookup by filename used for submission=%s filename=%s.",
            self._submission_id(submission),
            clean_filename,
        )
        return self.storage.get_pdf_bytes(self._slug(submission), clean_filename)

    def build_documents_view(self, submission: dict, form_config: dict, available_actions: list[dict] | None = None) -> dict:
        row = self._row(submission)
        documents = []
        for document in self.get_documents_config(form_config):
            filename = self._document_filename(row, document.get("id", ""))
            documents.append(
                {
                    "id": document.get("id"),
                    "label": document.get("label"),
                    "filename": filename,
                    "url": self.build_download_url(submission, filename) if filename else "",
                    "signature_required": document.get("signature_required", True),
                    "signature_valid": self._document_signature_valid(row, document.get("id", "")),
                    "actions": [],
                }
            )
        return {
            "current_step": row.get("workflow_step") or "",
            "available_actions": available_actions or [],
            "documents": documents,
        }

    def _file_metadata(self, submission: dict, filename: str, *, signed: bool) -> dict | None:
        if not self.submission_repository or not hasattr(self.submission_repository, "get_file_metadata"):
            return None
        return self.submission_repository.get_file_metadata(
            self._submission_id(submission),
            filename,
            signed=signed,
        )

    def build_filename(self, pattern: str, submission: dict) -> str:
        fallback = f"{naming.sanitize_filename_part(submission.get('submission_id'), 'dokument')}.pdf"
        return naming.build_filename_from_pattern(pattern, submission, fallback)

    def build_filename_for_document(self, document: Mapping[str, Any], row: Mapping[str, Any], document_id: str) -> str:
        if document_id == DocumentType.DECLARATION:
            return naming.build_declaration_filename(row, document)
        if document_id in {DocumentType.AGREEMENT, DocumentType.TRAINING_AGREEMENT}:
            fallback = naming.build_agreement_filename(row, document)
            return naming.build_filename_from_pattern(normalize_text(document.get("filename_pattern")), row, fallback)
        return self.build_filename(normalize_text(document.get("filename_pattern")), dict(row))

    def get_document_by_id(self, form_config: dict, document_id: str) -> dict | None:
        for document in self.get_documents_config(form_config):
            if document.get("id") == document_id:
                return document
        return None

    def resolve_template_html(self, template_path: str) -> str | None:
        normalized_path = str(template_path or "").replace("\\", "/").strip().strip("/")
        if not normalized_path:
            return None
        forms_dir = current_app.config["NEXTCLOUD_FORMS_DIR"].strip("/")
        output_dir = current_app.config["NEXTCLOUD_OUTPUT_DIR"].strip("/")
        if not normalized_path.startswith((f"{forms_dir}/", f"{output_dir}/")):
            normalized_path = f"{forms_dir}/{normalized_path}"
        template_html = self.storage.read_text_or_empty(normalized_path)
        if not template_html.strip():
            raise RuntimeError(f"Nie znaleziono szablonu dokumentu w Nextcloud: {normalized_path}")
        return template_html

    def resolve_pdf_image_url(self, form_definition: dict) -> str | None:
        image_value = form_definition.get("header_image") or form_definition.get("logo_url")
        if not image_value:
            return None
        normalized = str(image_value).replace("\\", "/").lstrip("/")
        if normalized.startswith(("http://", "https://")):
            return normalized
        if normalized.startswith("static/"):
            normalized = normalized[len("static/"):]
        if normalized.startswith("assets/"):
            return request.url_root.rstrip("/") + "/" + normalized
        return request.url_root.rstrip("/") + "/static/" + normalized

    def build_document_number(
        self,
        document: Mapping[str, Any],
        *,
        submission_id: str,
        sequence: int,
        generated_date: str,
    ) -> str:
        numbering = document.get("numbering") or {}
        pattern = numbering.get("number_pattern") or "{submission_id}/{agreement_sequence}/{generated_date}"
        return pattern.format(
            submission_id=submission_id,
            agreement_sequence=sequence,
            generated_date=generated_date,
        )

    def _row(self, submission: dict) -> dict:
        return submission.get("row") if isinstance(submission.get("row"), dict) else submission

    def _slug(self, submission: dict) -> str:
        return str(submission.get("form_slug") or self._row(submission).get("form_slug") or "").strip()

    def _submission_id(self, submission: dict) -> str:
        return str(submission.get("submission_id") or self._row(submission).get("submission_id") or "").strip()

    def _update_submission(self, submission: dict, updates: dict) -> bool:
        row = self._row(submission)
        row.update(updates)
        submission_id = self._submission_id(submission)
        if self.submission_repository and submission_id:
            return self.submission_repository.update(submission_id, updates)
        return False

    def _audit(self, event_type: str, submission: dict, metadata: dict | None = None) -> None:
        if not self.audit_log_service:
            return
        self.audit_log_service.log_event(
            event_type,
            self._submission_id(submission),
            self._slug(submission),
            metadata=metadata or {},
        )

    def _storage_document_type(self, document_id: str) -> str | None:
        if document_id == DocumentType.DECLARATION:
            return "declaration"
        if document_id in {DocumentType.AGREEMENT, DocumentType.TRAINING_AGREEMENT}:
            return "agreement"
        return None

    def _not_required_updates(self, document_id: str, form_config: Mapping[str, Any]) -> dict[str, str]:
        if document_id == DocumentType.DECLARATION:
            agreement_document = self.get_document_by_id(dict(form_config), DocumentType.AGREEMENT)
            training_document = self.get_document_by_id(dict(form_config), DocumentType.TRAINING_AGREEMENT)
            agreement_enabled = bool(
                (agreement_document and agreement_document.get("enabled", True))
                or (training_document and training_document.get("enabled", True))
            )
            return {
                "declaration_required": "Nie",
                "declaration_generated": "Nie",
                "process_status": (
                    ProcessStatus.AGREEMENT_READY.value
                    if agreement_enabled
                    else ProcessStatus.PARTICIPANT_ACCEPTED.value
                ),
            }
        return {f"{document_id}_required": "Nie"}

    def _generated_updates(self, document_id: str, filename: str) -> dict[str, str]:
        if document_id == DocumentType.DECLARATION:
            return {
                "declaration_required": "Tak",
                "declaration_generated": "Tak",
                "declaration_filename": filename,
                "process_status": ProcessStatus.DECLARATION_WAITING_FOR_SIGNATURE.value,
            }
        if document_id in {DocumentType.AGREEMENT, DocumentType.TRAINING_AGREEMENT}:
            return {
                "agreement_required": "Tak",
                "agreement_generated": "Tak",
                "agreement_filename": filename,
                "process_status": ProcessStatus.AGREEMENT_WAITING_FOR_SIGNATURE.value,
            }
        return {
            f"{document_id}_generated": "Tak",
            f"{document_id}_filename": filename,
        }

    def _signed_document_target(self, row: dict, document_id: str, instance_id: str | None) -> tuple[str, str, dict | None]:
        if document_id == DocumentType.DECLARATION:
            source_filename = str(row.get("declaration_filename") or "").strip()
            if not source_filename:
                raise ValueError("Najpierw wygeneruj deklarację do podpisu.")
            return source_filename, build_signed_filename(source_filename), None

        agreements = parse_json_list(row.get("training_agreements"))
        if agreements and instance_id:
            agreement = next((item for item in agreements if str(item.get("id") or "") == str(instance_id)), None)
            if not agreement:
                raise ValueError("Nie znaleziono umowy dla wybranego szkolenia.")
            source_filename = agreement.get("filename") or f"{instance_id}-umowa.pdf"
            return source_filename, build_signed_filename(source_filename), {"agreements": agreements, "agreement": agreement}

        source_filename = str(row.get("agreement_filename") or "").strip()
        if not source_filename:
            raise ValueError("Najpierw wygeneruj umowę do podpisu.")
        return source_filename, build_signed_filename(source_filename), None

    def _signed_updates(
        self,
        row: dict,
        document_id: str,
        signed_filename: str,
        verification: dict,
        is_signed: bool,
        is_valid: bool,
        update_target: dict | None,
    ) -> dict[str, Any]:
        signature_type = verification.get("signature_type") or "unknown"
        signature_error = "" if is_valid else verification.get("reason", "Niepoprawny podpis dokumentu.")

        if document_id == DocumentType.DECLARATION:
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
                "training_agreements": serialize_json_list(agreements),
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

    def _document_filename(self, row: Mapping[str, Any], document_id: str) -> str:
        if document_id == DocumentType.DECLARATION:
            return str(row.get("declaration_filename") or "").strip()
        if document_id in {DocumentType.AGREEMENT, DocumentType.TRAINING_AGREEMENT}:
            return str(row.get("agreement_filename") or "").strip()
        return str(row.get(f"{document_id}_filename") or "").strip()

    def _document_signature_valid(self, row: Mapping[str, Any], document_id: str) -> bool:
        if document_id == DocumentType.DECLARATION:
            return str(row.get("declaration_signature_valid") or "").strip().lower() == "tak"
        if document_id in {DocumentType.AGREEMENT, DocumentType.TRAINING_AGREEMENT}:
            return str(row.get("agreement_signature_valid") or "").strip().lower() == "tak"
        return str(row.get(f"{document_id}_signature_valid") or "").strip().lower() == "tak"

    def _add_collection_context(self, context: dict, row: Mapping[str, Any]) -> None:
        selected_trainings = parse_json_list(row.get("selected_trainings"))
        context["selected_trainings"] = selected_trainings
        context["training_agreements"] = parse_json_list(row.get("training_agreements"))
        context["selected_trainings_total"] = sum(
            float(training.get("price") or 0)
            for training in selected_trainings
        )


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def is_enabled(value: Any) -> bool:
    normalized = normalize_text(value).lower()
    return normalized in {"true", "1", "yes", "tak"}


def sanitize_filename_part(value: Any, fallback: str = "dokument") -> str:
    text = normalize_text(value)

    if not text:
        text = fallback

    text = text.replace(" ", "_")
    text = FILENAME_SAFE_PATTERN.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("_")

    return text or fallback


def parse_json_list(value: str | list | None) -> list[dict]:
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


def serialize_json_list(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)


def build_signed_filename(filename: str) -> str:
    source = Path(filename)
    return f"{source.stem or 'dokument'}-signed{source.suffix or '.pdf'}"


def get_first_existing_value(row: Mapping[str, Any], field_names: list[str]) -> str:
    for field_name in field_names:
        value = normalize_text(row.get(field_name))

        if value:
            return value

    return ""


def get_participant_first_name(row: Mapping[str, Any]) -> str:
    return get_first_existing_value(
        row,
        [
            "first_name",
            "imie",
            "imiona",
            "imię",
            "Imię",
            "Imię (imiona)",
        ],
    )


def get_participant_last_name(row: Mapping[str, Any]) -> str:
    return get_first_existing_value(
        row,
        [
            "last_name",
            "nazwisko",
            "Nazwisko",
        ],
    )


def build_participant_name(row: Mapping[str, Any]) -> str:
    first_name = get_participant_first_name(row)
    last_name = get_participant_last_name(row)
    full_name = " ".join(part for part in [first_name, last_name] if part)

    return full_name or "Uczestnik"


def get_project_documents_config(form_definition: Mapping[str, Any]) -> dict:
    process = form_definition.get("process") or {}

    if not isinstance(process, Mapping):
        process = {}

    documents = process.get("documents") or form_definition.get("documents") or {}

    if not isinstance(documents, Mapping):
        documents = {}

    return dict(documents)


def get_document_config(form_definition: Mapping[str, Any], document_type: str) -> dict:
    documents = get_project_documents_config(form_definition)
    raw_config = documents.get(document_type) or {}

    if not isinstance(raw_config, Mapping):
        raw_config = {}

    config = {**DEFAULT_DOCUMENT_CONFIG, **dict(raw_config)}
    config["enabled"] = bool(config.get("enabled"))
    config["signature_required"] = bool(config.get("signature_required", True))

    return config


def is_document_enabled(form_definition: Mapping[str, Any], document_type: str) -> bool:
    return bool(get_document_config(form_definition, document_type).get("enabled"))


def build_filename_from_pattern(pattern: str, row: Mapping[str, Any], fallback: str) -> str:
    if not pattern:
        return fallback

    values = {
        "first_name": sanitize_filename_part(get_participant_first_name(row), "Imie"),
        "last_name": sanitize_filename_part(get_participant_last_name(row), "Nazwisko"),
        "participant_name": sanitize_filename_part(build_participant_name(row), "Uczestnik"),
        "submission_id": sanitize_filename_part(row.get("submission_id"), "wniosek"),
        "training_id": sanitize_filename_part(row.get("training_id"), "szkolenie"),
        "agreement_sequence": sanitize_filename_part(row.get("agreement_sequence"), "1"),
        "generated_date": sanitize_filename_part(row.get("generated_date") or row.get("agreement_generated_at"), "data"),
    }

    try:
        filename = pattern.format(**values)
    except KeyError:
        return fallback

    filename = sanitize_filename_part(filename.replace(".pdf", ""), Path(fallback).stem)

    return f"{filename}.pdf"


def build_declaration_filename(row: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> str:
    first_name = sanitize_filename_part(get_participant_first_name(row), "Imie")
    last_name = sanitize_filename_part(get_participant_last_name(row), "Nazwisko")
    fallback = f"{first_name}_{last_name}-deklaracja.pdf"

    return build_filename_from_pattern(normalize_text((config or {}).get("filename_pattern")), row, fallback)


def build_agreement_filename(row: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> str:
    first_name = sanitize_filename_part(get_participant_first_name(row), "Imie")
    last_name = sanitize_filename_part(get_participant_last_name(row), "Nazwisko")
    fallback = f"{first_name}_{last_name}-umowa.pdf"

    return build_filename_from_pattern(normalize_text((config or {}).get("filename_pattern")), row, fallback)


def build_document_pdf_context(
    *,
    form_definition: dict,
    submission_id: str,
    row: Mapping[str, Any],
    submission_view: list[dict],
    consents_view: list[dict],
    pdf_image_url: str | None,
    document_type: str,
) -> dict:
    return {
        **dict(row),
        "form_definition": form_definition,
        "submission_id": submission_id,
        "participant_name": build_participant_name(row),
        "submission": row,
        "submission_view": submission_view,
        "consents_view": consents_view,
        "pdf_image_url": pdf_image_url,
        "pdf_image_alt": form_definition.get("title", ""),
        "document_type": document_type,
        "generated_at": datetime.now().strftime("%d.%m.%Y"),
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "submission_date": row.get("created_at") or row.get("submission_date") or "",
        "project_name": form_definition.get("title", ""),
        "first_name": get_participant_first_name(row),
        "last_name": get_participant_last_name(row),
        "pesel": row.get("pesel", ""),
        "email": row.get("email", ""),
        "phone": row.get("phone") or row.get("telefon") or "",
    }


def remove_inline_logo_markup(template_html: str) -> str:
    """Remove logo blocks from the document body.

    Repeated logos are now rendered only by Playwright's PDF footer_template.
    Keeping HTML logo blocks causes an additional logo on the first page header.
    """

    cleaned = PDF_LOGO_FOOTER_PATTERN.sub("", template_html)
    cleaned = PDF_LOGO_HEADER_PATTERN.sub("", cleaned)
    cleaned = DOCUMENT_LOGO_HEADER_PATTERN.sub("", cleaned)
    cleaned = FORM_HEADER_IMAGE_PATTERN.sub("", cleaned)

    return cleaned


def prepare_document_template_html(template_html: str, context: Mapping[str, Any]) -> str:
    if normalize_text(context.get("pdf_image_url")):
        return remove_inline_logo_markup(template_html)

    return template_html


def generate_document_pdf_bytes(
    *,
    app: Flask,
    template_name: str,
    context: dict,
    template_html: str | None = None,
) -> bytes:
    with tempfile.NamedTemporaryFile(
        suffix=".pdf",
        delete=False,
        dir=app.config["TEMP_DIR"],
    ) as tmp_pdf:
        tmp_pdf_path = Path(tmp_pdf.name)

    try:
        if template_html:
            template_html = prepare_document_template_html(template_html, context)
            generate_pdf_from_html(
                app=app,
                template_html=template_html,
                context=context,
                output_path=tmp_pdf_path,
            )
        else:
            generate_pdf(
                app=app,
                template_name=template_name,
                context=context,
                output_path=tmp_pdf_path,
            )

        return tmp_pdf_path.read_bytes()
    finally:
        tmp_pdf_path.unlink(missing_ok=True)
