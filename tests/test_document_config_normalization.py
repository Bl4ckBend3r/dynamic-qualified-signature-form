from services.form_config_service import FormConfigService


def test_normalizes_legacy_process_documents_to_list():
    config = {
        "title": "Test",
        "fields": [],
        "process": {
            "documents": {
                "declaration": {"enabled": True, "template": "Template/deklaracja.html"},
                "agreement": {"enabled": False, "template": "Template/umowa.html"},
            }
        },
    }

    normalized = FormConfigService().normalize_form_config(config)

    assert [document["id"] for document in normalized["documents"]] == ["declaration", "agreement"]
    assert normalized["documents"][0]["kind"] == "generated_pdf"
    assert normalized["workflow"]["initial_step"] == "submission"


def test_keeps_new_documents_list():
    config = {
        "documents": [
            {"id": "attachment_certificate", "kind": "uploaded_file", "required": False}
        ]
    }

    documents = FormConfigService().normalize_documents_config(config)

    assert documents[0]["id"] == "attachment_certificate"
    assert documents[0]["kind"] == "uploaded_file"
