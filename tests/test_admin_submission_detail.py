import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from models import Form
from services.admin_workflow_view_service import build_admin_workflow_view
from test_admin_panel import admin_app, create_user, login
from test_composite_document_workflow import composite_env


def test_timeline_uses_observed_path_and_supports_loops_and_arbitrary_steps():
    steps = [{"id": f"node-{i}", "admin_label": f"Etap {i}", "user_label": f"Stan {i}"} for i in range(9)]
    steps[3].update(stage_type="decision", decision_scope="submission")
    submission = SimpleNamespace(process_status="WAITING_FOR_OFFICER_DECISION", workflow_stage="node-3")
    events = [
        {"previous_step": "node-0", "new_step": "node-3", "created_at": "2026-09-07T10:00:00Z"},
        {"previous_step": "node-3", "new_step": "node-5", "created_at": "2026-09-07T11:00:00Z"},
        {"previous_step": "node-5", "new_step": "node-3", "created_at": "2026-09-07T12:00:00Z"},
    ]
    view = build_admin_workflow_view(submission, form_config={"workflow": {"flow_mode": "explicit", "steps": steps}}, events=events)
    sections = view["sections"]
    assert [s["key"] for s in sections] == [s["id"] for s in steps]
    assert [s["key"] for s in sections if s["is_current"]] == ["node-3"]
    assert sections[0]["state"] == sections[5]["state"] == "completed"
    assert sections[1]["state"] == sections[2]["state"] == "future"
    assert sections[3]["state"] == "current"
    assert sections[3]["updated_at"] == events[-1]["created_at"]


@pytest.mark.parametrize("status,extra,expected", [
    ("SIGNATURE_INVALID", {}, "blocked"),
    ("CUSTOM_END", {"final": True}, "completed"),
    ("CUSTOM_REJECTED", {"rejected": True, "final": True}, "blocked"),
])
def test_timeline_semantics_come_from_state_and_flags(status, extra, expected):
    view = build_admin_workflow_view(SimpleNamespace(process_status=status, workflow_stage="custom"),
        form_config={"workflow": {"steps": [{"id": "custom", "admin_label": "Dowolna nazwa", **extra}]}})
    assert view["sections"][0]["state"] == expected
    assert view["sections"][0]["is_current"] is True


def test_submission_detail_timeline_documents_forms_and_responsive_browser(composite_env, tmp_path):
    playwright_api = pytest.importorskip("playwright.sync_api")
    env = composite_env("custom-document", participant_signature=False, upload_required=False, office_signature=True)
    env.enter()
    # A changed editor must never replace the historical graph in this view.
    with env.repo.session_factory() as db:
        db.get(Form, env.form_id).definition_json = {"title": "Changed draft", "fields": [],
            "workflow": {"steps": [{"id": "wrong-draft", "admin_label": "Wrong draft"}]}}
        db.commit()
    create_user(env.app)
    client = env.app.test_client()
    env.app.config["WTF_CSRF_ENABLED"] = True
    login(client)
    path = f"/admin/forms/{env.form_id}/submissions/{env.row()['id']}"
    response = client.get(path)
    assert response.status_code == 200
    assert 'data-workflow-step="wrong-draft"' not in response.text

    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            url = urlsplit(route.request.url)
            result = client.get(url.path + (f"?{url.query}" if url.query else ""))
            route.fulfill(status=result.status_code, content_type=result.content_type, body=result.data)

        page.route("http://localhost/**", serve)
        page.goto("http://localhost" + path)
        steps = page.locator("[data-workflow-step]")
        assert steps.count() == 4
        current = page.locator('[data-workflow-step][aria-current="step"]')
        assert current.count() == 1 and current.get_attribute("data-workflow-step") == "paper"
        assert current.locator("button[data-workflow-toggle]").get_attribute("aria-expanded") == "true"
        assert page.locator('[data-workflow-step="intake"] button').get_attribute("aria-expanded") == "false"
        assert page.locator('[data-workflow-step="review"] button').get_attribute("aria-expanded") == "false"
        toggle = current.locator("button[data-workflow-toggle]")
        toggle.focus()
        page.keyboard.press("Enter")
        assert current.locator(".admin-workflow-panel").is_hidden()
        page.keyboard.press("Space")
        assert current.locator(".admin-workflow-panel").is_visible()
        assert current.get_by_role("link", name="Przejdź do dokumentu").get_attribute("href") == "#submission-documents"
        assert current.locator('input[type="file"]').count() == 0
        assert page.locator('[name="document_pdf"]').count() == 1
        upload = page.locator('[data-office-document-step] form')
        assert upload.get_attribute("action").endswith("/document-step/paper")
        assert upload.get_attribute("enctype") == "multipart/form-data"
        assert upload.locator('[name="csrf_token"]').input_value()
        assert upload.locator('[name="instance"]').count() == 1
        download = page.locator('[data-office-document-step] a').get_attribute("href")
        assert client.get(download).data == env.pdf
        assignment = page.locator("#assignment form").first
        assert assignment.get_attribute("action").endswith("/assignment")
        for name in ("csrf_token", "assigned_to_user_id", "priority", "due_at", "assignment_reason"):
            assert assignment.locator(f'[name="{name}"]').count() == 1
        notes = page.locator(".admin-internal-note-form")
        assert notes.get_attribute("action").endswith("/internal-notes")
        for name in ("csrf_token", "content", "is_important"):
            assert notes.locator(f'[name="{name}"]').count() == 1
        assert notes.locator('[name="content"]').get_attribute("required") is not None
        for width, height in [(1920, 1080), (1440, 900), (1280, 800), (768, 1024), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert page.locator(".admin-submission-detail").evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 14
            main = page.locator(".admin-submission-main").bounding_box()
            sidebar = page.locator(".admin-submission-sidebar").bounding_box()
            if width >= 1200:
                assert sidebar["x"] > main["x"] + main["width"]
            else:
                assert sidebar["y"] > main["y"]
            screenshots = Path(os.environ.get("ADMIN_DETAIL_SCREENSHOT_DIR", str(tmp_path)))
            page.screenshot(path=str(screenshots / f"submission-{width}.png"))
        page.set_viewport_size({"width": 1440, "height": 900})
        assert assignment.locator('[name="due_at"]').bounding_box()["width"] >= 250
        for section in ("submission-workflow", "internal-notes"):
            page.locator(f"#{section}").screenshot(path=str(screenshots / f"{section}.png"))
        page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
        page.wait_for_function("window.scrollY === 0")
        page.locator('[data-admin-action-toggle]').click()
        assert page.locator('#submission-page-actions').is_visible()
        page.keyboard.press("Escape")
        assert page.locator('#submission-page-actions').is_hidden()
        assert not errors
        browser.close()
