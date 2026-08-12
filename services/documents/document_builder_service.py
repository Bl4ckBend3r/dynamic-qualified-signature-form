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
MAX_LIST_LEVEL = 3
ORDERED_LIST_MARKERS = {
    "decimal-dot",
    "decimal-paren",
    "decimal-compound",
    "alpha-paren",
    "alpha-dot",
    "lower-roman-dot",
    "upper-roman-dot",
}
DEFAULT_ORDERED_LIST_STYLES = (
    {"level": 0, "marker": "decimal-dot", "indent_mm": 0},
    {"level": 1, "marker": "decimal-compound", "indent_mm": 7},
    {"level": 2, "marker": "alpha-paren", "indent_mm": 14},
    {"level": 3, "marker": "lower-roman-dot", "indent_mm": 21},
)
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
    "criteria_table",
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
        elif block_type in {"ordered_list", "bullet_list"}:
            normalized_items = []
            previous_level = 0
            for item_index, item in enumerate(block.get("items") if isinstance(block.get("items"), list) else []):
                holder = dict(item) if isinstance(item, Mapping) else {"content": item, "level": 0}
                _normalize_rich_text_holder(holder, "paragraph", include_format=False)
                try:
                    requested_level = max(0, min(int(holder.get("level") or 0), MAX_LIST_LEVEL))
                except (TypeError, ValueError):
                    requested_level = 0
                holder["level"] = 0 if item_index == 0 else min(requested_level, previous_level + 1)
                previous_level = holder["level"]
                normalized_items.append(holder)
            block["items"] = normalized_items
            if block_type == "ordered_list":
                block["list_styles"] = _normalize_ordered_list_styles(block.get("list_styles"))
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
        elif block_type == "criteria_table":
            criteria = []
            used = set()
            for item in block.get("criteria") if isinstance(block.get("criteria"), list) else []:
                field_key = str(item.get("field_key") if isinstance(item, Mapping) else item or "").strip()
                if field_key and field_key not in used:
                    criteria.append({"field_key": field_key})
                    used.add(field_key)
            block["criteria"] = criteria
            block["show_number"] = _format_flag(block.get("show_number", True))
            block["show_header"] = _format_flag(block.get("show_header", True))
    return {"version": BUILDER_VERSION, "document_type": normalized_type, "blocks": blocks}


def validate_document_builder_document(
    value: Any,
    fields: Iterable[Any] = (),
    document_type: str = "agreement",
    *,
    form_definition: Mapping[str, Any] | None = None,
) -> list[DocumentBuilderValidationError]:
    normalized_type = _document_type(document_type)
    fields = tuple(fields)
    raw_document = dict(value) if isinstance(value, Mapping) else {}
    raw_blocks = [block for block in raw_document.get("blocks") or [] if isinstance(block, Mapping)]
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
    criterion_fields: set[str] = set()
    if normalized_type == "declaration" and form_definition is not None:
        from services.documents.declaration_template_context_service import declaration_criteria_catalog

        criterion_fields = {item["field_key"] for item in declaration_criteria_catalog(form_definition, fields)}

    for index, block in enumerate(blocks):
        path = f"blocks.{index}"
        raw_block = raw_blocks[index] if index < len(raw_blocks) else block
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
            for item_error in _list_item_level_errors(raw_block.get("items")):
                errors.append(DocumentBuilderValidationError(path + ".items", item_error))
            if block_type == "ordered_list":
                styles_to_validate = raw_block.get("list_styles") if raw_block.get("list_styles") is not None else block.get("list_styles")
                for style_error in _ordered_list_style_errors(styles_to_validate):
                    errors.append(DocumentBuilderValidationError(path + ".list_styles", style_error))
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
        elif block_type == "criteria_table":
            criteria = block.get("criteria") or []
            if not criteria:
                errors.append(DocumentBuilderValidationError(path + ".criteria", "Wybierz co najmniej jedno kryterium kwalifikacyjne."))
            for criterion_index, criterion in enumerate(criteria):
                field_key = str(criterion.get("field_key") or "") if isinstance(criterion, Mapping) else ""
                if not _FIELD_NAME_RE.fullmatch(field_key):
                    errors.append(DocumentBuilderValidationError(f"{path}.criteria.{criterion_index}", "Kryterium ma niepoprawny klucz pola."))
                elif form_definition is not None and field_key not in criterion_fields:
                    errors.append(DocumentBuilderValidationError(f"{path}.criteria.{criterion_index}", "Wybierz aktywne kryterium z warunków kwalifikacyjnych formularza."))
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

    known = _context_names(fields, normalized_type, form_definition=form_definition) | set(environment.globals)
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
        return _render_list(block, ordered=block_type == "ordered_list")
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
    if block_type == "criteria_table":
        return _render_criteria_table(block)
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


def _render_list(block: Mapping[str, Any], *, ordered: bool) -> str:
    tag = "ol" if ordered else "ul"
    styles = _normalize_ordered_list_styles(block.get("list_styles")) if ordered else []
    counters = [0] * (MAX_LIST_LEVEL + 1)
    items = []
    for item in block.get("items") or []:
        level = max(0, min(int(item.get("level") or 0), MAX_LIST_LEVEL))
        if ordered:
            counters[level] += 1
            for deeper in range(level + 1, len(counters)):
                counters[deeper] = 0
            style = styles[level]
            marker = _ordered_list_marker(style["marker"], counters, level)
            indent_mm = style["indent_mm"]
            marker_html = f'<span class="document-list__marker" aria-hidden="true">{html.escape(marker)}</span>'
        else:
            indent_mm = level * 7
            marker_html = '<span class="document-list__marker" aria-hidden="true">&#8226;</span>'
        items.append(
            f'<li class="document-list__item document-list__item--level-{level}" '
            f'style="--document-list-indent: {_format_mm(indent_mm)}mm">'
            f'{marker_html}<span class="document-list__content">{_inline(item)}</span></li>'
        )
    return f'<{tag} class="document-list document-list--{"ordered" if ordered else "bullet"}">' + "".join(items) + f"</{tag}>"


def _render_criteria_table(block: Mapping[str, Any]) -> str:
    criteria = [str(item.get("field_key") or "") for item in block.get("criteria") or [] if isinstance(item, Mapping)]
    prefixes = [_criterion_variable_prefix(field_key) for field_key in criteria if _FIELD_NAME_RE.fullmatch(field_key)]
    other_expression = " or ".join(f"{prefix}_other_enabled" for prefix in prefixes) or "false"
    columns = []
    if block.get("show_number", True):
        columns.append('<th class="document-criteria-table__number">Lp.</th>')
    columns.extend([
        '<th class="document-criteria-table__criterion">Kryterium</th>',
        '<th class="document-criteria-table__choice">TAK</th>',
        '<th class="document-criteria-table__choice">NIE</th>',
        f'{{% if {other_expression} %}}<th class="document-criteria-table__other">Inna odpowiedź</th>{{% endif %}}',
    ])
    header = "<thead><tr>" + "".join(columns) + "</tr></thead>" if block.get("show_header", True) else ""
    rows = []
    for index, prefix in enumerate(prefixes, start=1):
        cells = []
        if block.get("show_number", True):
            cells.append(f'<td class="document-criteria-table__number">{index}.</td>')
        cells.extend([
            f'<td class="document-criteria-table__criterion">{{{{ {prefix}_label }}}}</td>',
            f'<td class="document-criteria-table__choice"><span class="document-checkbox">{{{{ {prefix}_yes_checked }}}}</span></td>',
            f'<td class="document-criteria-table__choice"><span class="document-checkbox">{{{{ {prefix}_no_checked }}}}</span></td>',
            f'{{% if {other_expression} %}}<td class="document-criteria-table__other"><div class="document-criteria-table__other-value"><span class="document-checkbox">{{{{ {prefix}_other_checked }}}}</span><span>{{{{ {prefix}_other_label }}}}</span></div></td>{{% endif %}}',
        ])
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return '<table class="document-table document-criteria-table">' + header + "<tbody>" + "".join(rows) + "</tbody></table>"


def _normalize_rich_text_holder(holder: dict[str, Any], block_type: str, *, include_format: bool = True) -> None:
    if "runs" in holder:
        holder["runs"] = normalize_inline_runs(holder.get("runs"))
        holder["content"] = "".join(run["text"] for run in holder["runs"])
    else:
        holder["content"] = _sanitize_builder_content(holder.get("content"))
    if include_format:
        holder["format"] = _normalize_text_format(holder, block_type)
        holder.pop("alignment", None)


def _normalize_ordered_list_styles(value: Any) -> list[dict[str, Any]]:
    raw_by_level = {
        int(item.get("level")): item
        for item in value if isinstance(item, Mapping) and str(item.get("level", "")).lstrip("-").isdigit()
    } if isinstance(value, list) else {}
    result = []
    for default in DEFAULT_ORDERED_LIST_STYLES:
        level = default["level"]
        raw = raw_by_level.get(level, {})
        marker = str(raw.get("marker") or default["marker"]).strip().casefold()
        try:
            indent_mm = float(raw.get("indent_mm", default["indent_mm"]))
        except (TypeError, ValueError):
            indent_mm = float(default["indent_mm"])
        result.append({
            "level": level,
            "marker": marker if marker in ORDERED_LIST_MARKERS else default["marker"],
            "indent_mm": max(0, min(indent_mm, 60)),
        })
    return result


def _ordered_list_style_errors(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) != MAX_LIST_LEVEL + 1:
        return ["Lista numerowana wymaga ustawień dla poziomów 1–4."]
    levels = set()
    errors = []
    for item in value:
        if not isinstance(item, Mapping):
            errors.append("Ustawienie poziomu listy musi być obiektem.")
            continue
        try:
            level = int(item.get("level"))
            indent_mm = float(item.get("indent_mm"))
        except (TypeError, ValueError):
            errors.append("Poziom i wcięcie listy muszą być liczbami.")
            continue
        levels.add(level)
        if level < 0 or level > MAX_LIST_LEVEL:
            errors.append("Poziom listy musi mieścić się w zakresie 1–4.")
        if str(item.get("marker") or "") not in ORDERED_LIST_MARKERS:
            errors.append("Wybierz obsługiwany marker listy numerowanej.")
        if indent_mm < 0 or indent_mm > 60:
            errors.append("Wcięcie listy musi mieścić się w zakresie 0–60 mm.")
    if levels != set(range(MAX_LIST_LEVEL + 1)):
        errors.append("Każdy poziom listy 1–4 musi mieć dokładnie jedno ustawienie.")
    return list(dict.fromkeys(errors))


def _list_item_level_errors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    errors = []
    previous_level = 0
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        try:
            level = int(item.get("level") or 0)
        except (TypeError, ValueError):
            errors.append(f"Punkt {index + 1}: poziom listy musi być liczbą.")
            continue
        if level < 0 or level > MAX_LIST_LEVEL:
            errors.append(f"Punkt {index + 1}: poziom listy musi mieścić się w zakresie 1–4.")
        if index == 0 and level != 0:
            errors.append("Pierwszy punkt listy musi być na poziomie 1.")
        elif index > 0 and level > previous_level + 1:
            errors.append(f"Punkt {index + 1}: nie można pominąć poziomu listy.")
        previous_level = max(0, min(level, MAX_LIST_LEVEL))
    return errors


def _ordered_list_marker(marker: str, counters: list[int], level: int) -> str:
    value = counters[level]
    if marker == "decimal-paren":
        return f"{value})"
    if marker == "decimal-compound":
        return ".".join(str(counter) for counter in counters[:level + 1])
    if marker in {"alpha-paren", "alpha-dot"}:
        return _alpha_number(value) + (")" if marker == "alpha-paren" else ".")
    if marker in {"lower-roman-dot", "upper-roman-dot"}:
        roman = _roman_number(value)
        return (roman.lower() if marker == "lower-roman-dot" else roman) + "."
    return f"{value}."


def _alpha_number(value: int) -> str:
    result = ""
    number = max(1, value)
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(97 + remainder) + result
    return result


def _roman_number(value: int) -> str:
    number = max(1, value)
    result = ""
    for amount, symbol in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        count, number = divmod(number, amount)
        result += symbol * count
    return result


def _format_mm(value: Any) -> str:
    number = max(0, min(float(value or 0), 60))
    return str(int(number)) if number.is_integer() else f"{number:.1f}".rstrip("0").rstrip(".")


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
    if run.get("underline"):
        rendered = f'<span class="document-text-underline">{rendered}</span>'
    if run.get("italic"):
        rendered = f"<em>{rendered}</em>"
    if run.get("bold"):
        rendered = f"<strong>{rendered}</strong>"
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


def _context_names(
    fields: Iterable[Any],
    document_type: str,
    *,
    form_definition: Mapping[str, Any] | None = None,
) -> set[str]:
    if document_type == "agreement":
        return AgreementVariableCatalog.context_names(fields)
    from services.documents.declaration_template_context_service import DeclarationVariableCatalog

    return DeclarationVariableCatalog.context_names(fields, form_definition=form_definition)


def _criterion_variable_prefix(field_key: str) -> str:
    normalized = "".join(character if character.isascii() and (character.isalnum() or character == "_") else "_" for character in field_key)
    normalized = normalized.strip("_") or "field"
    if normalized[0].isdigit():
        normalized = "field_" + normalized
    return "criterion_" + normalized


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
