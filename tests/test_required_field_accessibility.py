from pathlib import Path
import re


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_required_field_note_is_present_in_public_and_configuration_forms():
    templates = (
        "templates/form_page.html",
        "templates/declaration_form.html",
        "templates/documents_to_sign.html",
        "templates/admin/forms/edit.html",
        "templates/admin/mail_templates/edit.html",
        "templates/admin/submissions/detail.html",
    )

    for template in templates:
        source = read(template)
        assert "Pola oznaczone" in source
        assert "są obowiązkowe." in source


def test_required_markers_are_hidden_and_required_controls_are_exposed_accessibly():
    public_sources = (
        read("templates/form_page.html"),
        read("templates/declaration_form.html"),
        read("templates/documents_to_sign.html"),
    )

    for source in public_sources:
        assert 'aria-hidden="true">*</span>' in source
        assert re.search(r'required\s+aria-required="true"', source)

    admin_base = read("templates/admin/base.html")
    assert 'field.setAttribute("aria-required", "true")' in admin_base
    assert 'marker.setAttribute("aria-hidden", "true")' in admin_base


def test_required_field_note_has_shared_public_and_admin_styles():
    public_css = read("static/css/style.css")
    admin_css = read("static/css/admin.css")

    assert ".form-required-note" in public_css
    assert ".required-marker" in public_css
    assert ".admin-required-note" in admin_css
    assert ".required-marker" in admin_css
