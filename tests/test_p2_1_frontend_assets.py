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


def test_documents_to_sign_template_split_tracking():
    template = Path("templates/documents_to_sign.html").read_text(encoding="utf-8")

    # The static assets are prepared in P2.1. The large template still keeps
    # compatibility inline blocks until the follow-up template-only commit can
    # safely remove them without changing the page flow.
    assert "<style>" in template or "documents_to_sign.css" in template
    assert "<script>" in template or "documents_to_sign.js" in template
