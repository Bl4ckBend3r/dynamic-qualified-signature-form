from pathlib import Path


def test_frontend_split_plan_exists_until_documents_assets_are_extracted():
    plan = Path("FRONTEND_SPLIT_PLAN.md").read_text(encoding="utf-8")
    template = Path("templates/documents_to_sign.html").read_text(encoding="utf-8")

    assert "static/documents_to_sign.css" in plan
    assert "static/documents_to_sign.js" in plan
    assert "<script>" in template
