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
DOCUMENT_TYPES = {"agreement", "declaration"}
_JINJA_TOKEN_RE = re.compile(r"({{.*?}}|{%.*?%}|{#.*?#})", re.DOTALL)
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TEXT_BLOCK_TYPES = {"heading", "paragraph", "statement"}
_TEXT_ALIGNMENTS = {"left", "center", "right", "justify"}
_COMMON_BLOCK_TYPES = {
    "heading",
    "paragraph",
    "ordered_list",
    "bullet_list",
    "table",
    "participant_data",
    "signatures",
    "project_info",
    "page_break",
}
_AGREEMENT_BLOCK_TYPES = {"agreement_section", "training_table"}
_DECLARATION_BLOCK_TYPES = {
    "form_field",
    "participant_address",
    "participant_contact",
    "statement",
    "participant_signature",
}


@dataclass(frozen=True)
class DocumentBuilderValidationError:
    path: str
    message: str
    variable: str = ""

    def as_dict(self) -> dict[str, str]:
        result = {"path": self.path, "message": self.message}
        if self.variable:
            result["variable"] = self.variable
        return result


def document_builder_block_types(document_type: str) -> set[str]:
    normalized_type = _document_type(document_type)
    specific = _AGREEMENT_BLOCK_TYPES if normalized_type == "agreement" else _DECLARATION_BLOCK_TYPES
    return _COMMON_BLOCK_TYPES | specific


def default_document_builder_document(document_type: str = "agreement") -> dict[str, Any]:
    if _document_type(document_type) == "declaration":
        return {
            "version": BUILDER_VERSION,
            "document_type": "declaration",
            "blocks": [
                {
                    "type": "heading",
                    "level": 1,
                    "format": {"alignment": "center"},
                    "runs": [{"text": "Deklaracja uczestnictwa", "bold": True, "italic": False, "underline": False}],
                },
                {
                    "type": "paragraph",
                    "format": {"alignment": "center"},
                    "runs": [{"text": "z dnia {{ generated_date }}", "bold": False, "italic": False, "underline": False}],
                },
                {"type": "participant_data", "fields": ["participant_name", "pesel"]},
                {"type": "participant_address"},
                {"type": "participant_contact", "fields": ["email", "telefon"]},
                {
                    "type": "statement",
                    "format": {"alignment": "justify"},
                    "runs": [{"text": "Oświadczam, że podane dane są prawdziwe.", "bold": False, "italic": False, "underline": False}],
                },
                {"type": "participant_signature", "label": "Czytelny podpis uczestnika"},
            ],
        }
    return {
        "version": BUILDER_VERSION,
        "document_type": "agreement",
        "blocks": [
            {
                "type": "heading",
                "level": 1,
                "format": {"alignment": "center"},
                "runs": [{"text": "Umowa uczestnictwa nr {{ agreement_number }}", "bold": True, "italic": False, "underline": False}],
            },
            {
                "type": "paragraph",
                "format": {"alignment": "center"},
                "runs": [{"text": "zawarta w dniu {{ agreement_date }}", "bold": False, "italic": False, "underline": False}],
            },
            {"type": "agreement_section", "number": "§ 1.", "title": "Przedmiot umowy"},
            {
                "type": "paragraph",
                "format": {"alignment": "justify"},
                "runs": [{"text": "Umowa dotyczy udziału {{ participant_name }} w szkoleniu {{ training_name }}.", "bold": False, "italic": False, "underline": False}],
            },
            {"type": "training_table", "scope": "selected_trainings", "columns": ["index", "name", "price"], "show_total": True},
            {"type": "participant_data", "fields": ["participant_name", "pesel", "participant_address", "telefon"]},
            {"type": "signatures", "left_label": "Beneficjent", "right_label": "Uczestnik projektu"},
        ],
    }


def normalize_document_builder_document(value: Any, document_type: str = "agreement") -> dict[str, Any]:
    normalized_type = _document_type(document_type)
    document = dict(value) if isinstance(value, Mapping) else {}
    raw_blocks = document.get("blocks")
    blocks = [dict(block) for block in raw_blocks if isinstance(block, Mapping)] if isinstance(raw_blocks, list) else []
    for block in blocks:
        block_type = str(block.get("type") or "")
        block.pop("style", None)
        if block_type in _TEXT_BLOCK_TYPES:
            _normalize_rich_text_holder(block, block_type)
        elif block_type == "agreement_section":
            block["number"] = _sanitize_builder_content(block.get("number"))
            block["title"] = _sanitize_builder_content(block.get("title"))
        elif block_type in {"ordered_list", "bullet_list"} and isinstance(block.get("items"), list):
            normalized_items = []
            for item in block["items"]:
                holder = dict(item) if isinstance(item, Mapping) else {"content": item, "level": 0}
                _normalize_rich_text_holder(holder, "paragraph", include_format=False)
                try:
                    holder["level"] = max(0, min(int(holder.get("level") or 0), 8))
                except (TypeError, ValueError):
                    holder["level"] = 0
                normalized_items.append(holder)
            block["items"] = normalized_items
        elif block_type == "table" and isinstance(block.get("rows"), list):
            rows = []
            for row in block["rows"]:
                if not isinstance(row, list):
                    continue
                normalized_row = []
                for cell in row:
                    if isinstance(cell, Mapping):
                        holder = dict(cell)
                        _normalize_rich_text_holder(holder, "paragraph", include_format=False)
                        normalized_row.append(holder)
                    else:
                        normalized_row.append(_sanitize_builder_content(cell))
                rows.append(normalized_row)
            block["rows"] = rows
        elif block_type == "form_field":
            block["field"] = str(block.get("field") or "").strip()
            block["label"] = _plain_label(block.get("label"))
            display = str(block.get("display") or "display").strip().casefold()
            block["display"] = display if display in {"display", "value", "yes_no"} else "display"
        elif block_type == "participant_contact":
            block["fields"] = [item for item in block.get("fields") or [] if item in {"email", "telefon", "phone"}]
        elif block_type == "participant_signature":
            block["label"] = _plain_label(block.get("label") or "Czytelny podpis uczestnika")
    return {"version": BUILDER_VERSION, "document_type": normalized_type, "blocks": blocks}


def validate_document_builder_document(
    value: Any,
    fields: Iterable[Any] = (),
    document_type: str = "agreement",
) -> list[DocumentBuilderValidationError]:
    normalized_type = _document_type(document_type)
    fields = tuple(fields)
    document = normalize_document_builder_document(value, normalized_type)
    errors: list[DocumentBuilderValidationError] = []
    blocks = document["blocks"]
    if not blocks:
        return [DocumentBuilderValidationError("blocks", "Dokument nie zawiera treści.")]
    if len(blocks) > 500:
        errors.append(DocumentBuilderValidationError("blocks", "Dokument może zawierać maksymalnie 500 bloków."))
    allowed_types = document_builder_block_types(normalized_type)
    dynamic_field_types = {
        _field_value(field, "name"): (_field_value(field, "field_type") or _field_value(field, "type")).casefold()
        for field in fields
        if _field_value(field, "name")
    }
    dynamic_fields = set(dynamic_field_types)

    for index, block in enumerate(blocks):
        path = f"blocks.{index}"
        block_type = str(block.get("type") or "")
        if block_type not in allowed_types:
            errors.append(DocumentBuilderValidationError(path, f"Nieobsługiwany typ bloku dla dokumentu {normalized_type}: {block_type or 'brak'}."))
            continue
        if block_type in _TEXT_BLOCK_TYPES and not _visible_inline(block):
            errors.append(DocumentBuilderValidationError(path + ".content", "Blok tekstowy nie może być pusty."))
        elif block_type == "agreement_section":
            if not str(block.get("number") or "").strip():
                errors.append(DocumentBuilderValidationError(path + ".number", "Podaj numer paragrafu."))
            if not str(block.get("title") or "").strip():
                errors.append(DocumentBuilderValidationError(path + ".title", "Podaj tytuł paragrafu."))
        elif block_type in {"ordered_list", "bullet_list"}:
            items = block.get("items")
            if not isinstance(items, list) or not items:
                errors.append(DocumentBuilderValidationError(path + ".items", "Lista musi zawierać co najmniej jeden punkt."))
            elif any(not _visible_inline(item) for item in items):
                errors.append(DocumentBuilderValidationError(path + ".items", "Punkty listy nie mogą być puste."))
        elif block_type == "table":
            rows = block.get("rows")
            if not isinstance(rows, list) or not rows or any(not isinstance(row, list) or not row for row in rows):
                errors.append(DocumentBuilderValidationError(path + ".rows", "Tabela musi zawierać co najmniej jeden niepusty wiersz."))
        elif block_type == "training_table":
            columns = block.get("columns")
            if not isinstance(columns, list) or not any(item in {"index", "name", "price", "date", "location"} for item in columns):
                errors.append(DocumentBuilderValidationError(path + ".columns", "Wybierz co najmniej jedną kolumnę tabeli szkoleń."))
        elif block_type == "participant_data":
            if not isinstance(block.get("fields"), list) or not block.get("fields"):
                errors.append(DocumentBuilderValidationError(path + ".fields", "Wybierz co najmniej jedną daną uczestnika."))
        elif block_type == "participant_contact":
            if not block.get("fields"):
                errors.append(DocumentBuilderValidationError(path + ".fields", "Wybierz co najmniej jedną daną kontaktową."))
        elif block_type == "form_field":
            field_name = str(block.get("field") or "")
            if not _FIELD_NAME_RE.fullmatch(field_name) or field_name not in dynamic_fields:
                errors.append(DocumentBuilderValidationError(path + ".field", "Wybierz aktywne pole formularza."))
            elif block.get("display") == "yes_no" and dynamic_field_types.get(field_name) not in {"checkbox", "boolean", "bool"}:
                errors.append(DocumentBuilderValidationError(path + ".display", "Wariant Tak/Nie jest dostępny tylko dla pola logicznego."))
        elif block_type == "signatures":
            if not str(block.get("left_label") or "").strip() or not str(block.get("right_label") or "").strip():
                errors.append(DocumentBuilderValidationError(path, "Sekcja podpisów wymaga etykiety lewej i prawej strony."))
        elif block_type == "project_info":
            if not isinstance(block.get("fields"), list) or not block.get("fields"):
                errors.append(DocumentBuilderValidationError(path + ".fields", "Wybierz co najmniej jedną informację o projekcie."))

        for run_index, run in enumerate(block.get("runs") or []):
            if len(str(run.get("text") or "")) > 50_000:
                errors.append(DocumentBuilderValidationError(f"{path}.runs.{run_index}.text", "Fragment tekstu jest zbyt długi."))
        condition = block.get("condition")
        if condition is not None and (not isinstance(condition, Mapping) or not str(condition.get("variable") or "").strip()):
            errors.append(DocumentBuilderValidationError(path + ".condition", "Warunek wymaga wskazania zmiennej."))

    if errors:
        return errors
    try:
        template_html = render_document_builder_template(document, normalized_type)
        environment = SandboxedEnvironment(autoescape=True)
        parsed = environment.parse(template_html)
    except (TemplateSyntaxError, ValueError) as exc:
        return [DocumentBuilderValidationError("blocks", f"Niepoprawna składnia Jinja: {exc}.")]

    known = _context_names(fields, normalized_type) | set(environment.globals)
    for variable in sorted(meta.find_undeclared_variables(parsed) - known):
        errors.append(DocumentBuilderValidationError(
            _find_variable_path(blocks, variable),
            f"Nieznana zmienna: {{{{ {variable} }}}}",
            variable,
        ))
    return errors


def render_document_builder_template(value: Any, document_type: str = "agreement") -> str:
    normalized_type = _document_type(document_type)
    document = normalize_document_builder_document(value, normalized_type)
    rendered_blocks = []
    for block in document["blocks"]:
        rendered = _render_block(block, normalized_type)
        condition = block.get("condition")
        if isinstance(condition, Mapping) and str(condition.get("variable") or "").strip():
            rendered = _condition_open(condition) + rendered + "{% endif %}"
        rendered_blocks.append(rendered)
    return f'<main class="document document--{normalized_type} document--builder">' + "".join(rendered_blocks) + "</main>"


def _render_block(block: Mapping[str, Any], document_type: str) -> str:
    block_type = str(block.get("type") or "")
    if block_type == "heading":
        level = max(1, min(int(block.get("level") or 2), 3))
        return f'<h{level} class="{_text_format_classes(block, heading=True)}">{_inline(block)}</h{level}>'
    if block_type in {"paragraph", "statement"}:
        statement_class = " document-statement" if block_type == "statement" else ""
        return f'<p class="{_text_format_classes(block)}{statement_class}">{_inline(block)}</p>'
    if block_type == "agreement_section":
        return '<section class="document-section">' + f'<div class="document-section-number">{_inline(block.get("number"))}</div>' + f'<div class="document-section-title">{_inline(block.get("title"))}</div></section>'
    if block_type in {"ordered_list", "bullet_list"}:
        tag = "ol" if block_type == "ordered_list" else "ul"
        items = []
        for item in block.get("items") or []:
            try:
                level = max(0, min(int(item.get("level") or 0), 8))
            except (AttributeError, TypeError, ValueError):
                level = 0
            items.append(f'<li class="document-list__item document-list__item--level-{level}">{_inline(item)}</li>')
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
    if block_type == "participant_address":
        return '<section class="document-participant-data document-participant-address"><div class="document-participant-data__row"><span>Adres:</span><strong>{{ participant_address }}</strong></div></section>'
    if block_type == "participant_contact":
        rows = []
        available = {"email": ("E-mail", "email"), "telefon": ("Telefon", "telefon"), "phone": ("Telefon", "phone")}
        for field in block.get("fields") or []:
            if field in available:
                label, variable = available[field]
                rows.append(f'<div class="document-participant-data__row"><span>{label}:</span><strong>{{{{ {variable} }}}}</strong></div>')
        return '<section class="document-participant-data document-participant-contact">' + "".join(rows) + "</section>"
    if block_type == "form_field":
        field_name = str(block.get("field") or "")
        if not _FIELD_NAME_RE.fullmatch(field_name):
            raise ValueError("Niepoprawna nazwa pola formularza.")
        label = html.escape(str(block.get("label") or field_name))
        suffix = {"display": "_display", "yes_no": "_yes_no", "value": ""}.get(str(block.get("display") or "display"), "_display")
        return f'<div class="document-form-field"><span>{label}:</span><strong>{{{{ {field_name}{suffix} }}}}</strong></div>'
    if block_type == "signatures":
        return _render_signatures(str(block.get("left_label") or ""), str(block.get("right_label") or ""))
    if block_type == "participant_signature":
        label = html.escape(str(block.get("label") or "Czytelny podpis uczestnika"))
        return '<section class="document-signatures document-signatures--participant"><div class="document-signature"><div class="document-signature__line"></div>' + f'<div class="document-signature__label">{label}</div></div></section>'
    if block_type == "project_info":
        return _render_project_info(block)
    if block_type == "page_break":
        return '<div class="document-page-break" aria-hidden="true"></div>'
    raise ValueError(f"Nieobsługiwany typ bloku dla dokumentu {document_type}: {block_type}")


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
    total_name = {"selected_trainings": "selected_trainings_total_formatted", "all_selected_trainings": "all_selected_trainings_total_formatted", "locked_trainings": "locked_trainings_total_formatted"}[scope]
    footer = f'<tfoot><tr><th colspan="{max(1, len(columns) - 1)}">Razem</th><td>{{{{ {total_name} }}}}</td></tr></tfoot>' if block.get("show_total", True) else ""
    return '<table class="document-table document-training-table"><thead><tr>' + header + "</tr></thead><tbody>" + f"{{% for training in {scope} %}}<tr>{cells}</tr>{{% endfor %}}" + "</tbody>" + footer + "</table>"


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


def _render_signatures(left_label: str, right_label: str) -> str:
    return '<section class="document-signatures"><div class="document-signature"><div class="document-signature__line"></div>' + f'<div class="document-signature__label">{html.escape(left_label)}</div></div><div class="document-signature"><div class="document-signature__line"></div>' + f'<div class="document-signature__label">{html.escape(right_label)}</div></div></section>'


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
    logo = '{% if project_logo_url %}<img class="document-project-info__logo" src="{{ project_logo_url }}" alt="Logo projektu">{% endif %}' if block.get("show_logo") else ""
    return '<section class="document-project-info">' + logo + "".join(rows) + "</section>"


def _normalize_rich_text_holder(holder: dict[str, Any], block_type: str, *, include_format: bool = True) -> None:
    if "runs" in holder:
        holder["runs"] = normalize_inline_runs(holder.get("runs"))
        holder["content"] = "".join(run["text"] for run in holder["runs"])
    else:
        holder["content"] = _sanitize_builder_content(holder.get("content"))
    if include_format:
        holder["format"] = _normalize_text_format(holder, block_type)
        holder.pop("alignment", None)


def normalize_inline_runs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        text = str(raw.get("text") or "").replace("\x00", "")
        if not text:
            continue
        run = {
            "text": text,
            "bold": _format_flag(raw.get("bold")),
            "italic": _format_flag(raw.get("italic")),
            "underline": _format_flag(raw.get("underline")),
        }
        if normalized and all(normalized[-1][key] == run[key] for key in ("bold", "italic", "underline")):
            normalized[-1]["text"] += run["text"]
        else:
            normalized.append(run)
    return normalized


def _inline(value: Any) -> str:
    if isinstance(value, Mapping) and "runs" in value:
        return "".join(_render_run(run) for run in normalize_inline_runs(value.get("runs")))
    if isinstance(value, Mapping):
        value = value.get("content")
    return _sanitize_builder_content(value)


def _render_run(run: Mapping[str, Any]) -> str:
    rendered = _render_run_text(run.get("text"))
    if run.get("bold"):
        rendered = f"<strong>{rendered}</strong>"
    if run.get("italic"):
        rendered = f"<em>{rendered}</em>"
    if run.get("underline"):
        rendered = f'<span class="document-inline-underline">{rendered}</span>'
    return rendered


def _render_run_text(value: Any) -> str:
    raw = str(value or "")
    pieces: list[str] = []
    cursor = 0
    for match in _JINJA_TOKEN_RE.finditer(raw):
        pieces.append(html.escape(raw[cursor:match.start()]).replace("\n", "<br>"))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(html.escape(raw[cursor:]).replace("\n", "<br>"))
    return "".join(pieces)


def _normalize_text_format(block: Mapping[str, Any], block_type: str) -> dict[str, Any]:
    raw = block.get("format") if isinstance(block.get("format"), Mapping) else {}
    alignment = str(raw.get("alignment") or block.get("alignment") or "left").strip().casefold()
    result: dict[str, Any] = {"alignment": alignment if alignment in _TEXT_ALIGNMENTS else "left"}
    # Keep legacy whole-block flags only for documents that have not yet been
    # migrated by the visual editor. New documents persist inline styling in runs.
    if "runs" not in block:
        for name in ("bold", "italic", "underline"):
            if name in raw or (name == "bold" and block_type == "heading"):
                result[name] = _format_flag(raw.get(name, block_type == "heading"))
    return result


def _text_format_classes(block: Mapping[str, Any], *, heading: bool = False) -> str:
    formatting = _normalize_text_format(block, str(block.get("type") or "paragraph"))
    classes = ["document-paragraph", f'document-align-{formatting["alignment"]}']
    if formatting.get("bold"):
        classes.append("document-bold")
    elif heading and "runs" not in block:
        classes.append("document-regular")
    if formatting.get("italic"):
        classes.append("document-italic")
    if formatting.get("underline"):
        classes.append("document-underline")
    return " ".join(classes)


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


def _sanitize_builder_content(value: Any) -> str:
    sanitized = sanitize_content_html(str(value or ""))
    return _JINJA_TOKEN_RE.sub(lambda match: html.unescape(match.group(0)), sanitized)


def _plain_label(value: Any) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(str(value or ""))).strip()


def _visible_inline(value: Any) -> bool:
    if isinstance(value, Mapping) and "runs" in value:
        text = "".join(str(run.get("text") or "") for run in value.get("runs") or [] if isinstance(run, Mapping))
    elif isinstance(value, Mapping):
        text = str(value.get("content") or "")
    else:
        text = str(value or "")
    return bool(re.sub(r"<[^>]+>", "", html.unescape(text)).strip())


def _context_names(fields: Iterable[Any], document_type: str) -> set[str]:
    if document_type == "agreement":
        return AgreementVariableCatalog.context_names(fields)
    from services.documents.declaration_template_context_service import DeclarationVariableCatalog

    return DeclarationVariableCatalog.context_names(fields)


def _find_variable_path(blocks: list[Mapping[str, Any]], variable: str) -> str:
    for index, block in enumerate(blocks):
        if variable in str(block):
            return f"blocks.{index}"
    return "blocks"


def _field_value(field: Any, name: str) -> str:
    value = field.get(name) if isinstance(field, Mapping) else getattr(field, name, None)
    return str(value or "").strip()


def _format_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _document_type(value: str) -> str:
    normalized = str(value or "agreement").strip().casefold()
    if normalized not in DOCUMENT_TYPES:
        raise ValueError(f"Nieobsługiwany typ dokumentu: {normalized or 'brak'}.")
    return normalized
