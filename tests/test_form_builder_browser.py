import json
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_form_builder_add_edit_resize_drag_preview_delete_and_serialize():
    html = """<!doctype html><html><head><style>
      [data-form-canvas] {{ display:grid; grid-template-columns:repeat(12, 1fr); width:800px; gap:8px; }}
      .form-builder__field {{ min-height:80px; grid-column:span var(--field-span, 12); }}
    </style></head><body>
      <form data-form-builder>
        <input data-builder-state>
        <button type=button data-builder-mode=edit>Edytor</button>
        <button type=button data-builder-mode=preview>Podgląd</button>
        <div data-field-palette><button type=button draggable=true data-add-field=email>+ E-mail</button></div>
        <span data-field-count></span><div data-form-canvas></div><div data-builder-empty></div>
        <aside data-properties-panel>
          <div data-properties-empty></div><div data-properties-content>
            <button type=button data-close-properties>×</button>
            <input data-property=label><input data-property=name><small data-name-help></small>
            <select data-property=type></select><div data-width-options></div>
            <label data-placeholder-setting><input data-property=placeholder></label>
            <input type=checkbox data-property=required>
            <label data-options-setting><textarea data-property=options></textarea></label>
            <input data-property=section><input data-property=document_label>
            <div data-availability-settings></div>
            <label><input type=checkbox data-document-usage=declaration></label>
            <div data-repeatable-group-settings></div>
            <button type=button data-duplicate-field>Duplikuj</button><button type=button data-delete-field>Usuń</button>
          </div>
        </aside>
        <p data-builder-status></p>
      </form>
      <script type=application/json data-builder-initial-state>{initial_state}</script>
      <script type=application/json data-builder-widths>{widths}</script>
      <script type=application/json data-builder-types>{types}</script>
      <script type=module src="/static/js/form_builder/form-builder.js"></script>
    </body></html>""".format(
        initial_state=json.dumps([{"id": 1, "name": "imie", "label": "Imię", "type": "text", "required": True, "width": "full", "width_span": 12, "placeholder": "", "section": "", "document_label": "", "options": []}]),
        widths=json.dumps({"quarter": 3, "third": 4, "half": 6, "two-thirds": 8, "three-quarters": 9, "full": 12}),
        types=json.dumps(["text", "textarea", "email", "tel", "number", "date", "select", "radio", "checkbox", "pesel", "file"]),
    )

    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except playwright_api.Error as exception:
            pytest.skip(f"Brak przeglądarki Playwright: {exception}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda exception: errors.append(str(exception)))

        def serve(route):
            path = route.request.url.split("builder.test", 1)[-1].split("?", 1)[0]
            if path == "/":
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
                return
            asset = PROJECT_ROOT / path.lstrip("/").replace("/", "\\")
            if asset.is_file() and asset.suffix == ".js":
                route.fulfill(status=200, content_type="text/javascript; charset=utf-8", body=asset.read_text(encoding="utf-8"))
                return
            route.fulfill(status=404, body="not found")

        page.route("**/*", serve)
        page.goto("http://builder.test/")
        page.wait_for_function("document.querySelectorAll('[data-field-key]').length === 1")

        page.locator("[data-add-field=email]").click()
        assert page.locator("[data-field-key]").count() == 2
        page.locator('[data-property="label"]').fill("Adres e-mail")
        page.get_by_role("button", name="50%").click()
        selected = page.locator("[data-field-key].is-selected")
        assert selected.evaluate("element => element.style.getPropertyValue('--field-span')") == "6"

        first = page.locator("[data-field-key]").nth(0)
        second = page.locator("[data-field-key]").nth(1)
        first_key = first.get_attribute("data-field-key")
        first.drag_to(second, target_position={"x": 20, "y": 75})
        assert page.locator("[data-field-key]").nth(0).get_attribute("data-field-key") != first_key

        page.locator("[data-builder-mode=preview]").click()
        assert page.locator("[data-form-builder]").evaluate("element => element.classList.contains('is-preview')")
        page.locator("[data-builder-mode=edit]").click()
        page.locator("[data-delete-field]").click()
        assert page.locator("[data-field-key]").count() == 1

        state = json.loads(page.locator("[data-builder-state]").input_value())
        assert len(state) == 1
        assert state[0]["name"] == "email"
        assert state[0]["label"] == "Adres e-mail"
        assert state[0]["width"] == "half"
        assert not errors
        browser.close()
