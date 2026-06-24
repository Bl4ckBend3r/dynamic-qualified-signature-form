from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from flask import Flask

from pdf_generator import generate_pdf, generate_pdf_from_html


PDF_LOGO_FOOTER_PATTERN = re.compile(
    r"<footer\b[^>]*class=[\"'][^\"']*pdf-logo-footer[^\"']*[\"'][^>]*>.*?</footer>",
    re.IGNORECASE | re.DOTALL,
)
PDF_LOGO_HEADER_PATTERN = re.compile(
    r"<header\b[^>]*class=[\"'][^\"']*pdf-logo-header[^\"']*[\"'][^>]*>.*?</header>",
    re.IGNORECASE | re.DOTALL,
)
DOCUMENT_LOGO_HEADER_PATTERN = re.compile(
    r"<header\b[^>]*class=[\"'][^\"']*document-logo-header[^\"']*[\"'][^>]*>.*?</header>",
    re.IGNORECASE | re.DOTALL,
)
FORM_HEADER_IMAGE_PATTERN = re.compile(
    r"<div\b[^>]*class=[\"'][^\"']*form-header-image[^\"']*[\"'][^>]*>.*?</div>",
    re.IGNORECASE | re.DOTALL,
)


class PdfRenderError(RuntimeError):
    pass


def remove_inline_logo_markup(template_html: str) -> str:
    cleaned = PDF_LOGO_FOOTER_PATTERN.sub("", template_html)
    cleaned = PDF_LOGO_HEADER_PATTERN.sub("", cleaned)
    cleaned = DOCUMENT_LOGO_HEADER_PATTERN.sub("", cleaned)
    return FORM_HEADER_IMAGE_PATTERN.sub("", cleaned)


def prepare_document_template_html(template_html: str, context: Mapping[str, Any]) -> str:
    if str(context.get("pdf_image_url") or "").strip():
        return remove_inline_logo_markup(template_html)
    return template_html


class PdfRenderService:
    def __init__(self, template_renderer=generate_pdf, html_renderer=generate_pdf_from_html) -> None:
        self.template_renderer = template_renderer
        self.html_renderer = html_renderer

    def render_document_pdf_bytes(
        self,
        *,
        app: Flask,
        template_name: str,
        context: dict,
        template_html: str | None = None,
    ) -> bytes:
        with tempfile.NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
            dir=app.config["TEMP_DIR"],
        ) as tmp_pdf:
            tmp_pdf_path = Path(tmp_pdf.name)

        try:
            try:
                if template_html:
                    self.html_renderer(
                        app=app,
                        template_html=prepare_document_template_html(template_html, context),
                        context=context,
                        output_path=tmp_pdf_path,
                    )
                else:
                    self.template_renderer(
                        app=app,
                        template_name=template_name,
                        context=context,
                        output_path=tmp_pdf_path,
                    )
                return tmp_pdf_path.read_bytes()
            except Exception as exc:
                raise PdfRenderError(f"Nie udalo sie wyrenderowac PDF: {exc}") from exc
        finally:
            tmp_pdf_path.unlink(missing_ok=True)


def generate_document_pdf_bytes(
    *,
    app: Flask,
    template_name: str,
    context: dict,
    template_html: str | None = None,
) -> bytes:
    return PdfRenderService().render_document_pdf_bytes(
        app=app,
        template_name=template_name,
        template_html=template_html,
        context=context,
    )
