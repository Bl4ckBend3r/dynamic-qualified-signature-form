from pathlib import Path


def test_legacy_removal_checklist_exists_and_covers_required_areas():
    text = Path("LEGACY_REMOVAL_CHECKLIST.md").read_text(encoding="utf-8")

    for item in ["Dokumenty", "Workflow", "Decyzje", "Legacy app", "Repo cleanup"]:
        assert item in text
    assert "P4.6 nie usuwa" in text
    assert "strict_document_metadata_missing" in text


def test_legacy_removal_migration_plan_exists_and_lists_legacy_fields():
    text = Path("LEGACY_REMOVAL_MIGRATION_PLAN.md").read_text(encoding="utf-8")

    for field in [
        "pdf_filename",
        "signed_pdf_filename",
        "declaration_*",
        "agreement_*",
        "selected_trainings",
        "training_agreements",
        "signature_status",
        "signature_request_id",
        "acceptance_required",
        "acceptance_email_sent",
        "decision_email_sent",
        "decision_email_sent_for",
        "akceptacja",
        "osw_*",
    ]:
        assert field in text
    assert "rollback" in text.lower()
    assert "nie tworzy migracji Alembic" in text


def test_legacy_app_retirement_plan_exists_and_keeps_runtime_safe():
    text = Path("LEGACY_APP_RETIREMENT_PLAN.md").read_text(encoding="utf-8")

    assert "legacy_app.py" in text
    assert "test-only" in text
    assert "warunki usuniecia" in text.lower()
    assert "warunki pozostawienia" in text.lower()
    assert "create_app()" in text


def test_p4_schema_documentation_describes_required_order():
    text = Path("P4_SCHEMA_CHECK.md").read_text(encoding="utf-8")

    for item in [
        "check_p4_schema.py",
        "alembic upgrade head",
        "backfill_p4_metadata.py",
        "check_legacy_fallback_readiness.py",
        "report_strict_mode_stabilization.py",
        "ready_for_legacy_removal=false",
        "InFailedSqlTransaction",
        "submission_files.original_filename",
    ]:
        assert item in text
