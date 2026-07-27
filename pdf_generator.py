import base64
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from flask import render_template, render_template_string
from playwright.sync_api import sync_playwright

from services.nextcloud_storage import create_nextcloud_storage_from_env


NEXTCLOUD_LOGO_DIR = "Strona WWW/Formularze/Logo"


def inject_pdf_styles(app, html_string: str) -> str:
    css_paths = [
        Path(app.root_path) / "static" / "css" / "style.css",
        Path(app.root_path) / "static" / "css" / "document_template.css",
    ]

    css_content = []

    for css_path in css_paths:
        if css_path.exists():
            css_content.append(css_path.read_text(encoding="utf-8"))

    html_string = ensure_document_root(html_string)

    if not css_content:
        return html_string

    style_tag = "<style>\n" + "\n\n".join(css_content) + "\n</style>"

    if re.search(r"</head\s*>", html_string, flags=re.IGNORECASE):
        return re.sub(
            r"</head\s*>",
            lambda match: f"{style_tag}\n{match.group(0)}",
            html_string,
            count=1,
            flags=re.IGNORECASE,
        )

    return f"{style_tag}\n{html_string}"


def ensure_document_root(html_string: str) -> str:
    body_match = re.search(r"<body\b([^>]*)>", html_string, flags=re.IGNORECASE)
    if not body_match:
        return f'<main class="document">{html_string}</main>'

    attributes = body_match.group(1)
    class_match = re.search(r"\bclass\s*=\s*([\"'])(.*?)\1", attributes, flags=re.IGNORECASE | re.DOTALL)
    if class_match:
        classes = class_match.group(2).split()
        if "document" in classes:
            return html_string
        new_class = f'class={class_match.group(1)}{class_match.group(2)} document{class_match.group(1)}'
        new_attributes = attributes[: class_match.start()] + new_class + attributes[class_match.end() :]
    else:
        new_attributes = f'{attributes} class="document"'
    replacement = f"<body{new_attributes}>"
    return html_string[: body_match.start()] + replacement + html_string[body_match.end() :]


def get_logo_filename_from_url(footer_image_url: str) -> str:
    parsed = urlparse(footer_image_url)
    path = unquote(parsed.path or footer_image_url).replace("\\", "/").strip("/")

    if "/static/" in path:
        path = path.split("/static/", 1)[1]

    return Path(path).name


def read_nextcloud_logo_as_data_uri(footer_image_url: str | None) -> str | None:
    if not footer_image_url:
        return None

    if footer_image_url.startswith("data:"):
        return footer_image_url

    logo_filename = get_logo_filename_from_url(footer_image_url)

    if not logo_filename:
        return footer_image_url

    logo_path = f"{NEXTCLOUD_LOGO_DIR}/{logo_filename}"

    try:
        storage = create_nextcloud_storage_from_env()
        image_bytes = storage.get_file_bytes(logo_path)
    except Exception:
        return footer_image_url

    mime_type, _ = mimetypes.guess_type(logo_filename)

    if not mime_type:
        mime_type = "image/png"

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_footer_template(footer_image_url: str | None) -> str:
    footer_image_url = read_nextcloud_logo_as_data_uri(footer_image_url)

    if not footer_image_url:
        return "<div></div>"

    return f"""
    <div style="
        width: 100%;
        box-sizing: border-box;
        padding: 0 18mm 5mm 18mm;
        background: #ffffff;
        font-size: 0;
        line-height: 0;
    ">
        <img
            src="{footer_image_url}"
            style="
                display: block;
                width: 100%;
                max-width: 100%;
                height: 14mm;
                object-fit: contain;
                object-position: left center;
            "
        />
    </div>
    """


def write_pdf_from_html(
    html_string: str,
    output_path: Path,
    footer_image_url: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    footer_template = build_footer_template(footer_image_url)
    has_footer = footer_template != "<div></div>"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        page.set_content(html_string, wait_until="load")
        page.emulate_media(media="screen")
        page.pdf(
            path=str(output_path),
            format="A4",
            print_background=True,
            display_header_footer=has_footer,
            header_template="<div></div>",
            footer_template=footer_template,
            margin={
                "top": "20mm",
                "right": "20mm",
                "bottom": "28mm" if has_footer else "20mm",
                "left": "20mm",
            },
        )

        browser.close()

    return output_path


def get_footer_image_url(context: dict[str, Any]) -> str | None:
    value = str(context.get("pdf_image_url") or "").strip()
    return value or None


def generate_pdf(app, template_name: str, context: dict, output_path: Path) -> Path:
    with app.app_context():
        html_string = render_template(template_name, **context)
        html_string = inject_pdf_styles(app, html_string)

    return write_pdf_from_html(
        html_string,
        output_path,
        footer_image_url=get_footer_image_url(context),
    )


def generate_pdf_from_html(app, template_html: str, context: dict, output_path: Path) -> Path:
    with app.app_context():
        html_string = render_template_string(template_html, **context)
        html_string = inject_pdf_styles(app, html_string)

    return write_pdf_from_html(
        html_string,
        output_path,
        footer_image_url=get_footer_image_url(context),
    )
