import json
from pathlib import Path

import pytest

from scripts.maintenance.backup_restore import (
    BackupError,
    assert_isolated_restore_target,
    backup_storage_tree,
    restore_storage_tree,
    sha256_file,
    storage_inventory,
    verify_manifest,
)


def test_manifest_checksum_verification_fails_for_corrupted_dump(tmp_path):
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"verified backup")
    manifest = {
        "manifest_version": 1,
        "status": "complete",
        "database": {"sha256": sha256_file(dump)},
        "storage": {"sha256": None},
    }
    verify_manifest(manifest, dump_path=dump)
    dump.write_bytes(b"corrupted backup")
    with pytest.raises(BackupError, match="SHA-256"):
        verify_manifest(manifest, dump_path=dump)


def test_controlled_storage_backup_restore_preserves_three_document_kinds(tmp_path):
    source = tmp_path / "source"
    expected_content = {
        "generated/declaration.pdf": b"generated-pdf",
        "signed/agreement.pdf": b"signed-pdf",
        "attachments/participant.txt": b"synthetic-attachment",
    }
    for relative, content in expected_content.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    archive = tmp_path / "storage.zip"
    expected = backup_storage_tree(source, archive)
    destination = tmp_path / "restored"

    restore_storage_tree(archive, destination, expected)

    assert storage_inventory(destination) == expected
    assert {item["path"] for item in expected} == set(expected_content)


def test_restore_refuses_non_isolated_target(monkeypatch):
    monkeypatch.setenv("P3_ALLOW_ISOLATED_RESTORE", "true")
    with pytest.raises(BackupError, match="isolated"):
        assert_isolated_restore_target("postgresql://user:secret@db.example.org/production")


def test_restore_requires_explicit_confirmation(monkeypatch):
    monkeypatch.delenv("P3_ALLOW_ISOLATED_RESTORE", raising=False)
    with pytest.raises(BackupError, match="P3_ALLOW_ISOLATED_RESTORE"):
        assert_isolated_restore_target("postgresql://user:secret@localhost/app_restore_test")


def test_manifest_example_contains_no_database_credentials(tmp_path):
    dump = tmp_path / "db.sql"
    dump.write_bytes(b"sql")
    manifest = {
        "manifest_version": 1,
        "status": "complete",
        "database": {"kind": "mariadb", "filename": dump.name, "sha256": sha256_file(dump)},
        "storage": {"mode": "infrastructure_snapshot_required", "sha256": None},
    }
    serialized = json.dumps(manifest)
    assert "password" not in serialized.lower()
    assert "database_url" not in serialized.lower()
