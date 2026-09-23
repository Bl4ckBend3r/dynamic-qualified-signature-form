from __future__ import annotations

import json
import os
from pathlib import Path

from werkzeug.security import generate_password_hash

from models import Form, FormVersion, User


def _screenshot_dir(tmp_path):
    directory = Path(os.environ.get("THEME_SCREENSHOT_DIR", tmp_path))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _new_page(browser, *, color_scheme="light", storage_value=None, storage_error=False):
    context = browser.new_context(color_scheme=color_scheme)
    page = context.new_page()
    if storage_error:
        page.add_init_script(
            """
            for (const method of ['getItem', 'setItem']) {
                Object.defineProperty(Storage.prototype, method, {
                    configurable: true,
                    value() { throw new DOMException('Storage disabled', 'SecurityError'); }
                });
            }
            """
        )
    elif storage_value:
        page.add_init_script(
            f"localStorage.setItem('ui-theme', {json.dumps(storage_value)});"
        )
    return context, page


def test_theme_follows_system_and_manual_choice_persists(chromium_browser, live_server, tmp_path):
    context, page = _new_page(chromium_browser, color_scheme="light")
    try:
        page.set_viewport_size({"width": 1366, "height": 900})
        page.goto(live_server)
        assert page.locator("html").get_attribute("data-theme") == "light"
        assert page.locator("body").evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == "rgb(255, 255, 255)"
        assert page.locator(".site-header").evaluate(
            "element => getComputedStyle(element).color"
        ) == "rgb(255, 255, 255)"
        toggle = page.locator("[data-theme-toggle]")
        assert toggle.get_attribute("aria-label") == "Włącz ciemny motyw"
        assert toggle.bounding_box()["width"] >= 44
        assert toggle.bounding_box()["height"] >= 44
        page.screenshot(path=str(_screenshot_dir(tmp_path) / "theme-public-light-1366.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(_screenshot_dir(tmp_path) / "theme-public-light-390.png"), full_page=True)
        page.set_viewport_size({"width": 1366, "height": 900})

        toggle.click()
        assert page.locator("html").get_attribute("data-theme") == "dark"
        assert toggle.get_attribute("aria-label") == "Włącz jasny motyw"
        assert page.evaluate("localStorage.getItem('ui-theme')") == "dark"
        page.reload()
        assert page.locator("html").get_attribute("data-theme") == "dark"
    finally:
        context.close()


def test_theme_follows_dark_system_without_saved_choice(chromium_browser, live_server):
    context, page = _new_page(chromium_browser, color_scheme="dark")
    try:
        page.goto(live_server)
        assert page.locator("html").get_attribute("data-theme") == "dark"
        header_accent, button_accent = page.locator("html").evaluate(
            """element => {
                const styles = getComputedStyle(element);
                return [
                    styles.getPropertyValue('--color-header-accent').trim(),
                    styles.getPropertyValue('--color-primary').trim(),
                ];
            }"""
        )
        assert header_accent == button_accent
    finally:
        context.close()


def test_saved_light_overrides_dark_system(chromium_browser, live_server):
    context, page = _new_page(
        chromium_browser, color_scheme="dark", storage_value="light"
    )
    try:
        page.goto(live_server)
        assert page.locator("html").get_attribute("data-theme") == "light"
    finally:
        context.close()


def test_storage_security_error_uses_safe_system_fallback(chromium_browser, live_server):
    context, page = _new_page(
        chromium_browser, color_scheme="dark", storage_error=True
    )
    try:
        page.goto(live_server)
        assert page.locator("html").get_attribute("data-theme") == "dark"
        page.locator("[data-theme-toggle]").click()
        assert page.locator("html").get_attribute("data-theme") == "light"
    finally:
        context.close()


def test_theme_is_available_in_admin_and_has_no_page_overflow(chromium_browser, live_server, tmp_path):
    context, page = _new_page(chromium_browser, color_scheme="dark")
    screenshot_dir = _screenshot_dir(tmp_path)
    try:
        for path in ("/", "/admin/"):
            for width in (1366, 390):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(f"{live_server}{path}")
                assert page.locator("html").get_attribute("data-theme") == "dark"
                assert page.locator("[data-theme-toggle]").count() == 1
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(
                    path=str(screenshot_dir / f"theme-{'admin' if path != '/' else 'public'}-dark-{width}.png"),
                    full_page=True,
                )
        page.locator("[data-theme-toggle]").click()
        assert page.locator("html").get_attribute("data-theme") == "light"
        page.wait_for_timeout(250)
        assert page.locator("body").evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == "rgb(255, 255, 255)"
        assert page.locator(".admin-header").evaluate(
            "element => getComputedStyle(element).color"
        ) == "rgb(255, 255, 255)"
        for width in (1366, 390):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(
                path=str(screenshot_dir / f"theme-admin-light-{width}.png"),
                full_page=True,
            )
        print(f"Theme screenshots: {screenshot_dir}")
    finally:
        context.close()


def test_form_builder_uses_dark_theme_for_all_workspaces(
    chromium_browser, live_server, e2e_environment, tmp_path
):
    with e2e_environment.session_factory() as db:
        form = db.query(Form).filter(Form.slug == "participant-e2e").one()
        form_id = form.id
        user = User(
            email="theme-admin@example.invalid",
            password_hash=generate_password_hash("theme-test-secret"),
            role="super_admin",
            is_active=True,
            is_blocked=False,
        )
        db.add(user)
        db.flush()
        published = db.query(FormVersion).filter_by(
            form_id=form_id, status="published"
        ).one()
        e2e_environment.app.extensions["services"].form_version_service.clone_to_draft(
            db, form, published, actor_id=user.id
        )
        db.commit()

    context, page = _new_page(
        chromium_browser, color_scheme="dark", storage_value="dark"
    )
    screenshot_dir = _screenshot_dir(tmp_path)
    try:
        page.goto(f"{live_server}/admin/")
        page.locator('[name="email"]').fill("theme-admin@example.invalid")
        page.locator('[name="password"]').fill("theme-test-secret")
        page.locator('button[type="submit"]').click()
        response = page.goto(f"{live_server}/admin/forms/{form_id}/fields")
        assert response.status == 200
        field = page.locator(".form-builder__field").first
        field.click()
        assert field.evaluate("element => element.classList.contains('is-selected')")

        for width in (1366, 390):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            for selector in (
                ".admin-form-tabs",
                ".form-builder__palette",
                ".form-builder__canvas",
                ".form-builder__properties",
                ".form-builder__field.is-selected",
            ):
                locator = page.locator(selector)
                assert locator.is_visible()
                assert locator.evaluate(
                    "element => getComputedStyle(element).backgroundColor !== 'rgb(255, 255, 255)'"
                )
            page.screenshot(
                path=str(screenshot_dir / f"theme-form-builder-dark-{width}.png"),
                full_page=True,
            )
        print(f"Form builder theme screenshots: {screenshot_dir}")
    finally:
        context.close()
