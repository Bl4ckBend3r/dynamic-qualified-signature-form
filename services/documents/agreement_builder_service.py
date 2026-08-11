from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from jinja2 import TemplateSyntaxError, meta
from jinja2.sandbox import SandboxedEnvironment

from services.documents.agreement_template_context_service import AgreementVariableCatalog
from services.mail_template_service import sanitize_content_html


BUILDER_VERSION = 1
_JINJA_TOKEN_RE = re.compile(r"({{.*?}}|{%.*?%}|{#.*?#})", re.DOTALL)
_TEXT_BLOCK_TYPES = {"heading", "paragraph"}
_TEXT_ALIGNMENTS = {"left", "center", "right", "justify"}
BUILDER_BLOCK_TYPES = {
    "heading",
    "paragraph",
    "agreement_section",
    "ordered_list",
    "bullet_list",
    "table",
    "training_table",
    "participant_data",
    "signatures",
    "project_info",
    "page_break",
}


@dataclass(frozen=True)
class AgreementBuilderValidationError:
    path: str
    message: str
    variable: str = ""

    def as_dict(self) -> dict[str, str]:
        result = {"path": self.path, "message": self.message}
        if self.variable:
            result["variable"] = self.variable
        return result


def default_agreement_builder_document() -> dict[str, Any]:
    return {
        "version": BUILDER_VERSION,
        "blocks": [
            {
                "type": "heading",
                "level": 1,
                "format": _default_text_format("heading", alignment="center"),
                "content": "Umowa uczestnictwa nr {{ agreement_number }}",
            },
            {
                "type": "paragraph",
                "format": _default_text_format("paragraph", alignment="center"),
                "content": "zawarta w dniu {{ agreement_date }}",
            },
            {
                "type": "agreement_section",
                "number": "§ 1.",
                "title": "Przedmiot umowy",
            },
            {
                "type": "paragraph",
                "format": _default_text_format("paragraph", alignment="justify"),
                "content": "Umowa dotyczy udziału {{ participant_name }} w szkoleniu {{ training_name }}.",
            },
            {
                "type": "training_table",
                "scope": "selected_trainings",
                "columns": ["index", "name", "price"],
                "show_total": True,
            },
            {
                "type": "participant_data",
                "fields": ["participant_name", "pesel", "participant_address", "telefon"],
            },
            {
                "type": "signatures",
                "left_label": "Beneficjent",
                "right_label": "Uczestnik projektu",
            },
        ],
    }


def normalize_agreement_builder_document(value: Any) -> dict[str, Any]:
    document = dict(value) if isinstance(value, Mapping) else {}
    raw_blocks = document.get("blocks")
    blocks = [dict(block) for block in raw_blocks if isinstance(block, Mapping)] if isinstance(raw_blocks, list) else []
    for block in blocks:
        block_type = str(block.get("type") or "")
        block.pop("style", None)
        if block_type in _TEXT_BLOCK_TYPES:
            block["content"] = _sanitize_builder_content(block.get("content"))
            block["format"] = _normalize_text_format(block)
            # `alignment` was the version-1 representation. Reading it above is
            # the compatibility adapter; new saves use the bounded format map.
            block.pop("alignment", None)
        elif block_type == "agreement_section":
            block["number"] = _sanitize_builder_content(block.get("number"))
            block["title"] = _sanitize_builder_content(block.get("title"))
        elif block_type in {"ordered_list", "bullet_list"} and isinstance(block.get("items"), list):
            block["items"] = [
                {"content": _sanitize_builder_content(item.get("content")), "level": item.get("level", 0)}
                if isinstance(item, Mapping)
                else {"content": _sanitize_builder_content(item), "level": 0}
                for item in block["items"]
            ]
        elif block_type == "table" and isinstance(block.get("rows"), list):
            block["rows"] = [
                [_sanitize_builder_content(cell) for cell in row]
                for row in block["rows"] if isinstance(row, list)
            ]
    return {"version": BUILDER_VERSION, "blocks": blocks}


def validate_agreement_builder_document(
    value: Any,
    fields: Iterable[Any] = (),
) -> list[AgreementBuilderValidationError]:
    document = normalize_agreement_builder_document(value)
    errors: list[AgreementBuilderValidationError] = []
    blocks = document["blocks"]
    if not blocks:
        return [AgreementBuilderValidationError("blocks", "Dokument nie zawiera treści.")]
    if len(blocks) > 500:
        errors.append(AgreementBuilderValidationError("blocks", "Dokument może zawierać maksymalnie 500 bloków."))

    for index, block in enumerate(blocks):
        path = f"blocks.{index}"
        block_type = str(block.get("type") or "")
        if block_type not in BUILDER_BLOCK_TYPES:
            errors.append(AgreementBuilderValidationError(path, f"Nieobsługiwany typ bloku: {block_type or 'brak'}."))
            continue
        if block_type in {"heading", "paragraph"} and not _visible_content(block.get("content")):
            errors.append(AgreementBuilderValidationError(path + ".content", "Blok tekstowy nie może być pusty."))
        elif block_type == "agreement_section":
            if not str(block.get("number") or "").strip():
                errors.append(AgreementBuilderValidationError(path + ".number", "Podaj numer paragrafu."))
            if not str(block.get("title") or "").strip():
                errors.append(AgreementBuilderValidationError(path + ".title", "Podaj tytuł paragrafu."))
        elif block_type in {"ordered_list", "bullet_list"}:
            items = block.get("items")
            if not isinstance(items, list) or not items:
                errors.append(AgreementBuilderValidationError(path + ".items", "Lista musi zawierać co najmniej jeden punkt."))
            elif any(not _visible_content(item.get("content") if isinstance(item, Mapping) else item) for item in items):
                errors.append(AgreementBuilderValidationError(path + ".items", "Punkty listy nie mogą być puste."))
        elif block_type == "table":
            rows = block.get("rows")
            if not isinstance(rows, list) or not rows or any(not isinstance(row, list) or not row for row in rows):
                errors.append(AgreementBuilderValidationError(path + ".rows", "Tabela musi zawierać co najmniej jeden niepusty wiersz."))
        elif block_type == "training_table":
            columns = block.get("columns")
            if not isinstance(columns, list) or not any(item in {"index", "name", "price", "date", "location"} for item in columns):
                errors.append(AgreementBuilderValidationError(path + ".columns", "Wybierz co najmniej jedną kolumnę tabeli szkoleń."))
        elif block_type == "participant_data":
            if not isinstance(block.get("fields"), list) or not block.get("fields"):
                errors.append(AgreementBuilderValidationError(path + ".fields", "Wybierz co najmniej jedną daną uczestnika."))
        elif block_type == "signatures":
            if not str(block.get("left_label") or "").strip() or not str(block.get("right_label") or "").strip():
                errors.append(AgreementBuilderValidationError(path, "Sekcja podpisów wymaga etykiety lewej i prawej strony."))
        elif block_type == "project_info":
            if not isinstance(block.get("fields"), list) or not block.get("fields"):
                errors.append(AgreementBuilderValidationError(path + ".fields", "Wybierz co najmniej jedną informację o projekcie."))

        condition = block.get("condition")
        if condition is not None:
            if not isinstance(condition, Mapping) or not str(condition.get("variable") or "").strip():
                errors.append(AgreementBuilderValidationError(path + ".condition", "Warunek wymaga wskazania zmiennej."))

    if errors:
        return errors

    try:
        template_html = render_agreement_builder_template(document)
        environment = SandboxedEnvironment(autoescape=True)
        parsed = environment.parse(template_html)
    except (TemplateSyntaxError, ValueError) as exc:
        return [AgreementBuilderValidationError("blocks", f"Niepoprawna składnia Jinja: {exc}.")]

    known = AgreementVariableCatalog.context_names(fields) | set(environment.globals)
    unknown = sorted(meta.find_undeclared_variables(parsed) - known)
    for variable in unknown:
        errors.append(AgreementBuilderValidationError(
            _find_variable_path(blocks, variable),
            f"Nieznana zmienna: {{{{ {variable} }}}}",
            variable,
        ))
    return errors


def render_agreement_builder_template(value: Any) -> str:
    document = normalize_agreement_builder_document(value)
    rendered_blocks = []
    for block in document["blocks"]:
        rendered = _render_block(block)
        condition = block.get("condition")
        if isinstance(condition, Mapping) and str(condition.get("variable") or "").strip():
            rendered = _condition_open(condition) + rendered + "{% endif %}"
        rendered_blocks.append(rendered)
    return '<main class="document document--agreement document--builder">' + "".join(rendered_blocks) + "</main>"


def _render_block(block: Mapping[str, Any]) -> str:
    block_type = str(block.get("type") or "")
    if block_type == "heading":
        level = max(1, min(int(block.get("level") or 2), 3))
        return f'<h{level} class="{_text_format_classes(block, heading=True)}">{_inline(block.get("content"))}</h{level}>'
    if block_type == "paragraph":
        return f'<p class="{_text_format_classes(block)}">{_inline(block.get("content"))}</p>'
    if block_type == "agreement_section":
        return (
            '<section class="document-section">'
            f'<div class="document-section-number">{_inline(block.get("number"))}</div>'
            f'<div class="document-section-title">{_inline(block.get("title"))}</div>'
            "</section>"
        )
    if block_type in {"ordered_list", "bullet_list"}:
        tag = "ol" if block_type == "ordered_list" else "ul"
        items = []
        for item in block.get("items") or []:
            raw_content = item.get("content") if isinstance(item, Mapping) else item
            raw_level = item.get("level") if isinstance(item, Mapping) else 0
            try:
                level = max(0, min(int(raw_level or 0), 8))
            except (TypeError, ValueError):
                level = 0
            items.append(f'<li class="document-list__item document-list__item--level-{level}">{_inline(raw_content)}</li>')
        return f'<{tag} class="document-list">' + "".join(items) + f"</{tag}>"
    if block_type == "table":
        rows = []
        for row_index, row in enumerate(block.get("rows") or []):
            tag = "th" if row_index == 0 and block.get("header", True) else "td"
            rows.append("<tr>" + "".join(f"<{tag}>{_inline(cell)}</{tag}>" for cell in row) + "</tr>")
        head = f"<thead>{rows[0]}</thead>" if rows and block.get("header", True) else ""
        body_rows = rows[1:] if head else rows
        return '<table class="document-table document-builder-table">' + head + "<tbody>" + "".join(body_rows) + "</tbody></table>"
    if block_type == "training_table":
        return _render_training_table(block)
    if block_type == "participant_data":
        return _render_participant_data(block)
    if block_type == "signatures":
        return (
            '<section class="document-signatures">'
            '<div class="document-signature"><div class="document-signature__line"></div>'
            f'<div class="document-signature__label">{html.escape(str(block.get("left_label") or ""))}</div></div>'
            '<div class="document-signature"><div class="document-signature__line"></div>'
            f'<div class="document-signature__label">{html.escape(str(block.get("right_label") or ""))}</div></div>'
            "</section>"
        )
    if block_type == "project_info":
        return _render_project_info(block)
    if block_type == "page_break":
        return '<div class="document-page-break" aria-hidden="true"></div>'
    raise ValueError(f"Nieobsługiwany typ bloku: {block_type}")


def _render_training_table(block: Mapping[str, Any]) -> str:
    columns = [item for item in block.get("columns") or [] if item in {"index", "name", "price", "date", "location"}]
    scope = str(block.get("scope") or "selected_trainings")
    scope = scope if scope in {"selected_trainings", "all_selected_trainings", "locked_trainings"} else "selected_trainings"
    labels = {"index": "Lp.", "name": "Nazwa szkolenia", "price": "Cena", "date": "Termin", "location": "Lokalizacja"}
    values = {
        "index": "{{ loop.index }}",
        "name": "{{ training.get('name', training.get('label', '')) }}",
        "price": "{{ training.get('price_formatted', '') or training.get('price', '') }}",
        "date": "{{ training.get('date', training.get('date_label', '')) }}",
        "location": "{{ training.get('location', '') }}",
    }
    header = "".join(f"<th>{labels[column]}</th>" for column in columns)
    cells = "".join(f"<td>{values[column]}</td>" for column in columns)
    total_name = {
        "selected_trainings": "selected_trainings_total_formatted",
        "all_selected_trainings": "all_selected_trainings_total_formatted",
        "locked_trainings": "locked_trainings_total_formatted",
    }[scope]
    footer = ""
    if block.get("show_total", True):
        footer = f'<tfoot><tr><th colspan="{max(1, len(columns) - 1)}">Razem</th><td>{{{{ {total_name} }}}}</td></tr></tfoot>'
    return (
        '<table class="document-table document-training-table"><thead><tr>' + header + "</tr></thead><tbody>"
        f"{{% for training in {scope} %}}<tr>{cells}</tr>{{% endfor %}}"
        "</tbody>" + footer + "</table>"
    )


def _render_participant_data(block: Mapping[str, Any]) -> str:
    available = {
        "participant_name": ("Imię i nazwisko", "participant_name"),
        "pesel": ("PESEL", "pesel"),
        "participant_address": ("Adres", "participant_address_inline"),
        "telefon": ("Telefon", "telefon"),
        "email": ("E-mail", "email"),
        "data_urodzenia": ("Data urodzenia", "data_urodzenia"),
    }
    rows = []
    for field in block.get("fields") or []:
        if field in available:
            label, variable = available[field]
            rows.append(f'<div class="document-participant-data__row"><span>{label}:</span><strong>{{{{ {variable} }}}}</strong></div>')
    return '<section class="document-participant-data">' + "".join(rows) + "</section>"


def _render_project_info(block: Mapping[str, Any]) -> str:
    available = {
        "project_name": ("Nazwa projektu", "project_name"),
        "project_number": ("Numer projektu", "project_number"),
        "project_program": ("Program", "project_program"),
        "project_action": ("Działanie", "project_action"),
        "funding_source": ("Źródło finansowania", "funding_source"),
        "institution_name": ("Instytucja", "institution_name"),
    }
    rows = []
    for field in block.get("fields") or []:
        if field in available:
            label, variable = available[field]
            rows.append(f'<div class="document-project-info__row"><span>{label}:</span><strong>{{{{ {variable} }}}}</strong></div>')
    logo = ""
    if block.get("show_logo"):
        logo = '{% if project_logo_url %}<img class="document-project-info__logo" src="{{ project_logo_url }}" alt="Logo projektu">{% endif %}'
    return '<section class="document-project-info">' + logo + "".join(rows) + "</section>"


def _condition_open(condition: Mapping[str, Any]) -> str:
    variable = str(condition.get("variable") or "").strip()
    operator = str(condition.get("operator") or "not_empty")
    if operator == "empty":
        return f"{{% if not {variable} %}}"
    if operator == "equals":
        return "{% if " + variable + " == " + repr(str(condition.get("value") or "")) + " %}"
    if operator == "not_equals":
        return "{% if " + variable + " != " + repr(str(condition.get("value") or "")) + " %}"
    return f"{{% if {variable} %}}"


def _default_text_format(block_type: str, *, alignment: str = "left") -> dict[str, Any]:
    return {
        "bold": block_type == "heading",
        "italic": False,
        "underline": False,
        "alignment": alignment if alignment in _TEXT_ALIGNMENTS else "left",
    }


def _normalize_text_format(block: Mapping[str, Any]) -> dict[str, Any]:
    block_type = str(block.get("type") or "paragraph")
    raw = block.get("format") if isinstance(block.get("format"), Mapping) else {}
    alignment = str(raw.get("alignment") or block.get("alignment") or "left").strip().casefold()
    normalized = _default_text_format(block_type, alignment=alignment)
    for name in ("bold", "italic", "underline"):
        if name in raw:
            normalized[name] = _format_flag(raw.get(name))
    return normalized


def _format_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _text_format_classes(block: Mapping[str, Any], *, heading: bool = False) -> str:
    formatting = _normalize_text_format(block)
    classes = ["document-paragraph", f'document-align-{formatting["alignment"]}']
    if formatting["bold"]:
        classes.append("document-bold")
    elif heading:
        classes.append("document-regular")
    if formatting["italic"]:
        classes.append("document-italic")
    if formatting["underline"]:
        classes.append("document-underline")
    return " ".join(classes)


def _inline(value: Any) -> str:
    return _sanitize_builder_content(value)


def _sanitize_builder_content(value: Any) -> str:
    sanitized = sanitize_content_html(str(value or ""))
    # HTMLParser correctly escapes quotes/operators in text, but those entities
    # must be decoded inside Jinja tokens so compatible submission.get(...)
    # expressions and advanced if/for statements remain valid.
    return _JINJA_TOKEN_RE.sub(lambda match: html.unescape(match.group(0)), sanitized)


def _visible_content(value: Any) -> bool:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return bool(text.strip())


def _find_variable_path(blocks: list[Mapping[str, Any]], variable: str) -> str:
    needle = variable
    for index, block in enumerate(blocks):
        if needle in str(block):
            return f"blocks.{index}"
    return "blocks"
