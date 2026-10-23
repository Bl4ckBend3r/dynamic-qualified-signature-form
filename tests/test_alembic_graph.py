from __future__ import annotations

import ast
from collections import defaultdict
import importlib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import mysql, postgresql


ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "migrations" / "versions"


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Migration {path.name} does not define {name}.")


def _revision_files():
    return sorted(path for path in VERSIONS.glob("*.py") if path.name != "__init__.py")


def _script_directory() -> ScriptDirectory:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def test_alembic_revision_ids_are_unique():
    by_revision = defaultdict(list)
    for path in _revision_files():
        by_revision[_literal_assignment(path, "revision")].append(path.name)
    duplicates = {revision: files for revision, files in by_revision.items() if len(files) > 1}
    if duplicates:
        lines = ["Duplicate Alembic revision detected:"]
        for revision, files in sorted(duplicates.items()):
            lines.append(str(revision))
            lines.extend(f"- {name}" for name in files)
        raise AssertionError("\n".join(lines))


def test_alembic_has_exactly_one_head_and_loads_full_graph():
    script = _script_directory()
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly one Alembic head, found: {heads}"
    revisions = list(script.walk_revisions())
    assert revisions
    assert len(revisions) == len({item.revision for item in revisions})


def test_alembic_down_revisions_exist_without_self_reference_or_cycle():
    files = _revision_files()
    revisions = {_literal_assignment(path, "revision") for path in files}
    parents: dict[str, tuple[str, ...]] = {}
    for path in files:
        revision = _literal_assignment(path, "revision")
        down = _literal_assignment(path, "down_revision")
        down_items = tuple(item for item in (down if isinstance(down, tuple) else (down,)) if item is not None)
        assert revision not in down_items, f"Alembic revision {revision} references itself."
        missing = sorted(set(down_items) - revisions)
        assert not missing, f"Alembic revision {revision} references missing revisions: {missing}"
        parents[revision] = down_items

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(revision: str):
        if revision in visiting:
            raise AssertionError(f"Alembic revision cycle detected at {revision}.")
        if revision in visited:
            return
        visiting.add(revision)
        for parent in parents[revision]:
            visit(parent)
        visiting.remove(revision)
        visited.add(revision)

    for revision in parents:
        visit(revision)


def test_critical_migration_chain_is_ordered_and_reachable_from_head():
    expected = [
        "20260812_0031",
        "20260813_0032",
        "20260813_0033",
        "20260813_0034",
        "20260813_0035",
        "20260813_0036",
        "20260813_0037",
        "20260818_0038",
        "20260818_0039",
        "20260818_0040",
        "20260818_0041",
        "20260818_0042",
        "20260824_0043",
        "20260824_0044",
        "20260824_0045",
        "20260826_0046",
        "20260831_0047",
        "20260901_0048",
    ]
    script = _script_directory()
    for parent, child in zip(expected, expected[1:]):
        assert script.get_revision(child).down_revision == parent
    head = script.get_current_head()
    reachable = {revision.revision for revision in script.iterate_revisions(head, "base")}
    assert set(expected) <= reachable


def test_p0_json_types_compile_for_mariadb_and_postgresql():
    for module_name in (
        "migrations.versions.20260812_0031_form_versioning",
        "migrations.versions.20260813_0032_compliance_versioning",
        "migrations.versions.20260813_0035_public_form_drafts",
        "migrations.versions.20260813_0036_field_workflow_availability",
        "migrations.versions.20260818_0040_workflow_sla_deadlines",
        "migrations.versions.20260831_0047_training_pre_post_tests",
        "migrations.versions.20260818_0041_role_based_permissions",
    ):
        json_type = importlib.import_module(module_name)._json_type()
        assert "JSON" in json_type.compile(dialect=mysql.dialect()).upper()
        assert "JSONB" in json_type.compile(dialect=postgresql.dialect()).upper()
