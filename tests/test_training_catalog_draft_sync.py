from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask

from routes.admin.training_management import _save_training_catalog_with_draft
from services.training_catalog_service import TrainingCatalogService


def definition(catalog, title):
    return {
        "title": title,
        "documents": [{
            "id": "declaration",
            "fields": [{
                "type": "training_selection",
                "name": "selected_trainings",
                "enabled": True,
                "catalog": deepcopy(catalog),
            }],
        }],
    }


def test_deleted_training_does_not_return_from_draft():
    old = [
        {"id": "removed", "name": "Usuniete"},
        {"id": "keep", "name": "Pozostaje"},
    ]
    new = [{"id": "keep", "name": "Pozostaje"}]

    form = SimpleNamespace(
        id=1, definition_json=definition(new, "Form")
    )
    draft = SimpleNamespace(
        id=2, definition_json=definition(old, "Draft")
    )

    version_service = Mock()
    version_service.editable_draft.return_value = draft
    version_service.has_versions.return_value = True
    version_service.update_definition.side_effect = (
        lambda version, config: setattr(
            version, "definition_json", deepcopy(config)
        )
    )

    db = Mock()
    db.execute.return_value.scalar_one.return_value = draft

    app = Flask(__name__)
    app.extensions["services"] = SimpleNamespace(
        form_version_service=version_service
    )

    with app.app_context():
        _save_training_catalog_with_draft(
            db, form, definition(new, "Form")
        )

    saved = TrainingCatalogService.get_training_field(
        draft.definition_json
    )
    assert [item["id"] for item in saved["catalog"]] == ["keep"]
    assert draft.definition_json["title"] == "Draft"
    assert form.definition_json["title"] == "Form"


def test_catalog_change_requires_draft_for_versioned_form():
    form = SimpleNamespace(id=1, definition_json=definition([], "Form"))
    service = Mock()
    service.editable_draft.return_value = None
    service.has_versions.return_value = True

    app = Flask(__name__)
    app.extensions["services"] = SimpleNamespace(
        form_version_service=service
    )

    with app.app_context():
        with pytest.raises(ValueError, match="wersje robocza"):
            _save_training_catalog_with_draft(
                Mock(), form, definition([], "Form")
            )

    service.update_definition.assert_not_called()
