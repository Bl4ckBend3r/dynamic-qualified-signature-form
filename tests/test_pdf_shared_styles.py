from types import SimpleNamespace
from pathlib import Path

from pdf_generator import ensure_document_root, inject_pdf_styles


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_shared_document_css_is_injected_into_admin_html_template(tmp_path):
    static_dir = tmp_path / "static" / "css"
    static_dir.mkdir(parents=True)
    (static_dir / "document_template.css").write_text(".document-table { width: 100%; }", encoding="utf-8")
    app = SimpleNamespace(root_path=str(tmp_path))

    html = inject_pdf_styles(app, "<html><HEAD></HEAD><body><p>Umowa</p></body></html>")

    assert ".document-table { width: 100%; }" in html
    assert 'class="document"' in html
    assert html.index("<style>") < html.lower().index("</head>")


def test_html_fragment_is_wrapped_in_document_root():
    assert ensure_document_root("<p>Deklaracja</p>") == '<main class="document"><p>Deklaracja</p></main>'


def test_existing_body_class_is_preserved_and_extended():
    html = ensure_document_root('<html><body class="agreement custom"><p>Umowa</p></body></html>')

    assert 'class="agreement custom document"' in html


def test_agreement_document_styles_keep_formal_a4_structure_with_bounded_assets():
    css = (PROJECT_ROOT / "static" / "css" / "document_template.css").read_text(encoding="utf-8")

    assert "size: A4;" in css
    assert "font-size: 10.5pt;" in css
    assert ".document-section-number" in css
    assert ".document-section-title" in css
    assert ".document-list__item" in css
    assert "max-height: 28mm;" in css
    assert "table-layout: fixed;" in css
    assert "overflow-wrap: anywhere;" in css
    assert "page-break-after: avoid;" in css
