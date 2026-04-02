from pathlib import Path

from flask import render_template
from weasyprint import HTML


def generate_pdf(app, template_name: str, context: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        html_string = render_template(template_name, **context)
        base_url = str(app.config["BASE_DIR"])
        HTML(string=html_string, base_url=base_url).write_pdf(str(output_path))

    return output_path