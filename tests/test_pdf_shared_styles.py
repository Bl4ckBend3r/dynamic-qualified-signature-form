from types import SimpleNamespace
from pathlib import Path

import pytest

from pdf_generator import ensure_document_root, inject_pdf_styles, write_pdf_from_html


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
    page_rule = css.split("@page", 1)[1].split("}", 1)[0]

    assert "size: A4;" in css
    assert "margin:" not in page_rule
    assert "html.agreement-preview-document body" in css
    assert "padding: 18mm 18mm 20mm;" in css
    assert "font-size: 10.5pt;" in css
    assert ".document .document-bold" in css
    assert ".document .document-italic" in css
    assert ".document .document-underline" in css
    assert ".document .document-align-justify" in css
    assert ".document-section-number" in css
    assert ".document-section-title" in css
    assert ".document-list__item" in css
    assert "max-height: 28mm;" in css
    assert "table-layout: fixed;" in css
    assert "overflow-wrap: anywhere;" in css
    assert "page-break-after: avoid;" in css


def test_preview_a4_content_box_keeps_real_dimensions_and_wraps_long_content():
    playwright_api = pytest.importorskip("playwright.sync_api")
    css = (PROJECT_ROOT / "static" / "css" / "document_template.css").read_text(encoding="utf-8")
    long_word = "BardzoDługiFragmentAdresu" * 18
    html = f"""<!doctype html><html class="agreement-preview-document"><head><style>{css}</style></head>
    <body><main class="document document--agreement document--builder"><h1>Umowa uczestnictwa</h1>
    <p class="document-paragraph document-align-justify">{long_word}</p>
    <ol class="document-list"><li class="document-list__item">Długi punkt listy {long_word}</li></ol>
    <table class="document-table"><tbody><tr><td>{long_word}</td><td>Dane</td></tr></tbody></table>
    </main></body></html>"""

    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except playwright_api.Error as exception:
            pytest.skip(f"Brak przeglądarki Playwright: {exception}")
        page = browser.new_page(viewport={"width": 1000, "height": 900})
        page.set_content(html)
        metrics = page.evaluate("""() => {
            const body = document.body;
            const documentNode = document.querySelector('.document');
            const paragraph = document.querySelector('.document-paragraph');
            return {
                pageWidth: body.getBoundingClientRect().width,
                contentWidth: documentNode.getBoundingClientRect().width,
                paddingLeft: parseFloat(getComputedStyle(body).paddingLeft),
                fontSize: getComputedStyle(documentNode).fontSize,
                fits: documentNode.scrollWidth <= documentNode.clientWidth,
                paragraphFits: paragraph.scrollWidth <= paragraph.clientWidth,
            };
        }""")
        browser.close()

    assert 790 <= metrics["pageWidth"] <= 800
    assert 650 <= metrics["contentWidth"] <= 665
    assert 65 <= metrics["paddingLeft"] <= 70
    assert metrics["fontSize"] == "14px"
    assert metrics["fits"] is True
    assert metrics["paragraphFits"] is True


def test_pdf_renderer_applies_single_eighteen_millimeter_horizontal_margin(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    output_path = tmp_path / "margins.pdf"
    html = """<!doctype html><html><head><style>
    @page { size: A4; }
    html, body { margin: 0; padding: 0; }
    body { font: 12pt Arial, sans-serif; }
    </style></head><body><div>Znacznik marginesu</div></body></html>"""

    write_pdf_from_html(html, output_path)
    positions = []

    def collect_position(text, current_matrix, text_matrix, _font, _size):
        if "Znacznik" in text:
            positions.append((tuple(current_matrix), tuple(text_matrix)))

    pypdf.PdfReader(str(output_path)).pages[0].extract_text(visitor_text=collect_position)

    assert positions
    # 18 mm is about 51 pt. A doubled margin would start around 102 pt.
    horizontal_positions = [current[4] + text[4] for current, text in positions]
    assert 45 <= min(horizontal_positions) <= 60, positions
