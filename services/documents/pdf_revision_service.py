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
from lxml import etree

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



def allowed_profile_post_signature_update(
    signed_revision: bytes,
    final_pdf: bytes,
) -> bool:
    """
    Dopuszcza szczególny wariant aktualizacji Profilu Zaufanego:
    DSS/LTV oraz dodanie /DA i /DR bez modyfikowania pól,
    zawartości stron i pozostałej struktury dokumentu.
    """
    matches, _, _ = compare_pdf_revision(signed_revision, final_pdf)
    if not matches:
        import logging
        logging.getLogger(__name__).warning('pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition=not matches')
        return False

    try:
        before = PdfReader(BytesIO(signed_revision), strict=False)
        after = PdfReader(BytesIO(final_pdf), strict=False)

        old_root = before.trailer["/Root"].get_object()
        new_root = after.trailer["/Root"].get_object()

        old_keys = set(map(str, old_root.keys()))
        new_keys = set(map(str, new_root.keys()))

        if old_keys - new_keys:
            import logging
            logging.getLogger(__name__).warning('pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition=old_keys - new_keys')
            return False

        allowed_root_additions = {
            "/DSS", "/Extensions", "/Version", "/Metadata"
        }

        if "/Metadata" in new_keys - old_keys:
            if not _allowed_new_xmp_metadata(
                new_root["/Metadata"].get_object()
            ):
                return False

        if not (new_keys - old_keys) <= allowed_root_additions:
            import logging
            logging.getLogger(__name__).warning('pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition=not new_keys - old_keys <= allowed_root_additions')
            return False

        # Ta szczególna ścieżka wymaga rzeczywistego
        # dopisania danych DSS.
        if "/DSS" not in new_root or "/DSS" in old_root:
            import logging
            logging.getLogger(__name__).warning("pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition='/DSS' not in new_root or '/DSS' in old_root")
            return False

        if "/AcroForm" not in old_root or "/AcroForm" not in new_root:
            import logging
            logging.getLogger(__name__).warning("pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition='/AcroForm' not in old_root or '/AcroForm' not in new_root")
            return False

        old_form = old_root["/AcroForm"].get_object()
        new_form = new_root["/AcroForm"].get_object()

        old_form_keys = set(map(str, old_form.keys()))
        new_form_keys = set(map(str, new_form.keys()))

        if old_form_keys - new_form_keys:
            import logging
            logging.getLogger(__name__).warning('pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition=old_form_keys - new_form_keys')
            return False

        if not (new_form_keys - old_form_keys) <= {"/DA", "/DR"}:
            import logging
            logging.getLogger(__name__).warning("pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition=not new_form_keys - old_form_keys <= {'/DA', '/DR'}")
            return False

        # Nie dopuszczamy zmiany istniejących ustawień formularza.
        for key in old_form_keys - {"/Fields"}:
            if _canonical(old_form[key], set()) != _canonical(
                new_form[key], set()
            ):
                import logging
                logging.getLogger(__name__).warning('pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition=_canonical(old_form[key], set()) != _canonical(new_form[key], set())')
                return False

        # Zmiana domyślnego wyglądu mogłaby wpływać na
        # niepodpisane pola formularza.
        for field in old_form.get("/Fields", []):
            if _ordinary_field(field) is not None:
                import logging
                logging.getLogger(__name__).warning('pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition=_ordinary_field(field) is not None')
                return False

        # Istniejące metadane i parametry bezpieczeństwa
        # również muszą pozostać identyczne.
        sensitive_keys = {
            "/Metadata", "/Perms", "/Extensions",
            "/Version", "/DSS",
        }
        
        for key in old_keys & sensitive_keys:
            if key == "/Metadata":
                if not _allowed_xmp_metadata_update(
                    old_root[key].get_object(),
                    new_root[key].get_object(),
                ):
                    import logging
                    logging.getLogger(__name__).warning('pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition=not _allowed_xmp_metadata_update(old_root[key].get_object(), new_root[key].get_object())')
                    return False
            elif _canonical(old_root[key], set()) != _canonical(
                new_root[key], set()
            ):
                import logging
                logging.getLogger(__name__).warning('pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition=_canonical(old_root[key], set()) != _canonical(new_root[key], set())')
                return False


        return True

    except Exception:
        import logging
        logging.getLogger(__name__).warning('pdf_revision_rejected_detail function=allowed_profile_post_signature_update condition=exception_or_validation_failure')
        return False



def _allowed_new_xmp_metadata(stream) -> bool:
    """Validate newly added technical XMP metadata."""
    try:
        if str(stream.get("/Type")) != "/Metadata":
            return False

        if str(stream.get("/Subtype")) != "/XML":
            return False

        if set(map(str, stream.keys())) - {
            "/Type", "/Subtype", "/Length"
        }:
            return False

        raw = stream.get_data()

        if not raw or len(raw) > 65536:
            return False

        parser = etree.XMLParser(
            resolve_entities=False,
            load_dtd=False,
            no_network=True,
            recover=False,
        )

        document = etree.parse(BytesIO(raw), parser)

        if document.docinfo.doctype:
            return False

        for pi in document.xpath("/processing-instruction()"):
            if pi.target != "xpacket":
                return False

        xmp = "{adobe:ns:meta/}"
        rdf = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
        xap = "{http://ns.adobe.com/xap/1.0/}"
        mm = "{http://ns.adobe.com/xap/1.0/mm/}"
        pdf = "{http://ns.adobe.com/pdf/1.3/}"
        dc = "{http://purl.org/dc/elements/1.1/}"

        allowed_tags = {
            pdf + "Producer",
            mm + "DocumentID",
            mm + "InstanceID",
            xap + "CreateDate",
            xap + "CreatorTool",
            xap + "MetadataDate",
            xap + "ModifyDate",
            dc + "format",
        }

        def clean_container(element):
            return (
                not (element.text or "").strip()
                and all(
                    not (child.tail or "").strip()
                    for child in element
                )
            )

        root = document.getroot()

        if root.tag != xmp + "xmpmeta":
            return False

        if set(root.attrib) - {xmp + "xmptk"}:
            return False

        if len(root) != 1 or not clean_container(root):
            return False

        rdf_root = root[0]

        if rdf_root.tag != rdf + "RDF":
            return False

        if rdf_root.attrib or not clean_container(rdf_root):
            return False

        seen = set()

        for description in rdf_root:
            if description.tag != rdf + "Description":
                return False

            if set(description.attrib) - {rdf + "about"}:
                return False

            if description.get(rdf + "about", "") != "":
                return False

            if not clean_container(description):
                return False

            for element in description:
                if element.tag not in allowed_tags:
                    return False

                if element.tag in seen:
                    return False

                if len(element) or element.attrib:
                    return False

                value = (element.text or "").strip()

                if not value or len(value) > 4096:
                    return False

                if element.tag == dc + "format":
                    if value != "application/pdf":
                        return False

                seen.add(element.tag)

        return bool(seen)

    except Exception:
        return False


def _allowed_xmp_metadata_update(old_stream, new_stream) -> bool:
    """Allow only known XMP bookkeeping changes after signing."""

    ignored_stream_keys = {
        "/Length", "/Filter", "/DecodeParms", "/DL"
    }

    old_attributes = {
        str(k): _canonical(v, set())
        for k, v in old_stream.items()
        if str(k) not in ignored_stream_keys
    }
    new_attributes = {
        str(k): _canonical(v, set())
        for k, v in new_stream.items()
        if str(k) not in ignored_stream_keys
    }

    if old_attributes != new_attributes:
        return False

    allowed_tags = {
        "{http://ns.adobe.com/xap/1.0/}ModifyDate",
        "{http://ns.adobe.com/xap/1.0/}MetadataDate",
        "{http://ns.adobe.com/xap/1.0/mm/}InstanceID",
    }

    def normalized(stream):
        raw = stream.get_data()

        if b"<!DOCTYPE" in raw.upper():
            return None

        parser = etree.XMLParser(
            resolve_entities=False,
            load_dtd=False,
            no_network=True,
        )
        document = etree.parse(BytesIO(raw), parser)

        counts = {tag: 0 for tag in allowed_tags}

        for element in document.getroot().iter():
            if element.tag not in allowed_tags:
                continue

            if len(element) or element.attrib or element.text is None:
                return None

            counts[element.tag] += 1
            element.text = "__ALLOWED_XMP_VALUE__"

        if any(count != 1 for count in counts.values()):
            return None

        return etree.tostring(
            document,
            method="c14n",
            with_comments=True,
        )

    try:
        old_value = normalized(old_stream)
        new_value = normalized(new_stream)
        return old_value is not None and old_value == new_value
    except Exception:
        return False
