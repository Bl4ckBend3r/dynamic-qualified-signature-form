"""Backward-compatible agreement adapter for the shared document builder."""

from services.documents.document_builder_service import (
    BUILDER_VERSION,
    DocumentBuilderValidationError,
    default_document_builder_document,
    normalize_document_builder_document,
    render_document_builder_template,
    validate_document_builder_document,
)


AgreementBuilderValidationError = DocumentBuilderValidationError


def default_agreement_builder_document():
    return default_document_builder_document("agreement")


def normalize_agreement_builder_document(value):
    return normalize_document_builder_document(value, "agreement")


def validate_agreement_builder_document(value, fields=()):
    return validate_document_builder_document(value, fields, "agreement")


def render_agreement_builder_template(value):
    return render_document_builder_template(value, "agreement")
