"""Confirm that a signed PDF still contains the selected stored revision.

The byte-prefix case covers PDF incremental signatures. A signer may instead
rewrite the PDF, so compare decoded page content and the objects that determine
its rendering. Unrecognised changes fail closed rather than trusting a valid
signature on an unrelated document.
"""
from __future__ import annotations

from io import BytesIO
from decimal import Decimal

from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, IndirectObject, NumberObject, StreamObject


class PdfIdentityUnconfirmed(ValueError):
    """The PDF cannot be compared safely with the stored source."""


def _canonical(value, seen: set[tuple[int, int]], depth: int = 0):
    if depth > 40:
        raise PdfIdentityUnconfirmed("PDF object graph is too deep")
    if isinstance(value, IndirectObject):
        key = (id(value.pdf), value.idnum)
        if key in seen:
            raise PdfIdentityUnconfirmed("PDF object graph contains a cycle")
        return _canonical(value.get_object(), seen | {key}, depth + 1)
    if isinstance(value, StreamObject):
        attributes = {str(k): v for k, v in value.items()
                      if str(k) not in {"/Length", "/Filter", "/DecodeParms", "/DL"}}
        return ("stream", _canonical(attributes, seen, depth + 1), value.get_data())
    if isinstance(value, (DictionaryObject, dict)):
        return ("dict", tuple((str(k), _canonical(v, seen, depth + 1))
                              for k, v in sorted(value.items(), key=lambda pair: str(pair[0]))))
    if isinstance(value, (ArrayObject, list, tuple)):
        return ("array", tuple(_canonical(v, seen, depth + 1) for v in value))
    if isinstance(value, bytes):
        return ("bytes", bytes(value))
    if isinstance(value, (int, float, NumberObject, FloatObject)) and not isinstance(value, bool):
        return ("number", Decimal(str(value)).normalize())
    if value is None:
        return ("null",)
    return (type(value).__name__, str(value))


def _signature_widget(annotation) -> bool:
    annotation = annotation.get_object()
    if str(annotation.get("/Subtype")) != "/Widget":
        return False
    return _signature_field(annotation)


def _signature_field(field) -> bool:
    field = field.get_object()
    for _ in range(10):
        if str(field.get("/FT")) == "/Sig":
            value = field.get("/V")
            if value is None:
                return True  # Empty signature field in the source revision.
            value = value.get_object()
            return isinstance(value, DictionaryObject) and "/ByteRange" in value and "/Contents" in value
        parent = field.get("/Parent")
        if parent is None:
            return False
        field = parent.get_object()
    raise PdfIdentityUnconfirmed("Signature field hierarchy is too deep")


def _ordinary_field(field, depth=0):
    if depth > 10:
        raise PdfIdentityUnconfirmed("Form field hierarchy is too deep")
    field = field.get_object()
    if _signature_field(field):
        return None
    kids = field.get("/Kids") or []
    if isinstance(kids, IndirectObject):
        kids = kids.get_object()
    ordinary_kids = tuple(value for kid in kids if (value := _ordinary_field(kid, depth + 1)) is not None)
    values = {k: v for k, v in field.items() if str(k) not in {"/Kids", "/Parent", "/P"}}
    if not values and not ordinary_kids:
        return None
    if set(map(str, values)) <= {"/T"} and not ordinary_kids:
        return None  # Parent that only grouped signature fields.
    return _canonical(values, set()), ordinary_kids


def _inherited_page_value(page, key):
    node = page
    for _ in range(40):
        if key in node:
            return node[key]
        parent = node.get("/Parent")
        if parent is None:
            return None
        node = parent.get_object()
    raise PdfIdentityUnconfirmed("Page tree is too deep")


def _document_identity(pdf_bytes: bytes):
    reader = PdfReader(BytesIO(pdf_bytes), strict=False)
    if reader.is_encrypted or not reader.pages:
        raise PdfIdentityUnconfirmed("Encrypted or empty PDF")
    root = reader.trailer["/Root"].get_object()
    # These entries are signature plumbing or producer metadata. All other
    # catalog entries (including actions and attachments) must remain equal.
    allowed_root_changes = {"/Pages", "/AcroForm", "/Perms", "/DSS", "/Metadata", "/Extensions", "/Version"}
    catalog = _canonical({k: v for k, v in root.items() if str(k) not in allowed_root_changes}, set())
    pages = []
    for page in reader.pages:
        annotations = page.get("/Annots") or []
        if isinstance(annotations, IndirectObject):
            annotations = annotations.get_object()
        other_annotations = [annotation for annotation in annotations if not _signature_widget(annotation)]
        inherited_keys = {"/Resources", "/MediaBox", "/CropBox", "/Rotate"}
        page_values = {k: v for k, v in page.items() if str(k) not in
                       {"/Parent", "/Annots", "/Contents", *inherited_keys}}
        effective_values = {key: _inherited_page_value(page, key) for key in inherited_keys}
        effective_values["/CropBox"] = effective_values["/CropBox"] or effective_values["/MediaBox"]
        effective_values["/Rotate"] = effective_values["/Rotate"] or 0
        effective_values["/Resources"] = effective_values["/Resources"] or {}
        contents = page.get_contents()
        pages.append((
            _canonical(page_values, set()),
            _canonical(effective_values, set()),
            _canonical(other_annotations, set()),
            contents.get_data() if contents is not None else b"",
        ))
    acroform = root.get("/AcroForm")
    fields = acroform.get_object().get("/Fields", []) if acroform else []
    if isinstance(fields, IndirectObject):
        fields = fields.get_object()
    other_fields = tuple(value for field in fields if (value := _ordinary_field(field)) is not None)
    return catalog, tuple(pages), other_fields


def compare_pdf_revision(source: bytes, signed: bytes) -> tuple[bool, str, str]:
    """Return (matches, reason_code, comparison_method), without exposing PII."""
    if not source or not signed:
        return False, "DOCUMENT_IDENTITY_UNCONFIRMED", ""
    if signed == source:
        return False, "DOCUMENT_REVISION_MISMATCH", ""
    try:
        matches = _document_identity(source) == _document_identity(signed)
    except Exception:
        return False, "DOCUMENT_IDENTITY_UNCONFIRMED", ""
    method = "incremental_bytes_and_pdf" if signed.startswith(source) else "canonical_pdf"
    return (True, "OK", method) if matches else (False, "DOCUMENT_REVISION_MISMATCH", "")
