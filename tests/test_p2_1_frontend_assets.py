from pathlib import Path


def test_base_template_has_per_template_asset_blocks():
    template = Path("templates/base.html").read_text(encoding="utf-8")

    assert "{% block extra_css %}" in template
    assert "{% block extra_js %}" in template
    assert template.index("{% block extra_css %}") > template.index("style.css")
    assert template.index("{% block extra_js %}") > template.index("{% block content %}")


def test_documents_to_sign_static_assets_exist():
    stylesheet = Path("static/documents_to_sign.css")
    script = Path("static/documents_to_sign.js")

    assert stylesheet.exists()
    assert script.exists()
    assert ".upload-dropzone" in stylesheet.read_text(encoding="utf-8")
    assert "function checkAcceptanceStatus" in script.read_text(encoding="utf-8")
    assert "REJECTED_STATUSES" not in script.read_text(encoding="utf-8")
    assert "FINAL_STATUSES" not in script.read_text(encoding="utf-8")
    assert "message.includes" not in script.read_text(encoding="utf-8")


def test_documents_to_sign_template_loads_static_assets_only():
    template = Path("templates/documents_to_sign.html").read_text(encoding="utf-8")

    assert "documents_to_sign.css" in template
    assert "documents_to_sign.js" in template
    assert "{% block extra_css %}" in template
    assert "{% block extra_js %}" in template
    assert "<style" not in template
    assert "style=" not in template
    assert "<script src=\"{{ url_for('static', filename='documents_to_sign.js') }}\" defer></script>" in template


def test_documents_to_sign_frontend_uses_backend_status_flags():
    script = Path("static/documents_to_sign.js").read_text(encoding="utf-8")

    assert "Boolean(data.is_rejected)" in script
    assert "data.agreement_stage_completed" in script
    assert "data.declaration_stage_completed" in script
    assert "data.is_final" in script
    assert "rejectedStatuses" not in script
    assert "finalStatuses" not in script


def test_documents_to_sign_frontend_receives_acceptance_status_url_template():
    template = Path("templates/documents_to_sign.html").read_text(encoding="utf-8")
    script = Path("static/documents_to_sign.js").read_text(encoding="utf-8")

    assert "data-acceptance-status-url-template" in template
    assert "url_for('api.api_acceptance_status'" in template
    assert "buildAcceptanceStatusUrl" in script


def test_training_selection_keeps_full_width_layout():
    template = Path("templates/declaration_form.html").read_text(encoding="utf-8")
    stylesheet = Path("static/style.css").read_text(encoding="utf-8")

    assert "field.width or 'full'" in template
    assert "training-selection-row" in template
    assert ".training-selection-row" in stylesheet
    assert "grid-column: 1 / -1;" in stylesheet
    assert ".training-selection .checkbox-item span" in stylesheet
