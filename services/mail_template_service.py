from __future__ import annotations

import html
import mimetypes
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

from jinja2.sandbox import SandboxedEnvironment


MAIL_LAYOUT = {
    "container_width": "600px",
    "body_background": "#f0f1f5",
    "card_background": "#ffffff",
    "font_family": "Arial, Helvetica, sans-serif",
    "text_color": "#1d2e5b",
    "line_height": "1.4",
    "section_padding": "24px",
    "content_padding": "24px",
    "primary_color": "#1d2e5b",
    "background_color": "#f0f1f5",
    "light_panel_background": "#f7f3ec",
    "accent_color": "#c8a35d",
    "accent_color_variant": "#c7a35d",
    "text_muted": "#9096a7",
    "border_soft": "#e3dbcd",
    "panel_shadow_or_secondary": "#606b88",
    "secondary_blue": "#5c6989",
    "secondary_blue_dark": "#303e65",
}

ALLOWED_ZIP_EXTENSIONS = {".html", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
ALLOWED_ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
ALLOWED_CONTENT_TAGS = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "h1",
    "h2",
    "h3",
    "h4",
    "ol",
    "ul",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "a",
    "span",
    "div",
}
ALLOWED_CONTENT_ATTRS = {"title", "colspan", "rowspan"}
ALLOWED_LINK_ATTRS = {"href", "target", "title"}
STATUS_LABELS = {
    "FORM_SUBMITTED": "Wniosek zlozony",
    "WAITING_FOR_OFFICER_DECISION": "Oczekuje na decyzje",
    "CORRECTION_REQUIRED": "Do poprawy",
    "ACCEPTED": "Zaakceptowano",
    "REJECTED": "Odrzucono",
}


@dataclass
class ImportedMailTemplate:
    title: str
    intro_html: str
    body_html: str
    body_text: str
    instruction_html: str
    instruction_text: str
    footer_note: str
    assets: list[dict[str, Any]]


class MailImportError(ValueError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"style", "script"}:
            self._skip_depth += 1
        if tag.lower() in {"br", "p", "div", "tr", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"style", "script"} and self._skip_depth:
            self._skip_depth -= 1
        if tag.lower() in {"p", "div", "tr", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        text = " ".join(part.replace("\xa0", " ") for part in self.parts)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s+", "\n", text)
        text = re.sub(r"\s+\n", "\n", text)
        return text.strip()


class _ContentSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "iframe", "object", "embed", "form", "input"}:
            self._blocked_depth += 1
            return
        if self._blocked_depth or tag not in ALLOWED_CONTENT_TAGS:
            return
        attrs_html = self._attrs_html(tag, attrs)
        self.parts.append(f"<{tag}{attrs_html}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "iframe", "object", "embed", "form", "input"}:
            if self._blocked_depth:
                self._blocked_depth -= 1
            return
        if self._blocked_depth or tag not in ALLOWED_CONTENT_TAGS or tag == "br":
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._blocked_depth:
            self.parts.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        if not self._blocked_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._blocked_depth:
            self.parts.append(f"&#{name};")

    def html(self) -> str:
        return "".join(self.parts).strip()

    def _attrs_html(self, tag: str, attrs) -> str:
        allowed = ALLOWED_LINK_ATTRS if tag == "a" else ALLOWED_CONTENT_ATTRS
        rendered = []
        for raw_name, raw_value in attrs:
            name = (raw_name or "").lower()
            if name.startswith("on") or name not in allowed:
                continue
            value = str(raw_value or "")
            if tag == "a" and name == "href" and value.strip().lower().startswith("javascript:"):
                continue
            rendered.append(f' {name}="{html.escape(value, quote=True)}"')
        return "".join(rendered)


def html_to_text(raw_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw_html or "")
    return parser.text()


def extract_body_html(raw_html: str) -> str:
    match = re.search(r"(?is)<body\b[^>]*>(.*?)</body>", raw_html or "")
    if match:
        return match.group(1).strip()
    cleaned = re.sub(r"(?is)<!doctype[^>]*>", "", raw_html or "")
    cleaned = re.sub(r"(?is)<head\b[^>]*>.*?</head>", "", cleaned)
    cleaned = re.sub(r"(?is)</?(?:html)\b[^>]*>", "", cleaned)
    return cleaned.strip()


def sanitize_content_html(raw_html: str) -> str:
    cleaned = extract_body_html(raw_html)
    cleaned = re.sub(r"(?is)<(style|script|iframe|object|embed|link|meta)\b.*?</\1>", "", cleaned)
    cleaned = re.sub(r"(?is)<(style|script|iframe|object|embed|link|meta)\b[^>]*>", "", cleaned)
    sanitizer = _ContentSanitizer()
    sanitizer.feed(cleaned)
    return sanitizer.html()


def build_instruction_html(instruction_text: str) -> str:
    steps = parse_instruction_steps(instruction_text)
    if not steps:
        return ""
    rows = []
    for index, step in enumerate(steps, start=1):
        rows.append(
            '<tr>'
            f'<td style="width:24px;padding:0 8px 10px 0;color:{MAIL_LAYOUT["primary_color"]};font-size:16px;line-height:1.4;vertical-align:top;">{index}.</td>'
            f'<td style="padding:0 0 10px 0;color:{MAIL_LAYOUT["primary_color"]};font-size:16px;line-height:1.4;vertical-align:top;">{html.escape(step)}</td>'
            '</tr>'
        )
    return '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">' + "".join(rows) + "</table>"


def parse_instruction_steps(text: str) -> list[str]:
    lines = [line.strip() for line in str(text or "").splitlines()]
    steps = []
    after_header = False
    for line in lines:
        if not line:
            continue
        normalized = line.rstrip(":").strip().lower()
        if normalized == "instrukcja":
            after_header = True
            continue
        if not after_header and not re.match(r"^\d+[\.\)]\s+", line):
            continue
        line = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
        if line and line.lower() not in {"pozdrawiamy"}:
            steps.append(line)
    return steps


def parse_mail_content(raw_html: str, raw_text: str = "") -> ImportedMailTemplate:
    content_html = sanitize_content_html(raw_html)
    text = raw_text.strip() or html_to_text(content_html)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = extract_title(content_html) or (lines[0] if lines else "Wiadomosc")
    instruction_index = next((index for index, line in enumerate(lines) if line.rstrip(":").lower() == "instrukcja"), -1)
    footer_note = ""
    intro_lines: list[str] = []
    instruction_lines: list[str] = []

    if instruction_index >= 0:
        intro_lines = lines[1:instruction_index]
        instruction_lines = lines[instruction_index:]
    else:
        intro_lines = lines[1:]

    if instruction_lines and instruction_lines[-1].lower() in {"pozdrawiamy"}:
        footer_note = instruction_lines.pop()
    elif lines and lines[-1].lower() in {"pozdrawiamy"}:
        footer_note = lines[-1]
        intro_lines = intro_lines[:-1]

    instruction_text = "\n".join(instruction_lines)
    intro_text = "\n".join(intro_lines).strip()
    instruction_html = extract_instruction_html(content_html) or build_instruction_html(instruction_text)
    body_html = remove_instruction_from_html(content_html) if instruction_html else content_html
    body_html = body_html or paragraphs_to_html(intro_text)
    return ImportedMailTemplate(
        title=title,
        intro_html=paragraphs_to_html(intro_text),
        body_html=body_html,
        body_text=text,
        instruction_html=instruction_html,
        instruction_text=instruction_text,
        footer_note=footer_note,
        assets=[],
    )


def import_mail_template_zip(content: bytes) -> ImportedMailTemplate:
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise MailImportError("Niepoprawny plik ZIP.") from exc

    with archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        for entry in entries:
            validate_zip_entry(entry.filename)
        names = [entry.filename.replace("\\", "/") for entry in entries]
        html_name = "email.html" if "email.html" in names else next((name for name in names if PurePosixPath(name).suffix.lower() == ".html"), "")
        if not html_name:
            raise MailImportError("ZIP musi zawierac co najmniej jeden plik HTML.")
        txt_name = "email.txt" if "email.txt" in names else next((name for name in names if PurePosixPath(name).suffix.lower() == ".txt"), "")
        raw_html = archive.read(html_name).decode("utf-8-sig", errors="ignore")
        raw_text = archive.read(txt_name).decode("utf-8-sig", errors="ignore") if txt_name else ""
        parsed = parse_mail_content(raw_html, raw_text)
        parsed.assets = []
        for entry in entries:
            normalized = entry.filename.replace("\\", "/")
            suffix = PurePosixPath(normalized).suffix.lower()
            if suffix not in ALLOWED_ASSET_EXTENSIONS:
                continue
            parsed.assets.append(
                {
                    "filename": PurePosixPath(normalized).name,
                    "storage_path": normalized,
                    "mime_type": mimetypes.guess_type(normalized)[0] or "application/octet-stream",
                    "content": archive.read(entry),
                }
            )
        return parsed


def validate_zip_entry(filename: str) -> None:
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise MailImportError("ZIP zawiera niedozwolona sciezke pliku.")
    if path.suffix.lower() not in ALLOWED_ZIP_EXTENSIONS:
        raise MailImportError("ZIP zawiera niedozwolony typ pliku.")


def paragraphs_to_html(text: str) -> str:
    paragraphs = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)


def extract_title(content_html: str) -> str:
    for tag in ["h1", "h2"]:
        match = re.search(rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}>", content_html or "")
        if match:
            return html_to_text(match.group(1)).strip()
    return ""


def extract_instruction_html(content_html: str) -> str:
    match = re.search(r"(?is)<h[1-4]\b[^>]*>\s*Instrukcja\s*</h[1-4]>\s*(<ol\b.*?</ol>)", content_html or "")
    if match:
        return sanitize_content_html(match.group(1))
    match = re.search(r"(?is)<ol\b.*?</ol>", content_html or "")
    if match:
        return sanitize_content_html(match.group(0))
    return ""


def remove_instruction_from_html(content_html: str) -> str:
    cleaned = re.sub(r"(?is)<h[1-4]\b[^>]*>\s*Instrukcja\s*</h[1-4]>\s*<ol\b.*?</ol>", "", content_html or "")
    return cleaned.strip()


def generate_text_from_html(content_html: str) -> str:
    return html_to_text(content_html)


def _jinja_env() -> SandboxedEnvironment:
    return SandboxedEnvironment(autoescape=False)


def render_template_text(raw_text: str, context: dict[str, Any]) -> str:
    return _jinja_env().from_string(raw_text or "").render(**context)


def build_status_label(value: str) -> str:
    return STATUS_LABELS.get(str(value or ""), str(value or ""))


def build_mail_context(form, submission, files: list | None = None, extra: dict | None = None) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if submission:
        context.update(submission.data_json or {})
        for column in submission.__table__.columns:
            context[column.name] = getattr(submission, column.name)
        context["submission"] = dict(context)
        context["data_json"] = dict(submission.data_json or {})
    context["form_name"] = getattr(form, "name", "") if form else context.get("form_name", "")
    context["form_title"] = context["form_name"]
    context["form_slug"] = getattr(form, "slug", "") if form else context.get("form_slug", "")
    context["decision"] = context.get("officer_decision", "")
    if not context.get("imie") and context.get("imiona"):
        context["imie"] = context["imiona"]
    context["status_label"] = build_status_label(str(context.get("process_status") or ""))
    context.setdefault("podpisz_url", "")
    context.setdefault("pobierz_url", "")
    context.setdefault("document_url", "")
    context.setdefault("agreement", {})
    context.setdefault("signed_filename", "")
    context.setdefault("urls", {})
    context["files"] = files or []
    context.update(extra or {})
    return context


def render_platform_mail_html(template, context: dict[str, Any], footer_html: str = "") -> str:
    title = render_template_text(getattr(template, "content_title", "") or getattr(template, "name", "") or "Wiadomosc", context)
    raw_body_html = (
        getattr(template, "content_html", "")
        or getattr(template, "body_html", "")
        or getattr(template, "html_body", "")
        or getattr(template, "content_intro", "")
    )
    body_html = render_template_text(raw_body_html or "", context)
    raw_instruction_text = getattr(template, "instruction_text", "")
    instruction_html = render_template_text(getattr(template, "instruction_html", "") or build_instruction_html(raw_instruction_text), context)
    footer_note = render_template_text(getattr(template, "footer_note", "") or default_footer_note(), context)
    footer_html = render_template_text(footer_html or "", context)
    info_rows = [
        ("Formularz", context.get("form_name", "")),
        ("Numer zgloszenia", context.get("submission_id", "")),
        ("Status", context.get("status_label") or context.get("process_status", "")),
    ]
    info_html = "".join(
        f'<tr><td style="padding:4px 0;color:#ffffff;font-size:16px;line-height:1.4;">{html.escape(label)}: '
        f'<strong style="color:{MAIL_LAYOUT["accent_color"]};">{html.escape(str(value or ""))}</strong></td></tr>'
        for label, value in info_rows
    )
    return f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@media(max-width:550px) {{
  .platform-title {{ font-size:29.4px !important; }}
  .platform-instruction-title {{ font-size:20px !important; }}
}}
</style>
</head>
<body style="margin:0;padding:0;background:{MAIL_LAYOUT["body_background"]};font-family:{MAIL_LAYOUT["font_family"]};color:{MAIL_LAYOUT["text_color"]};line-height:1.4;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:{MAIL_LAYOUT["body_background"]};">
<tr><td align="center" style="padding:24px 0;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" class="platform-mail-card" style="width:600px;max-width:100%;border-collapse:collapse;background:{MAIL_LAYOUT["card_background"]};">
<tr><td class="platform-title" style="padding:0 24px 16px;text-align:center;color:{MAIL_LAYOUT["primary_color"]};font-size:42.7px;font-weight:700;line-height:1.4;">{html.escape(title)}</td></tr>
<tr><td style="padding:0 24px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:{MAIL_LAYOUT["primary_color"]};border-radius:20px;"><tr><td style="padding:24px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">{info_html}</table>
</td></tr></table>
</td></tr>
<tr><td style="padding:0 24px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:{MAIL_LAYOUT["light_panel_background"]};border:1px solid {MAIL_LAYOUT["border_soft"]};border-radius:20px;"><tr><td style="padding:24px;color:{MAIL_LAYOUT["primary_color"]};font-size:16px;line-height:1.4;">{body_html}</td></tr></table>
</td></tr>
<tr><td class="platform-instruction-title" style="padding:0 24px 16px;color:{MAIL_LAYOUT["primary_color"]};font-size:24px;font-weight:700;line-height:1.4;">Instrukcja</td></tr>
<tr><td style="padding:0 24px 16px;">{instruction_html}</td></tr>
<tr><td style="padding:0 24px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:{MAIL_LAYOUT["primary_color"]};border-radius:20px;"><tr><td style="padding:24px;color:#ffffff;font-size:16px;line-height:1.4;">{footer_note}</td></tr></table>
</td></tr>
{f'<tr><td style="padding:0 24px 16px;">{footer_html}</td></tr>' if footer_html else ''}
</table>
</td></tr>
</table>
</body>
</html>"""


def render_platform_mail_text(template, context: dict[str, Any]) -> str:
    title = render_template_text(getattr(template, "content_title", "") or getattr(template, "name", "") or "Wiadomosc", context)
    raw_body = (
        getattr(template, "body_text", "")
        or getattr(template, "text_body", "")
        or getattr(template, "content_text", "")
        or getattr(template, "content_intro", "")
        or getattr(template, "content_html", "")
        or getattr(template, "body_html", "")
        or getattr(template, "html_body", "")
    )
    body = render_template_text(raw_body or "", context)
    instruction = render_template_text(
        getattr(template, "instruction_text", "") or html_to_text(getattr(template, "instruction_html", "") or ""),
        context,
    )
    footer = render_template_text(getattr(template, "footer_note", "") or "Pozdrawiamy", context)
    parts = [title, body, instruction, footer]
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def default_footer_note() -> str:
    return (
        'W razie problemow z podpisaniem lub wgraniem dokumentow skontaktuj sie z obsluga projektu '
        'i podaj numer zgloszenia: <strong style="color:#c8a35d;">{{ submission_id }}</strong>.'
    )
