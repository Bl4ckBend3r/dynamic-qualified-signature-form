from pathlib import Path
import re

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADMIN_CSS = PROJECT_ROOT / "static" / "css" / "admin.css"
STYLE_CSS = PROJECT_ROOT / "static" / "css" / "style.css"
FORM_BUILDER_CSS = PROJECT_ROOT / "static" / "css" / "form_builder.css"
FORM_BUILDER_PROPERTIES_JS = PROJECT_ROOT / "static" / "js" / "form_builder" / "properties-panel.js"
AGREEMENT_BUILDER_JS = PROJECT_ROOT / "static" / "js" / "agreement_builder.js"
AGREEMENT_BUILDER_TEMPLATE = PROJECT_ROOT / "templates" / "admin" / "forms" / "_agreement_builder.html"
ADMIN_TEMPLATES = PROJECT_ROOT / "templates" / "admin"


def test_admin_css_defines_shared_spacing_scale_and_required_breakpoints():
    css = ADMIN_CSS.read_text(encoding="utf-8")

    for index, value in enumerate((4, 8, 12, 16, 24, 32), start=1):
        assert f"--admin-space-{index}: {value}px;" in css
    for breakpoint in (1024, 768, 480, 360):
        assert f"@media (max-width: {breakpoint}px)" in css


def test_primary_admin_button_uses_the_darker_shared_gold_token():
    css = ADMIN_CSS.read_text(encoding="utf-8")
    primary_rule = css.split(".admin-button {", 1)[1].split("}", 1)[0]

    assert "background: var(--gold-dark);" in primary_rule
    assert "border: 1px solid var(--gold-dark);" in primary_rule
    assert '.admin-button:hover:not(:disabled):not([aria-disabled="true"])' in css
    assert ".admin-button:focus-visible" in css
    assert ".admin-button:disabled" in css


def test_admin_tables_typography_focus_and_tabs_have_accessible_css_contracts():
    css = ADMIN_CSS.read_text(encoding="utf-8")

    assert css.count("letter-spacing: 0.05em;") >= 3
    assert ".admin-table tbody tr:nth-child(even) td" in css
    sticky_header = css.split(".admin-table thead th {", 1)[1].split("}", 1)[0]
    assert "position: sticky;" in sticky_header
    assert "top: 0;" in sticky_header
    assert "z-index: 2;" in sticky_header
    assert "background-color: var(--surface-alt);" in sticky_header
    assert ".admin-form input:focus-visible" in css
    assert "box-shadow: 0 0 0 4px rgba(200, 163, 93, 0.16);" in css
    assert '.admin-form input[aria-invalid="true"]:focus-visible' in css
    tabs = css.split(".admin-form-tabs {", 1)[1].split("}", 1)[0]
    assert "padding-bottom: calc(var(--admin-space-2) + 6px);" in tabs
    assert "scrollbar-color: var(--muted) transparent;" in tabs
    assert ".admin-form-tabs::-webkit-scrollbar" in css
    assert "height: 6px;" in css.split(".admin-form-tabs::-webkit-scrollbar {", 1)[1].split("}", 1)[0]


def test_form_builder_properties_options_and_tools_use_shared_spacing_and_color_tokens():
    css = FORM_BUILDER_CSS.read_text(encoding="utf-8")

    properties = css.split(".form-builder__properties label {", 1)[1].split("}", 1)[0]
    widths = css.split(".form-builder__widths > div {", 1)[1].split("}", 1)[0]
    options = css.split(".form-builder__option-preview {", 1)[1].split("}", 1)[0]
    tools = css.split(".form-builder__field-tools button {", 1)[1].split("}", 1)[0]
    assert "gap: var(--admin-space-2);" in properties
    assert "margin-top: var(--admin-space-4);" in properties
    assert "gap: var(--admin-space-2);" in widths
    assert "align-items: flex-start;" in options
    assert "color: var(--navy);" in tools
    assert ".form-builder__field-tools button:focus-visible" in css
    assert ".form-builder__field-tools button:disabled" in css


def test_form_builder_availability_uses_stage_blocks_with_clickable_checkbox_labels():
    css = FORM_BUILDER_CSS.read_text(encoding="utf-8")
    script = FORM_BUILDER_PROPERTIES_JS.read_text(encoding="utf-8")

    workspace = css.split(".form-builder__workspace {", 1)[1].split("}", 1)[0]
    controls = css.split(".form-builder__availability-controls {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: 200px minmax(0, 1fr) minmax(340px, 380px);" in workspace
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in controls
    assert "form-builder__availability-stage" in script
    assert "form-builder__availability-controls" in script
    assert "form-builder__availability-option" in script
    assert "wrapper.append(checkbox, document.createTextNode(permissionLabel));" in script
    assert 'permissions = [' in script
    for permission, label in (
        ("visible", "Widoczne"),
        ("editable", "Edytowalne"),
        ("required", "Wymagane"),
    ):
        assert f'["{permission}", "{label}"]' in script


def test_form_builder_properties_panel_fits_required_viewports():
    playwright_api = pytest.importorskip("playwright.sync_api")
    css = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (STYLE_CSS, ADMIN_CSS, FORM_BUILDER_CSS)
    )
    stage_names = (
        "Wniosek złożony",
        "Oczekuje na decyzję urzędnika",
        "Deklaracja gotowa",
        "Deklaracja oczekuje na podpis",
        "Umowa gotowa do podpisu",
        "Umowa oczekuje na podpis",
        "Zakończone",
        "Archiwizacja dokumentacji sprawy",
    )
    stages = "".join(
        '<div class="form-builder__availability-row">'
        f'<strong class="form-builder__availability-stage">{name}</strong>'
        '<div class="form-builder__availability-controls">'
        '<label class="form-builder__availability-option"><input type="checkbox">Widoczne</label>'
        '<label class="form-builder__availability-option"><input type="checkbox">Edytowalne</label>'
        '<label class="form-builder__availability-option"><input type="checkbox">Wymagane</label>'
        "</div></div>"
        for name in stage_names
    )
    widths = "".join(f'<button type="button">{width}</button>' for width in ("100%", "50%", "25%", "33%", "75%", "66%"))
    html = f"""<!doctype html><html><head><style>{css}</style></head><body>
      <main class="admin-main"><div class="admin-container">
        <form class="form-builder"><div class="form-builder__workspace">
          <aside class="form-builder__palette"><h3>Pola</h3></aside>
          <main class="form-builder__canvas-panel"><div class="form-builder__canvas">
            <div class="form-builder__field" id="field-full" style="--field-span:12">100%</div>
            <div class="form-builder__field" id="field-half" style="--field-span:6">50%</div>
            <div class="form-builder__field" id="field-quarter" style="--field-span:3">25%</div>
          </div></main>
          <aside class="form-builder__properties"><div data-properties-content>
            <label><span>Etykieta</span><input value="Długa etykieta pola formularza"></label>
            <label><span>Typ</span><select><option>Tekst</option></select></label>
            <fieldset class="form-builder__widths"><legend>Szerokość</legend><div>{widths}</div></fieldset>
            <fieldset class="form-builder__availability"><legend>Dostępność w etapach</legend>
              <p>Określ zachowanie pola w każdym etapie.</p><div>{stages}</div>
            </fieldset>
          </div></aside>
        </div></form>
      </div></main></body></html>"""

    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except playwright_api.Error as exception:
            pytest.skip(f"Brak przeglądarki Playwright: {exception}")
        page = browser.new_page(viewport={"width": 1920, "height": 900})
        page.set_content(html)
        for width in (1920, 1600, 1366, 1280, 1024, 768):
            page.set_viewport_size({"width": width, "height": 900})
            metrics = page.evaluate("""() => {
              const workspace = document.querySelector('.form-builder__workspace');
              const properties = document.querySelector('.form-builder__properties');
              const availability = document.querySelector('.form-builder__availability');
              const propertyBounds = properties.getBoundingClientRect();
              const rows = [...document.querySelectorAll('.form-builder__availability-row')];
              const controls = [...document.querySelectorAll('.form-builder__availability-controls')];
              const overflowedDescendants = [...properties.querySelectorAll('input, select, textarea, button, label, strong, fieldset, div')].filter((element) => {
                const bounds = element.getBoundingClientRect();
                return bounds.right > propertyBounds.right + 1 || bounds.left < propertyBounds.left - 1;
              }).map((element) => `${element.tagName}.${element.className}`);
              return {
                documentFits: document.documentElement.scrollWidth <= window.innerWidth,
                workspaceFits: workspace.scrollWidth <= workspace.clientWidth,
                propertiesWidth: propertyBounds.width,
                fieldWidths: ['#field-full', '#field-half', '#field-quarter'].map(
                  (selector) => document.querySelector(selector).getBoundingClientRect().width
                ),
                propertiesFit: properties.scrollWidth <= properties.clientWidth,
                availabilityFit: availability.scrollWidth <= availability.clientWidth,
                controlsFit: controls.every((element) => element.scrollWidth <= element.clientWidth),
                overflowedDescendants,
                rowCount: rows.length,
                everyRowHasThreeLabels: rows.every((row) => row.querySelectorAll('label').length === 3),
                workspaceColumns: getComputedStyle(workspace).gridTemplateColumns.split(' ').length,
              };
            }""")
            assert metrics["documentFits"] is True
            assert metrics["workspaceFits"] is True
            assert metrics["propertiesFit"] is True
            assert metrics["availabilityFit"] is True
            assert metrics["controlsFit"] is True
            assert metrics["overflowedDescendants"] == [], (width, metrics["overflowedDescendants"])
            assert metrics["rowCount"] == 8
            assert metrics["everyRowHasThreeLabels"] is True
            assert metrics["fieldWidths"][0] > metrics["fieldWidths"][1] > metrics["fieldWidths"][2]
            if width >= 1024:
                assert metrics["workspaceColumns"] == 3
                assert 339 <= metrics["propertiesWidth"] <= 381
            else:
                assert metrics["workspaceColumns"] == 2
        browser.close()


def test_admin_css_computed_layout_at_required_viewports():
    playwright_api = pytest.importorskip("playwright.sync_api")
    css = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (STYLE_CSS, ADMIN_CSS, FORM_BUILDER_CSS)
    )
    tabs = "".join(f'<button class="admin-form-tab">Zakładka {index}</button>' for index in range(16))
    rows = "".join(
        f"<tr><td>Wiersz {index}</td><td>Długa zawartość tabeli {index}</td></tr>"
        for index in range(1, 21)
    )
    html = f"""<!doctype html><html><head><style>{css}</style></head>
    <body class="admin-body"><main class="admin-container">
      <button class="admin-button" id="primary">Zapisz</button>
      <nav class="admin-form-tabs" id="tabs">{tabs}</nav>
      <form class="admin-form"><input id="regular"><label class="has-error"><input id="invalid" aria-invalid="true"></label></form>
      <div class="admin-table-wrap" id="table-wrap" style="max-height:160px;overflow:auto">
        <table class="admin-table"><thead><tr><th>Nagłówek</th><th>Dane</th></tr></thead><tbody>{rows}</tbody></table>
      </div>
      <div class="form-builder"><div class="form-builder__field is-selected"><div class="form-builder__field-tools"><button id="tool">⋮</button></div>
        <div class="form-builder__option-preview"><span>Bardzo długa wielowierszowa etykieta opcji formularza</span></div></div>
        <label class="form-builder__checkbox" id="builder-check"><input type="checkbox"><span>Długa etykieta checkboxa zawijana do wielu wierszy</span></label>
      </div>
    </main></body></html>"""

    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except playwright_api.Error as exception:
            pytest.skip(f"Brak przeglądarki Playwright: {exception}")
        page = browser.new_page(viewport={"width": 1920, "height": 900})
        page.set_content(html)
        for width in (1920, 1366, 1024, 768):
            page.set_viewport_size({"width": width, "height": 900})
            metrics = page.evaluate("""() => {
              const style = (selector) => getComputedStyle(document.querySelector(selector));
              const tabs = document.querySelector('#tabs');
              return {
                bodyFits: document.documentElement.scrollWidth <= window.innerWidth,
                buttonBackground: style('#primary').backgroundColor,
                buttonBorder: style('#primary').borderColor,
                buttonColor: style('#primary').color,
                evenRow: style('.admin-table tbody tr:nth-child(even) td').backgroundColor,
                headerPosition: style('.admin-table thead th').position,
                headerZ: style('.admin-table thead th').zIndex,
                tabsOverflow: tabs.scrollWidth > tabs.clientWidth,
                tabsPaddingBottom: style('#tabs').paddingBottom,
                optionAlignment: style('.form-builder__option-preview').alignItems,
                checkboxAlignment: style('#builder-check').alignItems,
                toolColor: style('#tool').color,
              };
            }""")
            assert metrics["bodyFits"] is True
            assert metrics["buttonBackground"] == "rgb(179, 141, 69)"
            assert metrics["buttonBorder"] == "rgb(179, 141, 69)"
            assert metrics["buttonColor"] == "rgb(255, 255, 255)"
            assert metrics["evenRow"] == "rgb(247, 243, 236)"
            assert metrics["headerPosition"] == "sticky"
            assert metrics["headerZ"] == "2"
            assert metrics["tabsPaddingBottom"] == "14px"
            assert metrics["optionAlignment"] == "flex-start"
            assert metrics["checkboxAlignment"] == "flex-start"
            assert metrics["toolColor"] == "rgb(29, 46, 91)"
            if width == 768:
                assert metrics["tabsOverflow"] is True

        primary = page.locator("#primary")
        page.evaluate("document.activeElement.blur()")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "primary"
        focus_style = page.evaluate("""() => {
          const style = getComputedStyle(document.querySelector('#primary'));
          return [style.outlineColor, style.outlineStyle, style.outlineWidth];
        }""")
        assert focus_style == ["rgb(29, 46, 91)", "solid", "3px"]

        for _ in range(20):
            if page.evaluate("document.activeElement.id") == "regular":
                break
            page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "regular"
        page.wait_for_timeout(250)
        assert page.evaluate("getComputedStyle(document.querySelector('#regular')).borderColor") == "rgb(200, 163, 93)"
        assert "rgba(200, 163, 93, 0.16)" in page.evaluate("getComputedStyle(document.querySelector('#regular')).boxShadow")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "invalid"
        page.wait_for_timeout(250)
        assert page.evaluate("getComputedStyle(document.querySelector('#invalid')).borderColor") == "rgb(180, 35, 24)"

        primary.hover()
        assert page.evaluate("getComputedStyle(document.querySelector('#primary')).backgroundColor") == "rgb(29, 46, 91)"
        primary.evaluate("element => { element.disabled = true; }")
        disabled_style = page.evaluate("""() => {
          const style = getComputedStyle(document.querySelector('#primary'));
          return [style.backgroundColor, style.color, style.cursor];
        }""")
        assert disabled_style == ["rgb(247, 243, 236)", "rgb(110, 118, 138)", "not-allowed"]

        table_wrap = page.locator("#table-wrap")
        header = page.locator(".admin-table thead th").first
        header_top = header.bounding_box()["y"]
        table_wrap.evaluate("element => { element.scrollTop = 120; }")
        assert abs(header.bounding_box()["y"] - header_top) <= 1
        browser.close()


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


def test_standalone_agreement_builder_has_bounded_panels_and_a4_viewport():
    css = ADMIN_CSS.read_text(encoding="utf-8")
    template = AGREEMENT_BUILDER_TEMPLATE.read_text(encoding="utf-8")

    assert "grid-template-rows: auto auto auto minmax(0, 1fr);" in css
    assert ".agreement-builder--standalone .agreement-builder__workspace" in css
    assert "overflow: hidden;" in css
    assert "scrollbar-gutter: stable;" in css
    assert ".agreement-preview-viewport" in css
    assert "width: max-content;" in css
    assert ".agreement-preview-page" in css
    assert "width: 210mm;" in css
    assert "min-height: 297mm;" in css
    assert 'data-builder-preview-viewport' in template
    assert 'data-builder-preview-canvas' in template
    assert 'data-agreement-builder-preview-page' in template


def test_agreement_builder_uses_one_ui_state_for_modes_panels_and_zoom():
    script = AGREEMENT_BUILDER_JS.read_text(encoding="utf-8")
    template = AGREEMENT_BUILDER_TEMPLATE.read_text(encoding="utf-8")

    assert 'viewMode: "split"' in script
    assert "variablesVisible: true" in script
    assert 'workspaceSizeMode: "balanced"' in script
    assert "previewZoom: 0.8" in script
    assert "fitWidth: false" in script
    assert "function applyBuilderLayout" in script
    assert 'data-builder-view-mode="split"' in template
    assert 'data-builder-view-mode="editor"' in template
    assert 'data-builder-view-mode="preview"' in template
    assert 'data-builder-variables-toggle' in template
    assert 'data-builder-size-mode="editor-wide"' in template
    assert 'data-builder-size-mode="preview-wide"' in template
    assert 'data-builder-view="variables"' not in template
    assert 'data-builder-toggle-variables' not in template


def test_agreement_builder_buttons_use_shared_classes_and_toolbar_groups():
    css = ADMIN_CSS.read_text(encoding="utf-8")
    script = AGREEMENT_BUILDER_JS.read_text(encoding="utf-8")
    template = AGREEMENT_BUILDER_TEMPLATE.read_text(encoding="utf-8")

    assert ".builder-button {" in css
    assert ".builder-button--primary" in css
    assert ".builder-button--success" in css
    assert ".builder-button--danger" in css
    assert ".builder-button--icon" in css
    assert '.builder-button[aria-pressed="true"]' in css
    assert "agreement-builder__toolset" in template
    assert not re.search(r"<button\b(?![^>]*\bclass=)[^>]*>", template)
    assert not re.search(r"<button\b(?![^>]*\bclass=)[^>]*>", script)
