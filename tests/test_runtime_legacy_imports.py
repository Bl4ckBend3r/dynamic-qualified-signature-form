from pathlib import Path


def test_documents_route_no_longer_imports_legacy_app_for_training_selection():
    source = Path("routes/documents.py").read_text(encoding="utf-8")

    assert "import legacy_app" not in source
    assert "services.training_agreement_service" in source
