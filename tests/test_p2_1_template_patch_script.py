from pathlib import Path

from scripts.apply_p2_1_template_patch import patch_template


def test_p2_1_template_patch_is_idempotent(tmp_path, monkeypatch):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    template = template_dir / "documents_to_sign.html"
    template.write_text(
        "{% block title %}Do podpisania{% endblock %}\n"
        "{% block content %}\n"
        "<style>body { color: red; }</style>\n"
        "<script>console.log('legacy');</script>\n"
        "{% endblock %}\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    assert patch_template() is True
    patched_once = template.read_text(encoding="utf-8")
    assert "documents_to_sign.css" in patched_once
    assert "documents_to_sign.js" in patched_once
    assert "<style>" not in patched_once
    assert "<script>" not in patched_once

    assert patch_template() is False
    assert template.read_text(encoding="utf-8") == patched_once


def test_p2_1_template_patch_script_exists():
    assert Path("scripts/apply_p2_1_template_patch.py").exists()
