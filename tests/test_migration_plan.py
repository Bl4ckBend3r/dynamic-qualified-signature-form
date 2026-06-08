from pathlib import Path


def test_migration_plan_contains_required_sections():
    plan = Path("MIGRATION_PLAN.md").read_text(encoding="utf-8")

    required_sections = [
        "Pola do zostawienia",
        "Pola do przeniesienia do `data_json`",
        "Pola do osobnych tabel",
        "Pola legacy do zachowania tymczasowo",
        "Proponowane etapy migracji",
        "Ryzyka",
    ]

    for section in required_sections:
        assert section in plan

    for legacy_field in ["acceptance_required", "decision_email_sent", "akceptacja", "signature_request_id"]:
        assert legacy_field in plan
