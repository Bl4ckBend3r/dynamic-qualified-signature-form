from pathlib import Path
import json
import shutil
import subprocess


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


def test_documents_to_sign_frontend_builds_api_urls_with_base_path():
    node = shutil.which("node")
    if not node:
        return

    script = Path("static/documents_to_sign.js").read_text(encoding="utf-8")
    runner = f"""
const vm = require("vm");
const elements = {{
  "submission_id": {{ value: "", dataset: {{ acceptanceStatusUrlTemplate: "/api/submissions/__SUBMISSION_ID__/acceptance-status" }}, addEventListener() {{}} }},
  "acceptance-status": null,
  "submission-status-tiles": null,
  "documents-section": null,
  "generate-button": null,
  "akceptacja": null,
  "sign-documents-form": null,
  "process-completed-box": null,
}};
global.window = {{
  APP_BASE_PATH: "/aplikacja",
  location: {{ pathname: "/aplikacja/do-podpisania" }},
  setTimeout() {{}},
}};
global.document = {{
  getElementById(id) {{ return elements[id] || null; }},
  querySelectorAll() {{ return []; }},
}};
vm.runInThisContext({json.dumps(script)});
console.log(JSON.stringify({{
  localUrl: (window.APP_BASE_PATH = "", buildApiUrl("/api/submissions/abc/acceptance-status")),
  prefixedUrl: (window.APP_BASE_PATH = "/aplikacja", buildApiUrl("/api/submissions/abc/acceptance-status")),
  prefixedRelativeUrl: buildApiUrl("api/submissions/abc/acceptance-status"),
  alreadyPrefixedUrl: buildApiUrl("/aplikacja/api/submissions/abc/acceptance-status"),
  doubleSlashUrl: buildApiUrl("/api//submissions//abc//acceptance-status"),
  acceptanceStatusUrl: buildAcceptanceStatusUrl("abc 123"),
}}));
"""
    completed = subprocess.run(
        [node, "-e", runner],
        check=True,
        capture_output=True,
        text=True,
    )
    urls = json.loads(completed.stdout)

    assert urls["localUrl"] == "/api/submissions/abc/acceptance-status"
    assert urls["prefixedUrl"] == "/aplikacja/api/submissions/abc/acceptance-status"
    assert urls["prefixedRelativeUrl"] == "/aplikacja/api/submissions/abc/acceptance-status"
    assert urls["alreadyPrefixedUrl"] == "/aplikacja/api/submissions/abc/acceptance-status"
    assert urls["doubleSlashUrl"] == "/aplikacja/api/submissions/abc/acceptance-status"
    assert urls["acceptanceStatusUrl"] == "/aplikacja/api/submissions/abc%20123/acceptance-status"


def test_training_selection_keeps_full_width_layout():
    template = Path("templates/declaration_form.html").read_text(encoding="utf-8")
    stylesheet = Path("static/style.css").read_text(encoding="utf-8")

    assert "field.width or 'full'" in template
    assert "training-selection-row" in template
    assert ".training-selection-row" in stylesheet
    assert "grid-column: 1 / -1;" in stylesheet
    assert ".training-selection .checkbox-item span" in stylesheet
