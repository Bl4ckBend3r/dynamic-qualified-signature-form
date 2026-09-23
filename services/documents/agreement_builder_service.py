"""Backward-compatible agreement adapter for the shared document builder."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from services.documents.document_builder_service import (
    BUILDER_VERSION,
    DocumentBuilderValidationError,
    default_document_builder_document,
    normalize_document_builder_document,
    render_document_builder_template,
    validate_document_builder_document,
)


AgreementBuilderValidationError = DocumentBuilderValidationError

AGREEMENT_DOCUMENT_CONTEXT_ADMIN_DRAFT = "admin_draft"
AGREEMENT_DOCUMENT_CONTEXT_VERSIONED_RUNTIME = "versioned_runtime"


@dataclass(frozen=True)
class AgreementDocumentConfig:
    source: str
    builder_document: Mapping[str, Any] | None = None
    template_metadata: Mapping[str, Any] | None = None
    template_html: str = ""


def resolve_agreement_document_config(
    workflow: Mapping[str, Any] | None,
    *,
    context: str,
) -> AgreementDocumentConfig:
    """Select one agreement source without falling through to another mode."""
    workflow = workflow or {}
    source = str(workflow.get("contract_template_source") or "").strip().casefold()
    metadata = workflow.get("contract_docx_template")
    if not source:
        source = (
            "html"
            if str(workflow.get("contract_template_html") or "").strip()
            else "docx"
            if isinstance(metadata, Mapping) and metadata
            else "builder"
        )
    if source not in {"builder", "docx", "html"}:
        raise ValueError(f"Nieobsługiwane źródło szablonu umowy: {source}.")

    if source == "builder":
        if context == AGREEMENT_DOCUMENT_CONTEXT_ADMIN_DRAFT:
            document = workflow.get("contract_builder_document")
        elif context == AGREEMENT_DOCUMENT_CONTEXT_VERSIONED_RUNTIME:
            # Old version snapshots predate the separate active builder payload.
            document = workflow.get("contract_builder_active_document")
            if not isinstance(document, Mapping):
                document = workflow.get("contract_builder_document")
        else:
            raise ValueError(f"Nieobsługiwany kontekst szablonu umowy: {context}.")
        return AgreementDocumentConfig(
            source=source,
            builder_document=document if isinstance(document, Mapping) else None,
        )

    if context not in {
        AGREEMENT_DOCUMENT_CONTEXT_ADMIN_DRAFT,
        AGREEMENT_DOCUMENT_CONTEXT_VERSIONED_RUNTIME,
    }:
        raise ValueError(f"Nieobsługiwany kontekst szablonu umowy: {context}.")
    if source == "docx":
        return AgreementDocumentConfig(
            source=source,
            template_metadata=metadata if isinstance(metadata, Mapping) else {},
        )
    return AgreementDocumentConfig(
        source=source,
        template_html=str(workflow.get("contract_template_html") or "").strip(),
    )


def agreement_builder_document_hash(document: Mapping[str, Any] | None) -> str:
    """Return a stable diagnostic hash without exposing agreement contents."""
    canonical = json.dumps(
        document if isinstance(document, Mapping) else None,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def default_agreement_builder_document():
    return default_document_builder_document("agreement")


def normalize_agreement_builder_document(value):
    return normalize_document_builder_document(value, "agreement")


def validate_agreement_builder_document(value, fields=()):
    return validate_document_builder_document(value, fields, "agreement")


def render_agreement_builder_template(value):
    return render_document_builder_template(value, "agreement")
