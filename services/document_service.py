from __future__ import annotations

import re
import tempfile
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from flask import Flask, current_app, request, url_for

from form_loader import build_consents_view, build_submission_view
from signature_verifier import verify_signed_pdf
from services.access_token_service import AccessTokenService
from services.agreement_context_service import (
    build_training_agreement_value_context,
    upgrade_training_agreement_total_placeholder,
)
from services.documents.agreement_template_context_service import build_agreement_render_context
from services.documents.agreement_docx_template_service import parse_stored_agreement_docx
from services.documents.declaration_template_context_service import build_declaration_render_context
from services import document_naming_service as naming
from services.documents.document_storage_service import DocumentStorageService
from services.documents.document_view_service import DocumentViewService
from services.documents.pdf_render_service import (
    PdfRenderService,
    generate_document_pdf_bytes as render_document_pdf_bytes,
)
from services.documents.signed_document_service import SignedDocumentService
from services.file_metadata import resolve_pdf_storage_path
from services.process_service import ProcessStatus
from services.submission_document_service import SubmissionDocumentService, SubmissionDocumentType
from services.training_service import format_price_pln, parse_decimal_price, parse_training_snapshots
from services.upload_validation import UploadValidationError, validate_pdf_upload


FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ_-]+")
BODY_OPEN_PATTERN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def append_filename_sequence(filename: str, sequence: int) -> str:
    path = Path(filename)
    stem = path.stem
    if stem.casefold().endswith("-umowa"):
        stem = f"{stem[:-6]}-{sequence}-umowa"
    else:
        stem = f"{stem}-{sequence}"
    return f"{stem}{path.suffix or '.pdf'}"


def build_unique_collection_filenames(filenames: list[str]) -> list[str]:
    """Disambiguate duplicate PDF names without collapsing collection items."""
    normalized = [Path(filename).name for filename in filenames]
    counts = Counter(filename.casefold() for filename in normalized)
    used: set[str] = set()
    result: list[str] = []
    for sequence, filename in enumerate(normalized, start=1):
        candidate = filename
        if counts[filename.casefold()] > 1:
            candidate = append_filename_sequence(filename, sequence)
        collision = 2
        base_candidate = candidate
        while candidate.casefold() in used:
            path = Path(base_candidate)
            candidate = f"{path.stem}-{collision}{path.suffix or '.pdf'}"
            collision += 1
        used.add(candidate.casefold())
        result.append(candidate)
    return result


def build_available_storage_filename(
    *,
    storage_service: DocumentStorageService,
    storage,
    slug: str,
    filename: str,
    document_type: str | None,
    signed: bool = False,
) -> str:
    """Return a filename which does not overwrite an existing storage object."""
    base_filename = Path(filename).name
    if not hasattr(storage_service, "document_exists"):
        return base_filename
    candidate = base_filename
    sequence = 2
    while True:
        storage_path = resolve_pdf_storage_path(
            storage,
            slug,
            candidate,
            document_type=document_type,
            signed=signed,
        )
        if not storage_service.document_exists(
            storage=storage,
            slug=slug,
            filename=candidate,
            metadata={"storage_path": storage_path},
        ):
            return candidate
        candidate = append_filename_sequence(base_filename, sequence)
        sequence += 1


def build_unique_collection_numbers(numbers: list[str]) -> list[str]:
    """Ensure every agreement instance has a distinct public number."""
    normalized = [str(number or "").strip() for number in numbers]
    counts = Counter(number.casefold() for number in normalized)
    return [
        f"{number}/{sequence}" if counts[number.casefold()] > 1 else number
        for sequence, number in enumerate(normalized, start=1)
    ]


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
        pdf_render_service: PdfRenderService | None = None,
        document_storage_service: DocumentStorageService | None = None,
        signed_document_service: SignedDocumentService | None = None,
        document_view_service: DocumentViewService | None = None,
        submission_document_service: SubmissionDocumentService | None = None,
        strict_document_metadata_read: bool = False,
    ) -> None:
        self.storage = storage
        self.submission_repository = submission_repository
        self.audit_log_service = audit_log_service
        self.access_token_service = access_token_service or AccessTokenService()
        self.pdf_render_service = pdf_render_service or PdfRenderService()
        self.document_storage_service = document_storage_service or DocumentStorageService()
        self.signed_document_service = signed_document_service or SignedDocumentService()
        self.document_view_service = document_view_service or DocumentViewService()
        self.submission_document_service = submission_document_service or SubmissionDocumentService(
            submission_repository=submission_repository,
            storage=storage,
        )
        self.strict_document_metadata_read = strict_document_metadata_read

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
        resolved_context_extra = dict(context_extra or {})
        agreement_number = ""
        if document_id == DocumentType.AGREEMENT:
            generated_date = str(resolved_context_extra.get("generated_date") or date.today().isoformat())
            agreement_number = self.build_document_number(
                document,
                submission_id=submission_id,
                sequence=1,
                generated_date=generated_date,
            )
            resolved_context_extra.update(
                {
                    "generated_date": generated_date,
                    "agreement_generated_at": generated_date,
                    "agreement_sequence": 1,
                    "agreement_number": agreement_number,
                }
            )
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

        render_row = {**row, **resolved_context_extra}
        filename = self.build_filename_for_document(document, render_row, document_id)
        context = build_document_pdf_context(
            form_definition=form_config,
            submission_id=submission_id,
            row=render_row,
            submission_view=build_submission_view(form_config, render_row),
            consents_view=build_consents_view(form_config, render_row),
            pdf_image_url=self.resolve_pdf_image_url(form_config),
            document_type=document_id,
        )
        context.update(resolved_context_extra)
        self._add_collection_context(context, render_row)
        if document_id == DocumentType.DECLARATION:
            context = build_declaration_render_context(
                context,
                form_definition=form_config,
                fields=form_config.get("fields") or (),
            )
        document_bytes = self.pdf_render_service.render_document_pdf_bytes(
            app=current_app._get_current_object(),
            template_name="declaration_template.html",
            template_html=self.resolve_document_template(document),
            context=context,
        )
        storage_path = self.document_storage_service.save_pdf(
            storage=self.storage,
            slug=slug,
            filename=filename,
            document_bytes=document_bytes,
            document_type=self._storage_document_type(document_id),
            signed=False,
        )
        recorded = self.submission_document_service.record_generated_document(
            submission_id=submission_id,
            form_slug=slug,
            filename=filename,
            file_bytes=document_bytes,
            document_id=document_id,
            document_type=self._document_metadata_type(document_id, signed=False),
            agreement_number=agreement_number,
            storage_path=storage_path,
            storage=self.storage,
        )
        self._log_document_write(
            submission,
            filename=filename,
            document_type=self._document_metadata_type(document_id, signed=False),
            storage_path=storage_path,
            metadata_recorded=recorded,
        )
        self._require_metadata_record(recorded, filename)
        updates = self._generated_updates(document_id, filename)
        if document_id == DocumentType.AGREEMENT:
            updates["agreement_generated_at"] = resolved_context_extra["generated_date"]
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
        items = parse_training_snapshots(row.get(collection_field)) or normalize_selected_items(row.get(collection_field))
        if not items:
            raise RuntimeError("Nie wybrano elementów do wygenerowania dokumentów.")

        generated_date = (context_extra or {}).get("generated_date") or date.today().isoformat()
        template_html = self.resolve_document_template(document)
        if document_id in {DocumentType.AGREEMENT, DocumentType.TRAINING_AGREEMENT}:
            template_html = upgrade_training_agreement_total_placeholder(
                template_html,
                show_all_trainings_total=bool(document.get("show_all_trainings_total", True)),
            )
        generated_documents = []
        prepared_documents = []
        for sequence, item in enumerate(items, start=1):
            agreement_value_context = build_training_agreement_value_context(items, item)
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
                "training_price_formatted": item.get("price_formatted") or format_price_pln(item.get("price"), item.get("currency")),
                "agreement_sequence": sequence,
                "agreement_number": agreement_number,
                "generated_date": generated_date,
                "agreement_generated_at": generated_date,
                collection_field: [item],
                "selected_trainings": [item],
                "selected_trainings_normalized": [item],
                **agreement_value_context,
            }
            record = {
                "id": str(item_id),
                "training_id": str(item_id),
                "training_name": item.get("name", item.get("label", "")),
                "training_price": item.get("price", ""),
                "training_price_formatted": item.get("price_formatted") or format_price_pln(item.get("price"), item.get("currency")),
                "sequence": sequence,
                "number": agreement_number,
                "agreement_number": agreement_number,
                "generated_at": generated_date,
                "filename": "",
                "signed": False,
                "signature_valid": False,
                "signed_filename": "",
                "signature_type": "",
                "signature_error": "",
            }
            render_row.update(
                {
                    "training_agreement": record,
                    "agreement": record,
                    "training_agreements": [record],
                }
            )
            prepared_documents.append(
                {
                    "item_id": item_id,
                    "render_row": render_row,
                    "record": record,
                    "filename": self.build_filename_for_document(document, render_row, document_id),
                }
            )

        unique_filenames = build_unique_collection_filenames(
            [prepared["filename"] for prepared in prepared_documents]
        )
        unique_numbers = build_unique_collection_numbers(
            [prepared["record"]["agreement_number"] for prepared in prepared_documents]
        )
        for prepared, filename, agreement_number in zip(
            prepared_documents,
            unique_filenames,
            unique_numbers,
            strict=True,
        ):
            item_id = prepared["item_id"]
            render_row = prepared["render_row"]
            record = prepared["record"]
            filename = build_available_storage_filename(
                storage_service=self.document_storage_service,
                storage=self.storage,
                slug=slug,
                filename=filename,
                document_type=self._storage_document_type(document_id),
                signed=False,
            )
            record["filename"] = filename
            record["number"] = agreement_number
            record["agreement_number"] = agreement_number
            render_row["agreement_number"] = agreement_number
            render_row["agreement_filename"] = filename
            context = build_document_pdf_context(
                form_definition=form_config,
                submission_id=submission_id,
                row=render_row,
                submission_view=build_submission_view(form_config, render_row),
                consents_view=build_consents_view(form_config, render_row),
                pdf_image_url=self.resolve_pdf_image_url(form_config),
                document_type=document_id,
            )
            context.update(render_row)
            self._add_collection_context(context, render_row)
            context = build_agreement_render_context(
                context,
                form_definition=form_config,
                training=render_row.get("training") or item,
                all_trainings=render_row.get("all_selected_trainings") or [item],
            )
            document_bytes = self.pdf_render_service.render_document_pdf_bytes(
                app=current_app._get_current_object(),
                template_name="declaration_template.html",
                template_html=template_html,
                context=context,
            )
            storage_path = self.document_storage_service.save_pdf(
                storage=self.storage,
                slug=slug,
                filename=filename,
                document_bytes=document_bytes,
                document_type=self._storage_document_type(document_id),
                signed=False,
            )
            recorded = self.submission_document_service.record_generated_document(
                submission_id=submission_id,
                form_slug=slug,
                filename=filename,
                file_bytes=document_bytes,
                document_id=document_id,
                document_type=self._document_metadata_type(document_id, signed=False),
                agreement_number=record["agreement_number"],
                training_key=str(item_id),
                storage_path=storage_path,
                storage=self.storage,
            )
            self._log_document_write(
                submission,
                filename=filename,
                document_type=self._document_metadata_type(document_id, signed=False),
                storage_path=storage_path,
                metadata_recorded=recorded,
            )
            self._require_metadata_record(recorded, filename)
            generated_documents.append(record)

        updates = {
            "agreement_generated": "Tak",
            "agreement_filename": generated_documents[0]["filename"] if generated_documents else "",
            "agreement_generated_at": generated_date,
            "training_agreements": serialize_json_list(generated_documents),
            "process_status": ProcessStatus.AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE.value,
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
        previous_status = str(row.get("process_status") or "")
        slug = self._slug(submission)
        uploaded_bytes = uploaded_file.read()
        self.signed_document_service.validate_pdf_bytes(uploaded_bytes)
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
            storage_path = self.document_storage_service.save_pdf(
                storage=self.storage,
                slug=slug,
                filename=signed_filename,
                document_bytes=uploaded_bytes,
                document_type=self._storage_document_type(document_id),
                signed=True,
            )
            recorded = self.submission_document_service.record_signed_document(
                submission_id=self._submission_id(submission),
                form_slug=slug,
                filename=signed_filename,
                file_bytes=uploaded_bytes,
                document_id=document_id,
                document_type=self._document_metadata_type(document_id, signed=True),
                original_filename=str(uploaded_file.filename or ""),
                signature_status="valid" if is_valid else "invalid",
                signature_validation_result=verification,
                training_key=str(instance_id or ""),
                storage_path=storage_path,
                storage=self.storage,
            )
            self._log_document_write(
                submission,
                filename=signed_filename,
                document_type=self._document_metadata_type(document_id, signed=True),
                storage_path=storage_path,
                metadata_recorded=recorded,
            )
            self._require_metadata_record(recorded, signed_filename)

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
        new_status = str(updates.get("process_status") or previous_status)
        if is_valid and new_status != previous_status:
            self._record_document_workflow_event(
                submission,
                previous_status=previous_status,
                new_status=new_status,
                document_id=document_id,
            )
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

    def build_download_url(
        self,
        submission: dict,
        filename: str,
        signed: bool = False,
        *,
        access_token: str | None = None,
    ) -> str:
        values = {"slug": self._slug(submission), "filename": filename}
        token = str(access_token or "").strip() or self.ensure_access_token(submission)
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
        return self.document_storage_service.read_document_bytes(
            storage=self.storage,
            slug=self._slug(submission),
            filename=clean_filename,
            metadata=metadata,
            submission_id=self._submission_id(submission),
            strict_metadata=self.strict_document_metadata_read,
        )

    def build_documents_view(self, submission: dict, form_config: dict, available_actions: list[dict] | None = None) -> dict:
        row = self._row(submission)
        document_files = self.available_document_files(submission)
        view = self.document_view_service.build_documents_view(
            row=row,
            documents_config=self.get_documents_config(form_config),
            download_url_builder=lambda filename, signed=False: self.build_download_url(submission, filename, signed=signed),
            available_actions=available_actions,
            document_files=document_files,
            allow_legacy_fallback=not getattr(self.submission_repository, "supports_file_metadata", False),
        )
        view["available_filenames"] = {
            str(item.get("filename") or "") for item in document_files if item.get("filename")
        }
        return view

    def available_document_files(self, submission: dict) -> list[dict]:
        self.submission_document_service.backfill_existing_legacy_documents(submission)
        if getattr(self.submission_repository, "supports_file_metadata", False):
            return self.submission_document_service.list_available_documents(submission)

        available = []
        for candidate in self.submission_document_service.sync_from_legacy_fields(submission):
            if self.document_storage_service.document_exists(
                storage=self.storage,
                slug=self._slug(submission),
                filename=str(candidate.get("filename") or ""),
                metadata=None,
            ):
                available.append({**candidate, "storage_exists": True})
        return available

    def _file_metadata(self, submission: dict, filename: str, *, signed: bool) -> dict | None:
        submission_id = self._submission_id(submission)
        inferred_type = self._infer_document_type_for_filename(self._row(submission), filename, signed=signed)
        if inferred_type:
            metadata = self.submission_document_service.get_document_file(
                submission_id,
                document_type=inferred_type,
                filename=filename,
            )
            if metadata:
                return metadata
        if not self.submission_repository or not hasattr(self.submission_repository, "get_file_metadata"):
            return None
        return self.submission_repository.get_file_metadata(submission_id, filename, signed=signed)

    def _infer_document_type_for_filename(self, row: Mapping[str, Any], filename: str, *, signed: bool) -> str:
        clean_filename = Path(filename).name
        if clean_filename == str(row.get("pdf_filename") or ""):
            return self._document_metadata_type("form_submission", signed=False)
        if clean_filename == str(row.get("signed_pdf_filename") or ""):
            return self._document_metadata_type("form_submission", signed=True)
        if clean_filename == str(row.get("declaration_filename") or ""):
            return self._document_metadata_type(DocumentType.DECLARATION, signed=False)
        if clean_filename == str(row.get("declaration_signed_filename") or ""):
            return self._document_metadata_type(DocumentType.DECLARATION, signed=True)
        if clean_filename == str(row.get("agreement_filename") or ""):
            return self._document_metadata_type(DocumentType.AGREEMENT, signed=False)
        if clean_filename == str(row.get("agreement_signed_filename") or ""):
            return self._document_metadata_type(DocumentType.AGREEMENT, signed=True)
        for agreement in parse_json_list(row.get("training_agreements")):
            if clean_filename == str(agreement.get("filename") or ""):
                return self._document_metadata_type(DocumentType.TRAINING_AGREEMENT, signed=False)
            if clean_filename == str(agreement.get("signed_filename") or ""):
                return self._document_metadata_type(DocumentType.TRAINING_AGREEMENT, signed=True)
        return ""

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

    def resolve_document_template(self, document: Mapping[str, Any]) -> str:
        if document.get("template_source") == "builder":
            from services.documents.document_builder_service import render_document_builder_template

            return render_document_builder_template(
                document.get("builder_document") or {},
                str(document.get("id") or "agreement"),
            )
        if document.get("template_source") == "docx":
            try:
                return parse_stored_agreement_docx(self.storage, document.get("template_metadata") or {}).html
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
        inline_template = str(document.get("template_html") or "").strip()
        if inline_template:
            return inline_template
        template_path = str(document.get("template") or "").strip()
        if template_path:
            resolved = self.resolve_template_html(template_path)
            if resolved:
                return resolved
        document_id = str(document.get("id") or "")
        if document_id in {DocumentType.AGREEMENT, DocumentType.TRAINING_AGREEMENT}:
            raise RuntimeError("Brak szablonu umowy dla tego formularza.")
        raise RuntimeError("Brak szablonu dokumentu dla tego formularza.")

    def resolve_pdf_image_url(self, form_definition: dict) -> str | None:
        image_value = form_definition.get("logo_url") or form_definition.get("header_image")
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
        from services.documents.document_generation_service import build_document_number

        return build_document_number(
            document,
            submission_id=submission_id,
            sequence=sequence,
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

    def _record_document_workflow_event(
        self,
        submission: dict,
        *,
        previous_status: str,
        new_status: str,
        document_id: str,
    ) -> None:
        if not self.submission_repository or not hasattr(self.submission_repository, "record_workflow_event"):
            return
        source = "declaration_uploaded" if document_id == DocumentType.DECLARATION else "agreement_uploaded"
        try:
            self.submission_repository.record_workflow_event(
                self._submission_id(submission),
                {
                    "previous_status": previous_status,
                    "new_status": new_status,
                    "previous_step": "",
                    "new_step": source,
                    "actor_role": "participant",
                    "reason": source,
                    "source": source,
                },
            )
        except Exception:
            current_app.logger.warning(
                "Nie udalo sie zapisac historii uploadu dokumentu submission_id=%s source=%s.",
                self._submission_id(submission),
                source,
                exc_info=True,
            )

    def _audit(self, event_type: str, submission: dict, metadata: dict | None = None) -> None:
        if not self.audit_log_service:
            return
        self.audit_log_service.log_event(
            event_type,
            self._submission_id(submission),
            self._slug(submission),
            metadata=metadata or {},
        )

    def _require_metadata_record(self, recorded: bool, filename: str) -> None:
        if getattr(self.submission_repository, "supports_file_metadata", False) and not recorded:
            raise RuntimeError(f"Nie udalo sie zapisac metadanych wygenerowanego dokumentu: {filename}")

    def _log_document_write(
        self,
        submission: dict,
        *,
        filename: str,
        document_type: str,
        storage_path: str,
        metadata_recorded: bool,
    ) -> None:
        row = self._row(submission)
        current_app.logger.info(
            "Document generated public_submission_id=%s internal_submission_id=%s filename=%s "
            "document_type=%s storage_path=%s file_saved=%s submission_file_created=%s.",
            self._submission_id(submission),
            row.get("id", ""),
            filename,
            document_type,
            storage_path,
            True,
            metadata_recorded,
        )

    def _storage_document_type(self, document_id: str) -> str | None:
        if document_id == DocumentType.DECLARATION:
            return "declaration"
        if document_id in {DocumentType.AGREEMENT, DocumentType.TRAINING_AGREEMENT}:
            return "agreement"
        return None

    def _document_metadata_type(self, document_id: str, *, signed: bool) -> str:
        if document_id == DocumentType.DECLARATION:
            return SubmissionDocumentType.SIGNED_DECLARATION if signed else SubmissionDocumentType.DECLARATION
        if document_id == DocumentType.TRAINING_AGREEMENT:
            return (
                SubmissionDocumentType.SIGNED_TRAINING_AGREEMENT
                if signed
                else SubmissionDocumentType.TRAINING_AGREEMENT
            )
        if document_id == DocumentType.AGREEMENT:
            return SubmissionDocumentType.SIGNED_AGREEMENT if signed else SubmissionDocumentType.AGREEMENT
        return SubmissionDocumentType.SIGNED_FORM_PDF if signed else SubmissionDocumentType.FORM_PDF

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
                "process_status": ProcessStatus.AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE.value,
            }
        return {
            f"{document_id}_generated": "Tak",
            f"{document_id}_filename": filename,
        }

    def _signed_document_target(self, row: dict, document_id: str, instance_id: str | None) -> tuple[str, str, dict | None]:
        return self.signed_document_service.build_target(row, document_id, instance_id)

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
        return self.signed_document_service.build_updates(
            row,
            document_id,
            signed_filename,
            verification,
            is_signed,
            is_valid,
            update_target,
        )

    def _add_collection_context(self, context: dict, row: Mapping[str, Any]) -> None:
        selected_trainings = parse_training_snapshots(row.get("selected_trainings")) or normalize_selected_items(row.get("selected_trainings"))
        context["selected_trainings"] = selected_trainings
        context["selected_trainings_normalized"] = selected_trainings
        context["training_agreements"] = normalize_selected_items(row.get("training_agreements"))
        context["selected_trainings_total"] = sum(
            (parse_decimal_price(training.get("price")) or 0 for training in selected_trainings),
            parse_decimal_price("0") or 0,
        )
        context["selected_trainings_total_formatted"] = format_price_pln(context["selected_trainings_total"])


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


def normalize_selected_items(value: Any) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return [normalize_selected_item(raw)]
        return normalize_selected_items(decoded)
    if isinstance(value, Mapping):
        return [normalize_selected_item(value)]
    if isinstance(value, list):
        normalized = []
        for item in value:
            normalized_item = normalize_selected_item(item)
            if normalized_item:
                normalized.append(normalized_item)
        return normalized
    return [normalize_selected_item(str(value))]


def normalize_selected_item(item: Any) -> dict:
    if item is None:
        return {}
    if isinstance(item, Mapping):
        normalized = dict(item)
        fallback = first_non_empty(
            normalized.get("name"),
            normalized.get("label"),
            normalized.get("value"),
            normalized.get("id"),
        )
    else:
        fallback = str(item or "").strip()
        normalized = {}
    if not fallback:
        return {}
    normalized.setdefault("name", fallback)
    normalized.setdefault("label", fallback)
    normalized.setdefault("value", fallback)
    return normalized


def first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def parse_json_list(value: str | list | None) -> list[dict]:
    return normalize_selected_items(value)


def serialize_json_list(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)


def build_signed_filename(filename: str) -> str:
    return SignedDocumentService.build_signed_filename(filename)


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

    if isinstance(documents, list):
        return {
            str(document.get("id") or ""): dict(document)
            for document in documents
            if isinstance(document, Mapping) and document.get("id")
        }

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
        "training_name": sanitize_filename_part(row.get("training_name"), "szkolenie"),
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
    selected_trainings = parse_training_snapshots(row.get("selected_trainings"))
    selected_trainings_total = sum(
        (parse_decimal_price(training.get("price")) or 0 for training in selected_trainings),
        parse_decimal_price("0") or 0,
    )
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
        "pdf_image_alignment": form_definition.get("logo_alignment", "left"),
        "pdf_image_width": form_definition.get("logo_width"),
        "pdf_image_is_logo": bool(form_definition.get("logo_url")),
        "document_type": document_type,
        "selected_trainings": selected_trainings,
        "selected_trainings_total": selected_trainings_total,
        "selected_trainings_total_formatted": format_price_pln(selected_trainings_total),
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
    from services.documents.pdf_render_service import remove_inline_logo_markup as service_remove_inline_logo_markup

    return service_remove_inline_logo_markup(template_html)


def prepare_document_template_html(template_html: str, context: Mapping[str, Any]) -> str:
    from services.documents.pdf_render_service import prepare_document_template_html as service_prepare_document_template_html

    return service_prepare_document_template_html(template_html, context)


def generate_document_pdf_bytes(
    *,
    app: Flask,
    template_name: str,
    context: dict,
    template_html: str | None = None,
) -> bytes:
    return render_document_pdf_bytes(
        app=app,
        template_name=template_name,
        template_html=template_html,
        context=context,
    )
