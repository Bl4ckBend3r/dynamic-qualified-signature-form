from pathlib import Path

from flask import render_template, render_template_string
from playwright.sync_api import sync_playwright


def inject_pdf_styles(app, html_string: str) -> str:
    css_paths = [
        Path(app.root_path) / "static" / "style.css",
        Path(app.root_path) / "static" / "document_template.css",
    ]

    css_content = []

    for css_path in css_paths:
        if css_path.exists():
            css_content.append(css_path.read_text(encoding="utf-8"))

    if not css_content:
        return html_string

    style_tag = "<style>\n" + "\n\n".join(css_content) + "\n</style>"

    if "</head>" in html_string:
        return html_string.replace("</head>", f"{style_tag}\n</head>", 1)

    return f"{style_tag}\n{html_string}"


def write_pdf_from_html(html_string: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        page.set_content(html_string, wait_until="load")
        page.emulate_media(media="screen")
        page.pdf(
            path=str(output_path),
            format="A4",
            print_background=True,
            margin={
                "top": "20mm",
                "right": "20mm",
                "bottom": "20mm",
                "left": "20mm",
            },
        )

        browser.close()

    return output_path


def generate_pdf(app, template_name: str, context: dict, output_path: Path) -> Path:
    with app.app_context():
        html_string = render_template(template_name, **context)
        html_string = inject_pdf_styles(app, html_string)

    return write_pdf_from_html(html_string, output_path)


def generate_pdf_from_html(app, template_html: str, context: dict, output_path: Path) -> Path:
    with app.app_context():
        html_string = render_template_string(template_html, **context)
        html_string = inject_pdf_styles(app, html_string)

    return write_pdf_from_html(html_string, output_path)
