from types import SimpleNamespace

from pdf_generator import ensure_document_root, inject_pdf_styles


def test_shared_document_css_is_injected_into_admin_html_template(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
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
