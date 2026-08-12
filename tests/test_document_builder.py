from io import BytesIO
from pathlib import Path

from docx import Document
from jinja2.sandbox import SandboxedEnvironment

from services.documents.declaration_template_context_service import (
    DeclarationVariableCatalog,
    build_declaration_render_context,
    declaration_builder_variable_catalog,
    declaration_criteria_catalog,
    declaration_variable_catalog,
)
from services.documents.document_builder_service import (
    normalize_document_builder_document,
    render_document_builder_template,
    validate_document_builder_document,
)
from services.documents.docx_template_parser import parse_docx_template
from services.form_config_service import FormConfigService


def test_inline_runs_are_merged_persisted_and_rendered_without_breaking_jinja():
    document = {
        "version": 1,
        "document_type": "agreement",
        "blocks": [{
            "type": "paragraph",
            "format": {"alignment": "justify"},
            "runs": [
                {"text": "Uczestnik: ", "bold": False},
                {"text": "{{ participant_name }}", "bold": True},
                {"text": " podpisuje", "bold": True},
                {"text": " umowę", "italic": True, "underline": True},
            ],
        }],
    }

    normalized = normalize_document_builder_document(document, "agreement")
    template = render_document_builder_template(normalized, "agreement")

    assert len(normalized["blocks"][0]["runs"]) == 3
    assert normalized["blocks"][0]["runs"][1]["text"] == "{{ participant_name }} podpisuje"
    assert "<strong>{{ participant_name }} podpisuje</strong>" in template
    assert 'class="document-text-underline"' in template
    assert "document-align-justify" in template
    assert validate_document_builder_document(normalized, document_type="agreement") == []
    rendered = SandboxedEnvironment(autoescape=True).from_string(template).render(participant_name="Jan Kowalski")
    assert "Jan Kowalski" in rendered


def test_inline_renderer_formats_only_requested_legal_phrase_in_stable_wrapper_order():
    document = {
        "version": 1,
        "document_type": "agreement",
        "blocks": [{
            "type": "paragraph",
            "format": {"alignment": "justify"},
            "runs": [
                {"text": "Beneficjent zobowiązuje się przestrzegać postanowień "},
                {
                    "text": "Regulaminu projektu",
                    "bold": True,
                    "italic": True,
                    "underline": True,
                },
                {"text": "."},
            ],
        }],
    }

    template = render_document_builder_template(document, "agreement")

    expected = (
        "Beneficjent zobowiązuje się przestrzegać postanowień "
        '<strong><em><span class="document-text-underline">'
        "Regulaminu projektu"
        "</span></em></strong>."
    )
    assert expected in template
    assert template.count("<strong>") == 1


def test_inline_renderer_keeps_whole_jinja_variable_inside_marked_run():
    document = {
        "version": 1,
        "document_type": "agreement",
        "blocks": [{
            "type": "paragraph",
            "runs": [
                {"text": "Uczestnik: "},
                {"text": "{{ participant_name }}", "bold": True},
            ],
        }],
    }

    template = render_document_builder_template(document, "agreement")
    rendered = SandboxedEnvironment(autoescape=True).from_string(template).render(participant_name="Jan Kowalski")

    assert "<strong>{{ participant_name }}</strong>" in template
    assert "<strong>Jan Kowalski</strong>" in rendered


def test_shared_document_css_does_not_reset_semantic_bold_to_normal():
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "document_template.css").read_text(encoding="utf-8")

    assert ".document strong," in css
    assert ".document b," in css
    assert "font-weight: 700;" in css.split(".document strong,", 2)[-1].split("}", 1)[0]


def test_ordered_list_hierarchy_styles_round_trip_and_render_markers():
    document = {
        "version": 1,
        "blocks": [{
            "type": "ordered_list",
            "items": [
                {"content": "Pierwszy", "level": 0},
                {"content": "Pierwszy podpunkt", "level": 1},
                {"content": "Głębiej", "level": 2},
                {"content": "Drugi podpunkt", "level": 1},
                {"content": "Drugi", "level": 0},
            ],
            "list_styles": [
                {"level": 0, "marker": "decimal-dot", "indent_mm": 0},
                {"level": 1, "marker": "decimal-compound", "indent_mm": 8},
                {"level": 2, "marker": "alpha-paren", "indent_mm": 16},
                {"level": 3, "marker": "upper-roman-dot", "indent_mm": 24},
            ],
        }],
    }

    normalized = normalize_document_builder_document(document, "agreement")
    template = render_document_builder_template(normalized, "agreement")

    assert normalize_document_builder_document(normalized, "agreement") == normalized
    assert [item["level"] for item in normalized["blocks"][0]["items"]] == [0, 1, 2, 1, 0]
    assert [style["marker"] for style in normalized["blocks"][0]["list_styles"]] == [
        "decimal-dot", "decimal-compound", "alpha-paren", "upper-roman-dot"
    ]
    assert [marker in template for marker in (">1.</span>", ">1.1</span>", ">a)</span>", ">1.2</span>", ">2.</span>")] == [True] * 5
    assert "--document-list-indent: 16mm" in template
    assert validate_document_builder_document(normalized, document_type="agreement") == []


def test_ordered_list_rejects_level_jumps_and_invalid_configuration():
    invalid = {
        "version": 1,
        "blocks": [{
            "type": "ordered_list",
            "items": [{"content": "A", "level": 0}, {"content": "B", "level": 3}],
            "list_styles": [
                {"level": 0, "marker": "unknown", "indent_mm": -1},
                {"level": 1, "marker": "decimal-dot", "indent_mm": 7},
            ],
        }],
    }

    errors = validate_document_builder_document(invalid, document_type="agreement")

    assert any("pominąć poziomu" in error.message for error in errors)
    assert any("poziomów 1–4" in error.message or "marker" in error.message.lower() for error in errors)


def test_declaration_criteria_table_uses_qualification_source_and_tri_state_context():
    fields = [
        {"name": "zgoda", "label": "Zgoda formularza", "document_label": "Zgoda w deklaracji", "type": "checkbox"},
        {
            "name": "status",
            "label": "Status uczestnika",
            "type": "radio",
            "options": [
                {"value": "tak", "label": "Tak"},
                {"value": "nie", "label": "Nie"},
                {"value": "odmowa", "label": "Odmowa podania informacji"},
            ],
        },
    ]
    definition = {
        "fields": fields,
        "qualification_conditions": {
            "enabled": True,
            "conditions": [
                {"field_name": "zgoda", "is_active": True},
                {"field_name": "status", "is_active": True},
                {"field_name": "pomijane", "is_active": False},
            ],
        },
    }
    criteria = declaration_criteria_catalog(definition, fields)
    builder_catalog = declaration_builder_variable_catalog(fields, form_definition=definition)
    document = {
        "version": 1,
        "blocks": [{
            "type": "criteria_table",
            "criteria": [{"field_key": "zgoda", "label": "stara kopia"}, {"field_key": "status"}],
            "show_number": True,
            "show_header": True,
        }],
    }

    normalized = normalize_document_builder_document(document, "declaration")
    assert [item["field_key"] for item in criteria] == ["zgoda", "status"]
    assert criteria[0]["label"] == "Zgoda w deklaracji"
    assert criteria[1]["answer_type"] == "choice"
    assert any(item["type"] == "criterion" and item["field_key"] == "status" for item in builder_catalog)
    assert normalized["blocks"][0]["criteria"] == [{"field_key": "zgoda"}, {"field_key": "status"}]
    assert validate_document_builder_document(
        normalized,
        fields,
        "declaration",
        form_definition=definition,
    ) == []

    template = render_document_builder_template(normalized, "declaration")
    context = build_declaration_render_context(
        {"zgoda": None, "status": "odmowa"},
        form_definition=definition,
        fields=fields,
    )
    rendered = SandboxedEnvironment(autoescape=True).from_string(template).render(**context)

    assert context["criterion_zgoda_yes_checked"] == ""
    assert context["criterion_zgoda_no_checked"] == ""
    assert context["criterion_status_other_enabled"] == "X"
    assert context["criterion_status_other_checked"] == "X"
    assert context["criterion_status_other_label"] == "Odmowa podania informacji"
    assert "Zgoda w deklaracji" in rendered
    assert "Odmowa podania informacji" in rendered


def test_declaration_builder_uses_form_fields_and_rejects_training_blocks():
    fields = [{"name": "zgoda", "label": "Zgoda na udział", "type": "checkbox"}]
    document = {
        "version": 1,
        "blocks": [
            {"type": "heading", "level": 1, "runs": [{"text": "Deklaracja", "bold": True}]},
            {"type": "form_field", "field": "zgoda", "label": "Zgoda na udział", "display": "yes_no"},
            {"type": "statement", "runs": [{"text": "Potwierdzam prawdziwość danych."}]},
            {"type": "participant_signature", "label": "Podpis uczestnika"},
        ],
    }

    assert validate_document_builder_document(document, fields, "declaration") == []
    template = render_document_builder_template(document, "declaration")
    assert "{{ zgoda_yes_no }}" in template
    assert "document--declaration" in template

    invalid = {"version": 1, "blocks": [{"type": "training_table", "columns": ["name"]}]}
    errors = validate_document_builder_document(invalid, fields, "declaration")
    assert errors and "Nieobsługiwany typ bloku" in errors[0].message


def test_declaration_context_has_boolean_representations_and_no_training_catalog():
    fields = [
        {"name": "zgoda", "label": "Zgoda", "type": "checkbox"},
        {"name": "tematy", "label": "Tematy", "type": "multi_select"},
    ]
    context = build_declaration_render_context(
        {"zgoda": "on", "tematy": ["Kadry", "Płace"], "imiona": "Anna", "nazwisko": "Nowak"},
        form_definition={"title": "Projekt", "fields": fields},
        fields=fields,
    )
    names = DeclarationVariableCatalog.context_names(fields)
    categories = {item["category"] for item in declaration_variable_catalog(fields)}

    assert context["zgoda_display"] == "Tak"
    assert context["zgoda_yes_no"] == "Tak"
    assert context["tematy_display"] == "Kadry, Płace"
    assert context["selected_trainings"] == []
    assert "training_name" not in names
    assert "selected_trainings" not in names
    assert "Deklaracja" not in categories
    assert "Zgłoszenie" in categories


def test_docx_import_preserves_inline_styles_and_whole_split_jinja_token():
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("Dane ")
    bold = paragraph.add_run("uczestnika ")
    bold.bold = True
    token_open = paragraph.add_run("{{")
    token_open.bold = True
    token_middle = paragraph.add_run(" participant_")
    token_middle.italic = True
    token_end = paragraph.add_run("name }}")
    token_end.underline = True
    suffix = paragraph.add_run(" ważne")
    suffix.italic = True
    buffer = BytesIO()
    document.save(buffer)

    parsed = parse_docx_template(buffer.getvalue(), document_type="declaration")
    block = parsed.builder_document["blocks"][0]

    assert parsed.variables == ("participant_name",)
    assert "{{ participant_name }}" in parsed.html
    assert "&#123;&#123;" not in parsed.html
    assert any(run["bold"] and "uczestnika" in run["text"] for run in block["runs"])
    assert any(run["italic"] and "ważne" in run["text"] for run in block["runs"])
    SandboxedEnvironment(autoescape=True).parse(parsed.html)


def test_form_config_supports_shared_builder_for_declaration_and_legacy_html():
    service = FormConfigService()
    fresh = service.normalize_form_config({"workflow": {"requires_declaration": True}})
    legacy = service.normalize_form_config({"workflow": {"requires_declaration": True, "declaration_template_html": "<p>Legacy</p>"}})

    assert fresh["workflow"]["declaration_template_source"] == "builder"
    declaration = next(item for item in fresh["documents"] if item["id"] == "declaration")
    assert declaration["template_source"] == "builder"
    assert declaration["builder_document"]["document_type"] == "declaration"
    assert legacy["workflow"]["declaration_template_source"] == "html"
    assert next(item for item in legacy["documents"] if item["id"] == "declaration")["template_html"] == "<p>Legacy</p>"


def test_form_config_preserves_selected_template_sources_for_both_document_types():
    service = FormConfigService()
    normalized = service.normalize_form_config({
        "workflow": {
            "requires_contract": True,
            "requires_declaration": True,
            "contract_template_source": "docx",
            "declaration_template_source": "html",
            "contract_docx_template": {"storage_path": "templates/agreement.docx"},
            "declaration_template_html": "<p>{{ participant_name }}</p>",
        }
    })

    assert normalized["workflow"]["contract_template_source"] == "docx"
    assert normalized["workflow"]["declaration_template_source"] == "html"
    agreement = next(item for item in normalized["documents"] if item["id"] == "agreement")
    declaration = next(item for item in normalized["documents"] if item["id"] == "declaration")
    assert agreement["template_source"] == "docx"
    assert declaration["template_source"] == "html"
