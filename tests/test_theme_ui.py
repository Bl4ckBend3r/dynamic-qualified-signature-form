from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    light, dark = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


def test_base_templates_initialize_theme_before_stylesheets():
    for template_path in ("templates/base.html", "templates/admin/base.html"):
        template = _read(template_path)
        assert "js/theme.js" in template
        assert template.index("js/theme.js") < template.index("css/style.css")
        assert "defer" not in template[template.index("js/theme.js") - 80 : template.index("js/theme.js") + 80]


def test_public_and_admin_headers_have_accessible_theme_toggle():
    for template_path in ("templates/partials/header.html", "templates/admin/base.html"):
        template = _read(template_path)
        assert "data-theme-toggle" in template
        assert 'aria-label="Włącz ciemny motyw"' in template
        assert "data-theme-icon" in template
        assert "data-theme-label" in template


def test_theme_module_uses_only_requested_storage_key_and_safe_fallback():
    script = _read("static/js/theme.js")
    assert 'const STORAGE_KEY = "ui-theme"' in script
    assert 'new Set(["light", "dark"])' in script
    assert "prefers-color-scheme: dark" in script
    assert "try {" in script
    assert "localStorage.getItem(STORAGE_KEY)" in script
    assert "localStorage.setItem(STORAGE_KEY, theme)" in script
    assert "root.dataset.theme" in script
    assert "Włącz ciemny motyw" in script
    assert "Włącz jasny motyw" in script


def test_theme_tokens_cover_public_admin_and_specialist_styles():
    style = _read("static/css/style.css")
    required_tokens = {
        "--color-bg",
        "--color-bg-subtle",
        "--color-surface",
        "--color-surface-raised",
        "--color-surface-hover",
        "--color-text",
        "--color-text-muted",
        "--color-text-inverse",
        "--color-primary",
        "--color-primary-hover",
        "--color-primary-active",
        "--color-primary-contrast",
        "--color-secondary",
        "--color-border",
        "--color-border-strong",
        "--color-focus",
        "--color-link",
        "--color-link-hover",
        "--color-success",
        "--color-warning",
        "--color-danger",
        "--color-info",
        "--color-input-bg",
        "--color-table-header",
        "--color-overlay",
    }
    assert required_tokens.issubset(set(re.findall(r"(--color-[\w-]+)\s*:", style)))
    assert '[data-theme="dark"]' in style
    assert "min-width: 44px" in style
    assert "min-height: 44px" in style
    for color in ("#3674b5", "#578fca", "#a1e3f9", "#d1f8ef"):
        assert color in style.lower()
    assert "--color-header-bg: #578fca;" in style.lower()
    assert "--color-bg: #ffffff;" in style.lower()
    assert "--color-header-text: #ffffff;" in style.lower()
    assert "--color-header-muted: #ffffff;" in style.lower()
    assert "--color-header-accent: #ffffff;" in style.lower()
    for color in ("#0d1117", "#010409", "#161b22", "#21262d", "#30363d"):
        assert color in style.lower()
    assert "--color-primary: #238636;" in style.lower()
    assert "--color-header-accent: #238636;" in style.lower()

    for stylesheet in (
        "static/css/admin.css",
        "static/css/documents_to_sign.css",
        "static/css/form_builder.css",
        "static/css/public_status.css",
        "static/css/training_selection.css",
    ):
        assert "var(--color-" in _read(stylesheet)

    assert "data-theme" not in _read("static/css/document_template.css")


def test_light_primary_button_text_meets_wcag_aa_contrast():
    assert _contrast_ratio("#3674b5", "#ffffff") >= 4.5
