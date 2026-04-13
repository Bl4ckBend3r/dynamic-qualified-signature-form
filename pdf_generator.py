from pathlib import Path
from flask import render_template
from playwright.sync_api import sync_playwright


def generate_pdf(app, template_name: str, context: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        html_string = render_template(template_name, **context)

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