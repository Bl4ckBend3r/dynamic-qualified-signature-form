from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADMIN_CSS = PROJECT_ROOT / "static" / "css" / "admin.css"
ADMIN_TEMPLATES = PROJECT_ROOT / "templates" / "admin"


def test_admin_css_defines_shared_spacing_scale_and_required_breakpoints():
    css = ADMIN_CSS.read_text(encoding="utf-8")

    for index, value in enumerate((4, 8, 12, 16, 24, 32), start=1):
        assert f"--admin-space-{index}: {value}px;" in css
    for breakpoint in (1024, 768, 480, 360):
        assert f"@media (max-width: {breakpoint}px)" in css


def test_admin_forms_actions_tables_and_modals_use_responsive_shared_rules():
    css = ADMIN_CSS.read_text(encoding="utf-8")

    assert ".admin-form label:not(.admin-check)" in css
    assert ".admin-form-actions {" in css
    assert "flex-wrap: wrap;" in css
    assert ".admin-table-wrap {" in css
    assert "overflow-x: auto;" in css
    assert "overscroll-behavior-x: contain;" in css
    assert "max-height: calc(100dvh -" in css
    assert ".admin-modal__body {" in css
    assert "overflow-y: auto;" in css


def test_mobile_modal_buttons_do_not_inherit_a_tall_flex_basis():
    css = ADMIN_CSS.read_text(encoding="utf-8")
    mobile_rules = css.split("@media (max-width: 480px)", 1)[1]

    assert ".admin-modal__footer > button {" in mobile_rules
    modal_button_rule = mobile_rules.split(
        ".admin-modal__footer > button {", 1
    )[1].split("}", 1)[0]
    assert "flex: 0 0 auto;" in modal_button_rule
    assert "min-height: 44px;" in modal_button_rule


def test_every_admin_table_uses_admin_table_and_scroll_wrapper():
    table_count = 0
    for template_path in ADMIN_TEMPLATES.rglob("*.html"):
        source = template_path.read_text(encoding="utf-8")
        assert '<table class="data-table"' not in source, template_path
        for match in re.finditer(r'<table class="admin-table"', source):
            table_count += 1
            nearby_prefix = source[max(0, match.start() - 240):match.start()]
            assert "admin-table-wrap" in nearby_prefix, template_path

    assert table_count >= 15


def test_static_admin_preview_layout_is_class_based():
    form_edit = (ADMIN_TEMPLATES / "forms" / "edit.html").read_text(encoding="utf-8")
    footer_edit = (ADMIN_TEMPLATES / "mail_footers" / "edit.html").read_text(encoding="utf-8")

    assert 'class="admin-form-header-logo admin-align-' in form_edit
    assert 'class="admin-form-header-logo__image"' in form_edit
    assert "max-width: 180px" not in form_edit
    assert 'class="admin-preview-logo admin-align-' in footer_edit
    assert "style=\"text-align:" not in footer_edit
