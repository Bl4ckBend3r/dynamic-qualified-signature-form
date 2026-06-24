from pathlib import Path


RUNTIME_PATHS = [
    Path("app.py"),
    Path("services/container.py"),
    Path("routes/documents.py"),
    *Path("routes/admin").glob("*.py"),
]


def test_documents_route_no_longer_imports_legacy_app_for_training_selection():
    source = Path("routes/documents.py").read_text(encoding="utf-8")
    declaration_flow_source = Path("services/documents/declaration_flow_service.py").read_text(encoding="utf-8")

    assert "import legacy_app" not in source
    assert "services.training_agreement_service" in declaration_flow_source


def test_app_factory_no_longer_installs_legacy_helpers_at_runtime():
    source = Path("app.py").read_text(encoding="utf-8")
    container_source = Path("services/container.py").read_text(encoding="utf-8")

    assert "install_legacy_helpers" not in source
    assert "import legacy_app" not in container_source
    assert "install_legacy_helpers" not in container_source


def test_runtime_modules_do_not_import_legacy_app():
    offenders = []
    for path in RUNTIME_PATHS:
        source = path.read_text(encoding="utf-8")
        if "import legacy_app" in source or "from legacy_app" in source:
            offenders.append(str(path))

    assert offenders == []


def test_create_app_does_not_register_legacy_app_view_functions(app):
    legacy_views = [
        endpoint
        for endpoint, view_func in app.view_functions.items()
        if getattr(view_func, "__module__", "") == "legacy_app"
    ]

    assert legacy_views == []


def test_legacy_app_imports_are_limited_to_compatibility_tests():
    allowed = {
        Path("tests/conftest.py"),
        Path("tests/test_document_split_services.py"),
        Path("tests/test_runtime_legacy_imports.py"),
        Path("tests/test_training_agreements.py"),
        Path("tests/test_training_agreement_service.py"),
    }
    offenders = []
    for path in Path("tests").glob("test_*.py"):
        source = path.read_text(encoding="utf-8")
        if "import legacy_app" in source and path not in allowed:
            offenders.append(str(path))

    assert offenders == []


def test_documents_route_is_thin_http_adapter():
    source = Path("routes/documents.py").read_text(encoding="utf-8")

    assert "Thin HTTP adapter for document routes." in source
    assert "build_status_view" not in source
    assert "ProcessStatus" not in source
    assert "STATUS_MAP" not in source
    assert "declaration_flow_service" in source
    assert "agreement_flow_service" in source
    assert "document_download_service" in source
    assert "document_signing_service" in source
    assert "document_view_service" in source
