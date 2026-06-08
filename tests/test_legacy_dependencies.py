from pathlib import Path


def test_legacy_dependencies_report_tracks_runtime_imports():
    report = Path("LEGACY_DEPENDENCIES.md").read_text(encoding="utf-8")

    assert "routes/documents.py" in report
    assert "services/container.py" in report
    assert "legacy_app.py" in report
