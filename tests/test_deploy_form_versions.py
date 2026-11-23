from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.deployment import deploy_form_versions as deployment


def _definition(
    allowed_signatures=("mszafir", "profil_zaufany"),
) -> dict:
    return {
        "title": "Formularz testowy",
        "fields": [],
        "workflow": {
            "initial_step": "submission",
            "steps": [{"id": "submission"}],
        },
        "documents": [
            {
                "id": "paper",
                "label": "Dokument",
                "allowed_signatures": list(allowed_signatures),
            }
        ],
    }


def _published(
    allowed_signatures=("mszafir", "profil_zaufany"),
):
    return SimpleNamespace(
        id=10,
        version_label="1.0",
        definition_json=_definition(allowed_signatures),
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDb:
    def __init__(self, form):
        self.form = form
        self.commits = 0

    def execute(self, _statement):
        return _ScalarResult(self.form)

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeFormVersionService:
    def __init__(
        self,
        published,
        *,
        draft=None,
        upgrade_result=0,
    ):
        self.published = published
        self.draft = draft
        self.upgrade_result = upgrade_result

        self.clone_calls = 0
        self.update_calls = 0
        self.publish_calls = 0
        self.upgrade_calls = 0

    def resolve_published(self, _db, _form_id):
        return self.published

    def editable_draft(self, _db, _form_id):
        return self.draft

    def clone_to_draft(
        self,
        _db,
        _form,
        source_version,
        *,
        actor_id,
        bump="minor",
    ):
        assert actor_id is None
        assert bump == "minor"

        self.clone_calls += 1

        self.draft = SimpleNamespace(
            id=11,
            version_label="1.1",
            definition_json=deepcopy(
                source_version.definition_json
            ),
        )
        return self.draft

    def update_definition(
        self,
        version,
        definition,
        *,
        change_summary=None,
    ):
        self.update_calls += 1
        version.definition_json = deepcopy(definition)

        assert change_summary

    def publish(
        self,
        _db,
        _form,
        version,
        *,
        actor_id,
        change_summary="",
    ):
        assert actor_id is None
        assert change_summary

        self.publish_calls += 1
        self.published = version
        self.draft = None

        return version

    def upgrade_active_submissions(
        self,
        _db,
        _form,
        version,
        *,
        actor_id,
    ):
        assert actor_id is None
        assert version is self.published

        self.upgrade_calls += 1
        return self.upgrade_result


def _wire_main(monkeypatch, service):
    form = SimpleNamespace(
        id=1,
        slug="sample_form_2",
    )
    db = _FakeDb(form)

    monkeypatch.setattr(
        deployment,
        "create_session_factory",
        lambda _database_url: lambda: db,
    )
    monkeypatch.setattr(
        deployment,
        "FormVersionService",
        lambda: service,
    )
    monkeypatch.delenv(
        "FORM_VERSION_MIGRATION_SLUGS",
        raising=False,
    )

    return db


def test_main_publishes_qualified_policy_when_missing(
    monkeypatch,
):
    service = _FakeFormVersionService(_published())
    db = _wire_main(monkeypatch, service)

    assert deployment.main() == 0

    assert service.clone_calls == 1
    assert service.update_calls == 1
    assert service.publish_calls == 1
    assert service.upgrade_calls == 0
    assert db.commits == 1

    allowed = service.published.definition_json[
        "documents"
    ][0]["allowed_signatures"]

    assert set(allowed) == {
        "mszafir",
        "profil_zaufany",
        "qualified",
    }


def test_main_is_idempotent(
    monkeypatch,
):
    service = _FakeFormVersionService(_published())
    db = _wire_main(monkeypatch, service)

    assert deployment.main() == 0
    assert deployment.main() == 0

    assert service.clone_calls == 1
    assert service.publish_calls == 1
    assert service.upgrade_calls == 1
    assert db.commits == 2


def test_main_accepts_existing_draft_with_only_qualified_added(
    monkeypatch,
):
    published = _published()

    draft_definition = deepcopy(
        published.definition_json
    )
    draft_definition["documents"][0][
        "allowed_signatures"
    ].append("qualified")

    draft = SimpleNamespace(
        id=11,
        version_label="1.1",
        definition_json=draft_definition,
    )

    service = _FakeFormVersionService(
        published,
        draft=draft,
    )
    db = _wire_main(monkeypatch, service)

    assert deployment.main() == 0

    assert service.clone_calls == 0
    assert service.update_calls == 1
    assert service.publish_calls == 1
    assert service.upgrade_calls == 0
    assert db.commits == 1


def test_main_rejects_existing_draft_with_unrelated_change(
    monkeypatch,
):
    published = _published()

    draft_definition = deepcopy(
        published.definition_json
    )
    draft_definition["title"] = "Inna treść"
    draft_definition["documents"][0][
        "allowed_signatures"
    ].append("qualified")

    draft = SimpleNamespace(
        id=11,
        version_label="1.1",
        definition_json=draft_definition,
    )

    service = _FakeFormVersionService(
        published,
        draft=draft,
    )
    db = _wire_main(monkeypatch, service)

    with pytest.raises(
        RuntimeError,
        match="istniejący draft",
    ):
        deployment.main()

    assert service.publish_calls == 0
    assert db.commits == 0


def test_main_rejects_existing_draft_with_other_signature_added(
    monkeypatch,
):
    published = _published()

    draft_definition = deepcopy(
        published.definition_json
    )
    draft_definition["documents"][0][
        "allowed_signatures"
    ].extend(
        [
            "qualified",
            "qualified_other",
        ]
    )

    draft = SimpleNamespace(
        id=11,
        version_label="1.1",
        definition_json=draft_definition,
    )

    service = _FakeFormVersionService(
        published,
        draft=draft,
    )
    db = _wire_main(monkeypatch, service)

    with pytest.raises(
        RuntimeError,
        match="istniejący draft",
    ):
        deployment.main()

    assert service.publish_calls == 0
    assert db.commits == 0


def test_main_upgrades_active_submissions_when_policy_is_current(
    monkeypatch,
):
    service = _FakeFormVersionService(
        _published(
            (
                "mszafir",
                "profil_zaufany",
                "qualified",
            )
        ),
        upgrade_result=3,
    )
    db = _wire_main(monkeypatch, service)

    assert deployment.main() == 0

    assert service.clone_calls == 0
    assert service.update_calls == 0
    assert service.publish_calls == 0
    assert service.upgrade_calls == 1
    assert db.commits == 1


def test_add_qualified_does_not_create_missing_policy():
    definition = {
        "documents": [
            {
                "id": "without-policy",
            }
        ]
    }

    updated, changed = (
        deployment._add_qualified_signature(
            definition
        )
    )

    assert changed is False
    assert (
        "allowed_signatures"
        not in updated["documents"][0]
    )