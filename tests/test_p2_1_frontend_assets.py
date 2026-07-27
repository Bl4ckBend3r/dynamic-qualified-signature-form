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
    stylesheet = Path("static/css/documents_to_sign.css")
    script = Path("static/js/documents_to_sign.js")

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
    assert "<script src=\"{{ url_for('static', filename='js/documents_to_sign.js') }}\" defer></script>" in template


def test_documents_to_sign_frontend_uses_backend_status_flags():
    script = Path("static/js/documents_to_sign.js").read_text(encoding="utf-8")

    assert "Boolean(data.is_rejected)" in script
    assert "data.agreement_stage_completed" in script
    assert "data.declaration_stage_completed" in script
    assert "data.is_final" in script
    assert "rejectedStatuses" not in script
    assert "finalStatuses" not in script


def test_documents_to_sign_frontend_receives_acceptance_status_url_template():
    template = Path("templates/documents_to_sign.html").read_text(encoding="utf-8")
    script = Path("static/js/documents_to_sign.js").read_text(encoding="utf-8")

    assert "data-acceptance-status-url-template" in template
    assert "url_for('api.api_acceptance_status'" in template
    assert "buildAcceptanceStatusUrl" in script


def test_documents_to_sign_frontend_builds_api_urls_with_base_path():
    node = shutil.which("node")
    if not node:
        return

    script = Path("static/js/documents_to_sign.js").read_text(encoding="utf-8")
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


def test_user_instruction_window_is_safe_and_remembers_minimize_and_close():
    node = shutil.which("node")
    if not node:
        return

    script = Path("static/js/documents_to_sign.js").read_text(encoding="utf-8")
    runner = f"""
const vm = require("vm");
function element() {{
  const classes = new Set(["is-hidden"]);
  const attributes = {{}};
  const children = [];
  return {{
    value: "",
    dataset: {{}},
    textContent: "",
    innerHTML: "untouched",
    children,
    attributes,
    className: "",
    classList: {{
      add(name) {{ classes.add(name); }},
      remove(name) {{ classes.delete(name); }},
      contains(name) {{ return classes.has(name); }},
    }},
    setAttribute(name, value) {{ attributes[name] = value; }},
    appendChild(child) {{ children.push(child); return child; }},
    replaceChildren() {{ children.splice(0, children.length); }},
    addEventListener() {{}},
  }};
}}
const instructionWindow = element();
const formInstructionSection = element();
const formInstructionContent = element();
const instructionSteps = element();
const nextActionSection = element();
const nextActionContent = element();
const instructionRestore = element();
const elements = {{
  "submission_id": {{ value: "", dataset: {{}}, addEventListener() {{}} }},
  "user-instruction-window": instructionWindow,
  "form-instruction-section": formInstructionSection,
  "form-instruction-content": formInstructionContent,
  "instruction-steps": instructionSteps,
  "next-action-section": nextActionSection,
  "next-action-content": nextActionContent,
  "user-instruction-minimize": element(),
  "user-instruction-close": element(),
  "user-instruction-restore": instructionRestore,
}};
const storage = new Map();
global.window = {{
  APP_BASE_PATH: "/aplikacja",
  location: {{ pathname: "/aplikacja/do-podpisania" }},
  setTimeout() {{}},
  sessionStorage: {{
    getItem(key) {{ return storage.has(key) ? storage.get(key) : null; }},
    setItem(key, value) {{ storage.set(key, value); }},
    removeItem(key) {{ storage.delete(key); }},
  }},
}};
global.document = {{
  getElementById(id) {{ return elements[id] || null; }},
  querySelectorAll() {{ return []; }},
  createElement() {{ return element(); }},
}};
vm.runInThisContext({json.dumps(script)});

showUserInstruction({{ has_form_instruction: false, form_instruction: "", next_action: "" }}, "abc");
const hiddenWithoutContent = instructionWindow.classList.contains("is-hidden");
showUserInstruction({{ has_form_instruction: false, form_instruction: "", next_action: "Poczekaj", process_status: "FORM_SUBMITTED", instruction_version: "empty-v1", instruction_steps: [] }}, "empty");
const shownWithNextActionOnly = !instructionWindow.classList.contains("is-hidden");
showUserInstruction({{
  has_form_instruction: true,
  form_instruction: "<img src=x onerror=alert(1)>\\nKrok 2",
  next_action: "Podpisz dokument",
  process_status: "OFFICER_ACCEPTED",
  instruction_version: "v1",
  instruction_steps: [
    {{ key: "submitted", label: "Wysłanie formularza", completed: true, current: false }},
    {{ key: "declaration", label: "Deklaracja", completed: false, current: true }},
  ],
}}, "abc");
const shown = !instructionWindow.classList.contains("is-hidden");
const safeTextBeforeChange = formInstructionContent.textContent;
const currentStep = instructionSteps.children.find((item) => item.attributes["aria-current"] === "step");
const currentStepIsSemanticAndStyled = Boolean(currentStep && currentStep.classList.contains("instruction-step--current"));
minimizeInstruction();
const minimized = instructionWindow.classList.contains("is-hidden") && !instructionRestore.classList.contains("is-hidden");
restoreInstruction();
const restored = !instructionWindow.classList.contains("is-hidden") && instructionRestore.classList.contains("is-hidden");
closeInstruction();
showUserInstruction({{ has_form_instruction: true, form_instruction: "same", next_action: "same", process_status: "OFFICER_ACCEPTED", instruction_version: "v1", instruction_steps: [] }}, "abc");
const stayedClosed = instructionWindow.classList.contains("is-hidden") && instructionRestore.classList.contains("is-hidden");
showUserInstruction({{ has_form_instruction: true, form_instruction: "changed", next_action: "changed", process_status: "DECLARATION_WAITING_FOR_SIGNATURE", instruction_version: "v2", instruction_steps: [] }}, "abc");
const reopenedAfterStatusChange = !instructionWindow.classList.contains("is-hidden");
console.log(JSON.stringify({{
  hiddenWithoutContent,
  shownWithNextActionOnly,
  shown,
  currentStepIsSemanticAndStyled,
  minimized,
  restored,
  stayedClosed,
  reopenedAfterStatusChange,
  safeTextBeforeChange,
  innerHtmlUntouched: formInstructionContent.innerHTML,
  closeKey: storage.get("instruction_closed_abc_OFFICER_ACCEPTED"),
}}));
"""
    completed = subprocess.run(
        [node, "-e", runner],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result == {
        "hiddenWithoutContent": True,
        "shownWithNextActionOnly": True,
        "shown": True,
        "currentStepIsSemanticAndStyled": True,
        "minimized": True,
        "restored": True,
        "stayedClosed": True,
        "reopenedAfterStatusChange": True,
        "safeTextBeforeChange": "<img src=x onerror=alert(1)>\nKrok 2",
        "innerHtmlUntouched": "untouched",
        "closeKey": "v1",
    }

    stylesheet = Path("static/css/documents_to_sign.css").read_text(encoding="utf-8")
    assert ".instruction-step--current" in stylesheet
    assert "font-weight: 800" in stylesheet


def test_training_selection_keeps_full_width_layout():
    template = Path("templates/declaration_form.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/style.css").read_text(encoding="utf-8")

    assert "field.width or 'full'" in template
    assert "training-selection-row" in template
    assert ".training-selection-row" in stylesheet
    assert "grid-column: 1 / -1;" in stylesheet
    assert ".training-selection .checkbox-item span" in stylesheet
