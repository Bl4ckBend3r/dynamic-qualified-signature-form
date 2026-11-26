"""Security regression tests for post-signature XMP metadata."""

import pytest
from pypdf.generic import DecodedStreamObject, NameObject

from services.documents.pdf_revision_service import (
    _allowed_new_xmp_metadata,
    _allowed_xmp_metadata_update,
)


XMP = b'''<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Test toolkit">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about=""
    xmlns:pdf="http://ns.adobe.com/pdf/1.3/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:mm="http://ns.adobe.com/xap/1.0/mm/"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
    <pdf:Producer>Test PDF producer</pdf:Producer>
    <xmp:CreatorTool>Test generator</xmp:CreatorTool>
    <xmp:CreateDate>2026-01-01T10:00:00Z</xmp:CreateDate>
    <xmp:ModifyDate>2026-01-01T10:01:00Z</xmp:ModifyDate>
    <xmp:MetadataDate>2026-01-01T10:01:00Z</xmp:MetadataDate>
    <mm:DocumentID>uuid:00000000-0000-4000-8000-000000000001</mm:DocumentID>
    <mm:InstanceID>uuid:00000000-0000-4000-8000-000000000002</mm:InstanceID>
    <dc:format>application/pdf</dc:format>
</rdf:Description>
</rdf:RDF>
</x:xmpmeta>'''


def _stream(raw=XMP, *, stream_type="/Metadata", subtype="/XML", extra=()):
    stream = DecodedStreamObject()
    stream.set_data(raw)
    if stream_type is not None:
        stream[NameObject("/Type")] = NameObject(stream_type)
    if subtype is not None:
        stream[NameObject("/Subtype")] = NameObject(subtype)
    for key, value in extra:
        stream[NameObject(key)] = NameObject(value)
    return stream


@pytest.mark.parametrize("raw", [
    XMP,
    b'<?xpacket begin=""?>\n' + XMP + b'\n<?xpacket end="w"?>',
], ids=["technical-xmp", "xpacket-wrapper"])
def test_new_xmp_accepts_technical_metadata(raw):
    assert _allowed_new_xmp_metadata(_stream(raw)) is True


@pytest.mark.parametrize("raw", [
    b"",
    XMP[:-15],
    XMP + b"<extra/>",
    b'<!DOCTYPE x:xmpmeta [<!ENTITY test "synthetic">]>\n' + XMP,
    b'<!DOCTYPE x:xmpmeta SYSTEM "https://example.invalid/xmp.dtd">\n' + XMP,
    b'<!DOCTYPE x:xmpmeta [<!ENTITY test SYSTEM "file:///nonexistent-xmp-test">]>\n'
        + XMP.replace(b"Test PDF producer", b"&test;"),
    b'<?xml-stylesheet href="https://example.invalid/style.xsl"?>\n' + XMP,
    XMP.replace(b"</rdf:Description>", b"<dc:title>Changed title</dc:title></rdf:Description>"),
    XMP.replace(b"</rdf:Description>", b"<dc:description>Changed description</dc:description></rdf:Description>"),
    XMP.replace(b"</rdf:Description>", b"<pdf:Producer>Duplicate</pdf:Producer></rdf:Description>"),
    XMP.replace(b"<pdf:Producer>", b'<pdf:Producer rdf:resource="https://example.invalid/data">'),
    XMP.replace(b'rdf:about=""', b'rdf:about="https://example.invalid/document"'),
    XMP.replace(b"<pdf:Producer>", b'<pdf:Producer xml:base="https://example.invalid/">'),
    XMP.replace(b"Test PDF producer", b"<rdf:Bag><rdf:li>Nested</rdf:li></rdf:Bag>"),
    XMP.replace(b"Test PDF producer", b""),
    XMP.replace(b"Test PDF producer", b"a" * 4097),
    XMP.replace(b"application/pdf", b"text/html"),
    XMP.replace(b"<rdf:RDF", b"<rdf:RDF unexpected=\"value\""),
    XMP.replace(b"</rdf:Description>", b"unexpected text</rdf:Description>"),
    XMP.replace(b"</rdf:Description>", b"<?unexpected test?></rdf:Description>"),
    XMP + b" " * 65536,
], ids=[
    "empty", "malformed", "multiple-roots", "internal-dtd", "external-dtd",
    "external-entity", "external-stylesheet", "unknown-title", "unknown-description",
    "duplicate-field", "rdf-resource", "external-about", "xml-base", "nested-value",
    "empty-value", "oversized-value", "wrong-format", "rdf-attribute",
    "unexpected-tail-text", "nested-processing-instruction", "oversized-packet",
])
def test_new_xmp_rejects_disallowed_xml(raw):
    assert _allowed_new_xmp_metadata(_stream(raw)) is False


@pytest.mark.parametrize("kwargs", [
    {"stream_type": None},
    {"stream_type": "/EmbeddedFile"},
    {"subtype": None},
    {"subtype": "/Image"},
    {"extra": (("/F", "/ExternalFile"),)},
    {"extra": (("/Filter", "/FlateDecode"),)},
    {"extra": (("/A", "/Unexpected"),)},
], ids=["missing-type", "wrong-type", "missing-subtype", "wrong-subtype",
        "external-stream", "unsupported-filter", "unexpected-stream-key"])
def test_new_xmp_rejects_disallowed_stream_dictionary(kwargs):
    assert _allowed_new_xmp_metadata(_stream(**kwargs)) is False


def test_existing_xmp_allows_only_bookkeeping_updates():
    changed = XMP.replace(b"2026-01-01T10:01:00Z", b"2026-01-01T10:02:00Z")
    changed = changed.replace(b"8000-000000000002", b"8000-000000000003")
    assert _allowed_xmp_metadata_update(_stream(), _stream(changed)) is True


@pytest.mark.parametrize("changed", [
    XMP.replace(b"Test PDF producer", b"Other PDF producer"),
    XMP.replace(b"Test generator", b"Other generator"),
    XMP.replace(b"8000-000000000001", b"8000-000000000004"),
    XMP.replace(b"2026-01-01T10:00:00Z", b"2026-01-02T10:00:00Z"),
    XMP.replace(b"</rdf:Description>", b"<dc:title>New title</dc:title></rdf:Description>"),
    XMP.replace(b"</rdf:Description>", b"<xmp:ModifyDate>duplicate</xmp:ModifyDate></rdf:Description>"),
    b'<!DOCTYPE x:xmpmeta [<!ENTITY test "synthetic">]>\n' + XMP,
], ids=["producer", "creator-tool", "document-id", "creation-date", "added-title", "duplicate-date", "dtd"])
def test_existing_xmp_rejects_other_changes(changed):
    assert _allowed_xmp_metadata_update(_stream(), _stream(changed)) is False
