from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from database import normalize_database_url


MANIFEST_VERSION = 1


class BackupError(RuntimeError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def alembic_head() -> str:
    script = ScriptDirectory.from_config(AlembicConfig("alembic.ini"))
    heads = script.get_heads()
    if len(heads) != 1:
        raise BackupError(f"Expected exactly one Alembic head, found {len(heads)}.")
    return heads[0]


def current_database_revision(database_url: str) -> str | None:
    engine = create_engine(normalize_database_url(database_url), future=True)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    finally:
        engine.dispose()


def git_commit_sha() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def database_kind(database_url: str) -> str:
    backend = make_url(normalize_database_url(database_url)).get_backend_name()
    if backend == "postgresql":
        return "postgresql"
    if backend in {"mysql", "mariadb"}:
        return "mariadb"
    raise BackupError(f"Unsupported backup database backend: {backend}.")


def storage_inventory(root: Path) -> list[dict[str, object]]:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise BackupError(f"Storage snapshot source is not a directory: {resolved}")
    inventory = []
    for path in sorted(resolved.rglob("*")):
        if path.is_symlink():
            raise BackupError(f"Storage snapshot cannot contain symlinks: {path}")
        if path.is_file():
            inventory.append(
                {
                    "path": path.relative_to(resolved).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return inventory


def backup_storage_tree(source: Path, archive_path: Path) -> list[dict[str, object]]:
    inventory = storage_inventory(source)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in inventory:
            archive.write(source / str(item["path"]), arcname=str(item["path"]))
    return inventory


def restore_storage_tree(archive_path: Path, destination: Path, expected: list[dict[str, object]]) -> None:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise BackupError("Storage restore destination must be empty.")
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            if stat.S_ISLNK(member.external_attr >> 16):
                raise BackupError("Storage archive cannot contain symlinks.")
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise BackupError("Storage archive contains an unsafe path.")
            target = (destination / Path(*relative.parts)).resolve()
            if destination not in target.parents and target != destination:
                raise BackupError("Storage archive escapes restore destination.")
        archive.extractall(destination)
    actual = storage_inventory(destination)
    if actual != expected:
        raise BackupError("Restored storage inventory/checksums do not match the manifest.")


def _mariadb_defaults_file(url) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False, encoding="utf-8")
    try:
        handle.write("[client]\n")
        handle.write(f"host={url.host or 'localhost'}\n")
        handle.write(f"port={url.port or 3306}\n")
        handle.write(f"user={url.username or ''}\n")
        handle.write(f"password={url.password or ''}\n")
    finally:
        handle.close()
    path = Path(handle.name)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def dump_database(database_url: str, output_path: Path) -> str:
    normalized = normalize_database_url(database_url)
    url = make_url(normalized)
    kind = database_kind(normalized)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "postgresql":
        env = os.environ.copy()
        if url.password:
            env["PGPASSWORD"] = url.password
        command = [
            "pg_dump", "--format=custom", "--no-owner", "--no-privileges",
            "--host", url.host or "localhost", "--port", str(url.port or 5432),
            "--username", url.username or "", "--dbname", url.database or "",
            "--file", str(output_path),
        ]
        subprocess.run(command, check=True, env=env)
        return kind

    defaults_path = _mariadb_defaults_file(url)
    try:
        command = [
            "mariadb-dump", f"--defaults-extra-file={defaults_path}", "--single-transaction",
            "--routines", "--events", "--triggers", "--default-character-set=utf8mb4",
            url.database or "",
        ]
        with output_path.open("wb") as output:
            subprocess.run(command, check=True, stdout=output)
    finally:
        defaults_path.unlink(missing_ok=True)
    return kind


def assert_isolated_restore_target(database_url: str) -> None:
    url = make_url(normalize_database_url(database_url))
    host = str(url.host or "").lower()
    database = str(url.database or "").lower()
    if os.getenv("P3_ALLOW_ISOLATED_RESTORE", "").lower() not in {"1", "true", "yes"}:
        raise BackupError("Set P3_ALLOW_ISOLATED_RESTORE=true for an explicit isolated restore test.")
    if host not in {"localhost", "127.0.0.1", "::1", "postgres", "mariadb"}:
        raise BackupError("Restore is allowed only on an isolated local/CI database host.")
    if not any(marker in database for marker in ("test", "restore", "_dr")):
        raise BackupError("Restore database name must contain test, restore, or _dr.")


def restore_database(database_url: str, dump_path: Path, manifest: dict) -> None:
    assert_isolated_restore_target(database_url)
    if manifest.get("manifest_version") != MANIFEST_VERSION or manifest.get("status") != "complete":
        raise BackupError("Backup manifest is incomplete or unsupported.")
    expected_dump = manifest.get("database", {}).get("sha256")
    if not expected_dump or not hmac_compare(sha256_file(dump_path), str(expected_dump)):
        raise BackupError("Database backup SHA-256 verification failed.")
    normalized = normalize_database_url(database_url)
    url = make_url(normalized)
    kind = database_kind(normalized)
    if manifest.get("database", {}).get("kind") != kind:
        raise BackupError("Database dump and restore target use different engines.")
    engine = create_engine(normalized, future=True)
    try:
        if inspect(engine).get_table_names():
            raise BackupError("Restore database must be empty.")
    finally:
        engine.dispose()
    if kind == "postgresql":
        env = os.environ.copy()
        if url.password:
            env["PGPASSWORD"] = url.password
        command = [
            "pg_restore", "--exit-on-error", "--no-owner", "--no-privileges",
            "--host", url.host or "localhost", "--port", str(url.port or 5432),
            "--username", url.username or "", "--dbname", url.database or "", str(dump_path),
        ]
        subprocess.run(command, check=True, env=env)
        return
    defaults_path = _mariadb_defaults_file(url)
    try:
        command = ["mariadb", f"--defaults-extra-file={defaults_path}", url.database or ""]
        with dump_path.open("rb") as source:
            subprocess.run(command, check=True, stdin=source)
    finally:
        defaults_path.unlink(missing_ok=True)


def create_backup(
    database_url: str,
    output_dir: Path,
    *,
    storage_source: Path | None = None,
) -> Path:
    timestamp = utc_timestamp()
    kind = database_kind(database_url)
    suffix = ".dump" if kind == "postgresql" else ".sql"
    dump_path = output_dir / f"database-{kind}-{timestamp}{suffix}"
    dump_database(database_url, dump_path)
    storage_path = output_dir / f"storage-{timestamp}.zip" if storage_source else None
    inventory = backup_storage_tree(storage_source, storage_path) if storage_source and storage_path else []
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "status": "complete",
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "application": {"commit_sha": git_commit_sha(), "alembic_head": alembic_head()},
        "database": {
            "kind": kind,
            "alembic_revision": current_database_revision(database_url),
            "filename": dump_path.name,
            "sha256": sha256_file(dump_path),
            "size": dump_path.stat().st_size,
        },
        "storage": {
            "mode": "controlled_archive" if storage_path else "infrastructure_snapshot_required",
            "filename": storage_path.name if storage_path else None,
            "sha256": sha256_file(storage_path) if storage_path else None,
            "inventory": inventory,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / f"manifest-{timestamp}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def verify_manifest(manifest: dict, *, dump_path: Path, storage_path: Path | None = None) -> None:
    if manifest.get("manifest_version") != MANIFEST_VERSION or manifest.get("status") != "complete":
        raise BackupError("Backup manifest is incomplete or unsupported.")
    expected_dump = manifest.get("database", {}).get("sha256")
    if not expected_dump or not hmac_compare(sha256_file(dump_path), str(expected_dump)):
        raise BackupError("Database backup SHA-256 verification failed.")
    expected_storage = manifest.get("storage", {}).get("sha256")
    if expected_storage:
        if storage_path is None or not hmac_compare(sha256_file(storage_path), str(expected_storage)):
            raise BackupError("Storage backup SHA-256 verification failed.")


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verified P3 database/storage backup and isolated restore helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    backup.add_argument("--output-dir", type=Path, required=True)
    backup.add_argument("--storage-source", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    restore = subparsers.add_parser("restore")
    restore.add_argument("--manifest", type=Path, required=True)
    restore.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    restore.add_argument("--storage-destination", type=Path)
    args = parser.parse_args()

    if args.command == "backup":
        print(create_backup(args.database_url, args.output_dir, storage_source=args.storage_source))
        return 0
    manifest = _load_manifest(args.manifest)
    dump_path = args.manifest.parent / str(manifest["database"]["filename"])
    storage_filename = manifest.get("storage", {}).get("filename")
    storage_path = args.manifest.parent / storage_filename if storage_filename else None
    verify_manifest(manifest, dump_path=dump_path, storage_path=storage_path)
    if args.command == "verify":
        print("Backup checksums verified.")
        return 0
    restore_database(args.database_url, dump_path, manifest)
    if storage_path and args.storage_destination:
        restore_storage_tree(storage_path, args.storage_destination, manifest["storage"]["inventory"])
    print("Isolated restore completed; run migrations, integrity checks, /ready and smoke tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
