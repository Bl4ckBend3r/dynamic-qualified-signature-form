from __future__ import annotations

import html
import re
from dataclasses import dataclass
from io import BytesIO
from zipfile import BadZipFile

from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.opc.exceptions import PackageNotFoundError


PARSER_VERSION = "1.0"
_VARIABLE_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}", re.DOTALL)
_JINJA_FRAGMENT_RE = re.compile(r"({{|}}|{%|%}|{#|#})")


class DocxTemplateParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedDocxTemplate:
    html: str
    variables: tuple[str, ...]
    warnings: tuple[str, ...]


def parse_docx_template(content: bytes) -> ParsedDocxTemplate:
    if not content:
        raise DocxTemplateParseError("Plik DOCX jest pusty.")
    try:
        document = Document(BytesIO(content))
    except (PackageNotFoundError, BadZipFile, ValueError, KeyError) as exc:
        raise DocxTemplateParseError("Plik nie jest poprawnym dokumentem DOCX.") from exc

    variables: set[str] = set()
    warnings: list[str] = []
    blocks: list[str] = []
    pending_list: list[str] = []
    pending_list_tag: str | None = None

    def flush_list() -> None:
        nonlocal pending_list_tag
        if pending_list_tag:
            blocks.append(f"<{pending_list_tag} class=\"document-list\">{''.join(pending_list)}</{pending_list_tag}>")
        pending_list.clear()
        pending_list_tag = None

    for block in _iter_blocks(document):
        if isinstance(block, Paragraph):
            text = _paragraph_text(block, variables, warnings)
            list_tag = _list_tag(block)
            if list_tag:
                if pending_list_tag and pending_list_tag != list_tag:
                    flush_list()
                pending_list_tag = list_tag
                pending_list.append(f"<li>{text or '&nbsp;'}</li>")
                continue
            flush_list()
            blocks.append(_paragraph_html(block, text))
        else:
            flush_list()
            blocks.append(_table_html(block, variables, warnings))
    flush_list()

    if not blocks or not any(_visible_text(block) for block in blocks):
        raise DocxTemplateParseError("Dokument DOCX nie zawiera treści szablonu.")
    if _contains_unsupported_xml(document):
        warnings.append("Dokument zawiera obrazy, pola tekstowe lub obiekty, których parser nie przenosi do HTML.")

    return ParsedDocxTemplate(
        html='<main class="document document--agreement document--docx">' + "".join(blocks) + "</main>",
        variables=tuple(sorted(variables)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _iter_blocks(parent: DocxDocument):
    for child in parent.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _paragraph_text(paragraph: Paragraph, variables: set[str], warnings: list[str]) -> str:
    raw = paragraph.text or ""
    matches = list(_VARIABLE_RE.finditer(raw))
    variables.update(match.group(1) for match in matches)
    stripped = _VARIABLE_RE.sub("", raw)
    if _JINJA_FRAGMENT_RE.search(stripped):
        warnings.append("Wykryto niepełny lub nieobsługiwany znacznik Jinja; obsługiwane są proste zmienne {{ nazwa }}.")

    pieces: list[str] = []
    cursor = 0
    for match in matches:
        pieces.append(_escape_literal(raw[cursor : match.start()]))
        pieces.append("{{ " + match.group(1) + " }}")
        cursor = match.end()
    pieces.append(_escape_literal(raw[cursor:]))
    return "".join(pieces).replace("\n", "<br>")


def _paragraph_html(paragraph: Paragraph, text: str) -> str:
    style_name = str(getattr(paragraph.style, "name", "") or "").casefold()
    heading_match = re.search(r"(?:heading|nagłówek|naglowek)\s*([1-3])", style_name)
    tag = f"h{heading_match.group(1)}" if heading_match else "p"
    classes = ["document-paragraph"]
    alignment = {
        WD_ALIGN_PARAGRAPH.CENTER: "center",
        WD_ALIGN_PARAGRAPH.RIGHT: "right",
        WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
        WD_ALIGN_PARAGRAPH.LEFT: "left",
    }.get(paragraph.alignment)
    if alignment:
        classes.append(f"document-align-{alignment}")
    if not text:
        classes.append("document-paragraph--empty")
    return f'<{tag} class="{" ".join(classes)}">{text or "&nbsp;"}</{tag}>'


def _list_tag(paragraph: Paragraph) -> str | None:
    style = str(getattr(paragraph.style, "name", "") or "").casefold()
    num_pr = paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
    if not num_pr and not any(token in style for token in ("list", "lista", "bullet", "number")):
        return None
    return "ul" if any(token in style for token in ("bullet", "punkt")) else "ol"


def _table_html(table: Table, variables: set[str], warnings: list[str]) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            contents = "".join(_paragraph_html(p, _paragraph_text(p, variables, warnings)) for p in cell.paragraphs)
            cells.append(f"<td>{contents or '&nbsp;'}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return '<table class="document-table document-table--docx"><tbody>' + "".join(rows) + "</tbody></table>"


def _contains_unsupported_xml(document: DocxDocument) -> bool:
    xml = document.element.xml
    return any(marker in xml for marker in ("<w:drawing", "<w:pict", "<w:txbxContent", "<w:object"))


def _visible_text(markup: str) -> str:
    return re.sub(r"<[^>]+>|&nbsp;", "", markup).strip()


def _escape_literal(value: str) -> str:
    return html.escape(value).replace("{", "&#123;").replace("}", "&#125;")
