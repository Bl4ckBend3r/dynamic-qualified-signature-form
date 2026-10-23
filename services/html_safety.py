from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from markupsafe import Markup

from services.mail_template_service import sanitize_content_html


HTML_ALLOWED_FIELDS = {
    "field.label",
    "field.description",
    "field.help_text",
    "field.option.label",
    "section.label",
    "static_text.label",
    "document.template_html",
    "mail.content_html",
    "mail.footer_html",
}


def sanitize_trusted_html(value: object) -> Markup:
    return Markup(sanitize_content_html(str(value or "")))


class _PdfLinkSanitizer(HTMLParser):
    """Keep only HTTP(S) links in PDF field values and consent text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._blocked_depth = 0
        self._links: list[bool] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._blocked_depth += 1
            return
        if self._blocked_depth or tag != "a":
            return

        href = next((value for name, value in attrs if name == "href"), None)
        if href and self._allowed_href(href):
            self.parts.append(f'<a href="{html.escape(href, quote=True)}">')
            self._links.append(True)
        else:
            self._links.append(False)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            if self._blocked_depth:
                self._blocked_depth -= 1
            return
        if self._blocked_depth or tag != "a" or not self._links:
            return
        if self._links.pop():
            self.parts.append("</a>")

    def handle_data(self, data: str) -> None:
        if not self._blocked_depth:
            self.parts.append(html.escape(data))

    @staticmethod
    def _allowed_href(href: str) -> bool:
        if any(ord(char) <= 32 or ord(char) == 127 for char in href):
            return False
        try:
            parsed = urlsplit(href)
            return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            return False

    def html(self) -> Markup:
        self.parts.extend("</a>" for allowed in reversed(self._links) if allowed)
        return Markup("".join(self.parts))


def sanitize_pdf_link_html(value: object) -> str | Markup:
    raw = str(value or "")
    if not re.search(r"<a(?:\s|>|/)", raw, flags=re.IGNORECASE):
        return raw
    sanitizer = _PdfLinkSanitizer()
    sanitizer.feed(raw)
    return sanitizer.html()
