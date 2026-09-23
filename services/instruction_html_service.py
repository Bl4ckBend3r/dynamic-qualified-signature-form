from __future__ import annotations

import html
from html.parser import HTMLParser
from urllib.parse import urlsplit


ALLOWED_INSTRUCTION_TAGS = {"p", "br", "strong", "b", "em", "i", "ul", "ol", "li", "a"}
DROP_WITH_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "math", "template"}
ALLOWED_LINK_SCHEMES = {"", "http", "https", "mailto", "tel"}


def sanitize_instruction_html(value: object, *, limit: int = 50_000) -> str:
    """Return a small, safe HTML fragment suitable for instructions and JSON APIs."""
    source = str(value or "").replace("\x00", "")[:limit]
    parser = _InstructionHTMLSanitizer()
    parser.feed(source)
    parser.close()
    return parser.result().strip()


class _InstructionHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.open_tags: list[str] = []
        self.suppressed_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.suppressed_tags:
            self.suppressed_tags.append(tag)
            return
        if tag in DROP_WITH_CONTENT_TAGS:
            self.suppressed_tags.append(tag)
            return
        if tag not in ALLOWED_INSTRUCTION_TAGS:
            return
        if tag == "br":
            self.output.append("<br>")
            return
        if tag == "a":
            self.output.append(self._safe_anchor(attrs))
        else:
            self.output.append(f"<{tag}>")
        self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() != "br":
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.suppressed_tags:
            if tag in self.suppressed_tags:
                while self.suppressed_tags:
                    current = self.suppressed_tags.pop()
                    if current == tag:
                        break
            return
        if tag not in self.open_tags:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            self.output.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.suppressed_tags:
            self.output.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")

    def result(self) -> str:
        while self.open_tags:
            self.output.append(f"</{self.open_tags.pop()}>")
        return "".join(self.output)

    @staticmethod
    def _safe_anchor(attrs: list[tuple[str, str | None]]) -> str:
        values = {str(name).lower(): str(value or "") for name, value in attrs}
        href = "".join(character for character in values.get("href", "").strip() if ord(character) >= 32)
        scheme = urlsplit(href).scheme.lower()
        if scheme not in ALLOWED_LINK_SCHEMES:
            href = ""
        parts = ["<a"]
        if href:
            parts.append(f' href="{html.escape(href, quote=True)}"')
        if scheme in {"http", "https"} or values.get("target") == "_blank":
            parts.append(' target="_blank"')
        parts.append(' rel="noopener noreferrer">')
        return "".join(parts)
