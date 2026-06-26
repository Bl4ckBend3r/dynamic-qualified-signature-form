from pathlib import Path

def test_frontend_split_plan_tracks_extracted_documents_assets():
    plan = Path("FRONTEND_SPLIT_PLAN.md").read_text(encoding="utf-8")
    template = Path("templates/documents_to_sign.html").read_text(encoding="utf-8")

    assert "static/documents_to_sign.css" in plan
    assert "static/documents_to_sign.js" in plan
    assert "documents_to_sign.css" in template
    assert "documents_to_sign.js" in template
    assert "<style>" not in template
    assert "<script>" not in template
