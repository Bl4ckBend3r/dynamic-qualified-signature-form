from pathlib import Path


def test_legacy_dependencies_report_tracks_runtime_imports():
    report = Path("LEGACY_DEPENDENCIES.md").read_text(encoding="utf-8")

    assert "routes/documents.py" in report
    assert "services/container.py" in report
    assert "legacy_app.py" in report


def test_legacy_app_is_documented_historical_module():
    source = Path("legacy_app.py").read_text(encoding="utf-8")

    assert "Legacy compatibility module." in source
    assert "Runtime create_app() does not import this file" in source
    assert "historical direct execution" in source


def test_legacy_wrapper_dead_code_was_removed():
    source = Path("legacy_app.py").read_text(encoding="utf-8")

    assert "selected_ids =" not in source
    assert "declaration_context =" not in source
    assert "blocking_fields =" not in source
    assert "agreement_bytes =" not in source
