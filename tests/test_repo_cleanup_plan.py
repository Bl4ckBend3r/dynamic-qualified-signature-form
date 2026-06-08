from pathlib import Path


def test_repo_cleanup_plan_documents_known_local_artifacts():
    plan = Path("REPO_CLEANUP_PLAN.md").read_text(encoding="utf-8")

    assert ".coverage" in plan
    assert ".pytest_cache" in plan
    assert "output/" in plan
