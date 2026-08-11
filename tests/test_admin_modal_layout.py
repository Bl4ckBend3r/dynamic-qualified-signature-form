from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_admin_modal_has_bounded_scrollable_layout_and_fixed_footer():
    css = (PROJECT_ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")

    assert ".admin-modal {" in css
    assert "max-height: 90vh;" in css
    assert "width: min(680px, calc(100vw - 32px));" in css
    assert ".admin-modal__body {" in css
    assert "overflow-y: auto;" in css
    assert ".admin-modal__footer {" in css
    assert "flex: 0 0 auto;" in css
    assert "body.admin-modal-open" in css
    assert "overflow: hidden;" in css


def test_admin_modal_keeps_all_structural_surfaces_opaque():
    css = (PROJECT_ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")

    for selector in (
        ".admin-modal",
        ".admin-modal__surface",
        ".admin-modal__header",
        ".admin-modal__body",
        ".admin-modal__footer",
    ):
        rule = css.split(f"{selector} {{", 1)[1].split("}", 1)[0]
        assert "background: #fff;" in rule


def test_agreement_preview_modal_keeps_a4_page_and_actions_inside_viewport():
    css = (PROJECT_ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")

    assert ".agreement-preview-dialog__close {" in css
    assert "width: min(1100px, calc(100vw - 40px));" in css
    assert "max-height: 92dvh;" in css
    assert ".agreement-preview-dialog__body {" in css
    assert "overflow: auto;" in css
    assert ".document-preview__page {" in css
    assert "width: 210mm;" in css
    assert "min-height: 297mm;" in css
    assert ".document-preview-error" in css


def test_every_admin_dialog_uses_shared_accessible_structure():
    templates_root = PROJECT_ROOT / "templates" / "admin"
    dialog_count = 0

    for template_path in templates_root.rglob("*.html"):
        source = template_path.read_text(encoding="utf-8")
        for opening_tag in re.findall(r"<dialog\b[^>]*>", source):
            dialog_count += 1
            assert "admin-modal" in opening_tag, template_path
            assert 'role="dialog"' in opening_tag, template_path
            assert 'aria-modal="true"' in opening_tag, template_path
            assert "aria-labelledby=" in opening_tag, template_path
            assert "data-admin-modal" in opening_tag, template_path

        assert "window.confirm(" not in source, template_path

    assert dialog_count >= 5


def test_admin_modal_script_locks_body_handles_escape_and_traps_tab_focus():
    base = (PROJECT_ROOT / "templates" / "admin" / "base.html").read_text(encoding="utf-8")

    assert 'classList.toggle("admin-modal-open"' in base
    assert 'if (event.key === "Escape")' in base
    assert 'if (event.key !== "Tab") return;' in base
    assert "document.activeElement === first" in base
    assert "document.activeElement === last" in base
    assert "dialog.returnFocusTarget?.focus()" in base
    assert "if (event.target === dialog) dialog.close();" in base


def test_correction_modal_contains_fields_checks_and_actions_inside_surface():
    source = (
        PROJECT_ROOT / "templates" / "admin" / "submissions" / "detail.html"
    ).read_text(encoding="utf-8")
    modal = source.split('id="return-for-correction-dialog"', 1)[1].split("</dialog>", 1)[0]

    body = modal.split('class="admin-modal__body"', 1)[1].split("</div>", 1)[0]
    assert 'name="reason"' in body
    assert 'name="message_to_user"' in body
    assert 'class="admin-modal__checks"' in body
    assert 'name="clear_submission"' in body
    assert 'name="send_email"' in body
    assert body.index('name="clear_submission"') < body.index('name="send_email"')

    footer = modal.split('class="admin-modal__footer"', 1)[1]
    assert "Anuluj" in footer
    assert "Wyślij do poprawy" in footer


def test_destructive_admin_actions_use_shared_confirmation_dialog():
    submissions = (
        PROJECT_ROOT / "templates" / "admin" / "submissions" / "list.html"
    ).read_text(encoding="utf-8")
    form_edit = (
        PROJECT_ROOT / "templates" / "admin" / "forms" / "edit.html"
    ).read_text(encoding="utf-8")

    assert 'id="bulk-delete-form"' in submissions
    assert "data-admin-confirm-message=" in submissions
    assert "data-admin-confirm-action" in form_edit
    assert "admin:confirmed" in form_edit
