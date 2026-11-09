"""Safely persist the Fernet key used by shared form links during deployment."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values
from sqlalchemy import select

from database import create_session_factory
from models import Form


KEY_NAME = "FORM_SHARE_TOKEN_ENCRYPTION_KEY"
KEY_LINE = re.compile(r"^\s*(?:export\s+)?FORM_SHARE_TOKEN_ENCRYPTION_KEY\s*=")
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@contextmanager
def _exclusive_lock(path: Path):
    lock_path = path.with_name(path.name + ".form-share-key.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        os.chmod(lock_path, 0o600)
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked and os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        elif locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _validate_key(value: str, source: str) -> str:
    try:
        Fernet(value.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(f"Niepoprawny {KEY_NAME} w {source}.") from exc
    return value


def _read_file_key(contents: bytes) -> str | None:
    try:
        lines = contents.decode("utf-8-sig").splitlines()
    except UnicodeError as exc:
        raise RuntimeError("Nie można odczytać .env jako UTF-8.") from exc

    values = []
    for line in lines:
        if KEY_LINE.match(line):
            entry = line.strip().removeprefix("export ").strip()
            if not entry.startswith(KEY_NAME + "="):
                raise RuntimeError(f"Niepoprawny wpis {KEY_NAME} w .env.")
            value = entry.split("=", 1)[1].strip().strip('"').strip("'")
            values.append(_validate_key(value, ".env"))
    if len(set(values)) > 1:
        raise RuntimeError(f"Sprzeczne wpisy {KEY_NAME} w .env.")
    return values[0] if values else None


def _has_encrypted_tokens(database_url: str) -> bool:
    try:
        Session = create_session_factory(database_url)
        with Session() as db:
            return db.execute(
                select(Form.id)
                .where(Form.share_token_encrypted.is_not(None))
                .where(Form.share_token_encrypted != "")
                .limit(1)
            ).first() is not None
    except Exception as exc:
        raise RuntimeError(
            "Nie można sprawdzić zaszyfrowanych tokenów w bazie; "
            "inicjalizacja klucza została wstrzymana."
        ) from exc


def _verify_existing_tokens(database_url: str, key: str) -> None:
    try:
        cipher = Fernet(key.encode("ascii"))
        Session = create_session_factory(database_url)
        with Session() as db:
            tokens = db.execute(
                select(Form.share_token_encrypted)
                .where(Form.share_token_encrypted.is_not(None))
                .where(Form.share_token_encrypted != "")
            ).scalars()
            for encrypted in tokens:
                try:
                    cipher.decrypt(encrypted.encode("ascii"))
                except (InvalidToken, UnicodeError) as exc:
                    raise RuntimeError(
                        "Istniejący klucz nie odszyfrowuje linków formularzy. "
                        "Przywróć oryginalny klucz przed uruchomieniem aplikacji."
                    ) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "Nie można zweryfikować klucza względem zaszyfrowanych tokenów w bazie."
        ) from exc


def _write_key_atomically(path: Path, original: bytes, key: str) -> None:
    separator = b"" if not original or original.endswith((b"\n", b"\r")) else b"\n"
    updated = original + separator + f"{KEY_NAME}={key}\n".encode("ascii")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env-key-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.chmod(temporary_path, 0o600)
            if os.name != "nt" and os.geteuid() == 0:
                owner = path.stat() if path.exists() else path.parent.stat()
                os.chown(temporary_path, owner.st_uid, owner.st_gid)
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)


def ensure_form_share_key(
    env_file: str | Path = DEFAULT_ENV_FILE,
    *,
    database_url: str | None = None,
) -> str:
    """Return a non-secret status; never replace an existing or needed key."""
    path = Path(env_file).resolve()
    if not path.parent.is_dir():
        raise RuntimeError("Katalog trwałego pliku .env nie istnieje.")
    with _exclusive_lock(path):
        contents = path.read_bytes() if path.exists() else b""
        file_key = _read_file_key(contents)
        environment_key = os.environ.get(KEY_NAME)
        if environment_key:
            environment_key = _validate_key(environment_key, "środowisku serwera")
        if file_key and environment_key and file_key != environment_key:
            raise RuntimeError(
                f"Konflikt {KEY_NAME} między .env a środowiskiem serwera. "
                "Przywróć właściwy klucz przed uruchomieniem aplikacji."
            )
        selected_url = database_url or os.environ.get("DATABASE_URL") or dotenv_values(path).get("DATABASE_URL")
        if file_key:
            if selected_url:
                _verify_existing_tokens(selected_url, file_key)
            os.chmod(path, 0o600)
            return "zachowano klucz z .env"

        if environment_key:
            if selected_url:
                _verify_existing_tokens(selected_url, environment_key)
            key = environment_key
            status = "utrwalono klucz ze środowiska serwera"
        else:
            if not selected_url:
                raise RuntimeError(
                    "Brak DATABASE_URL; nie można bezpiecznie sprawdzić "
                    "zaszyfrowanych tokenów przed utworzeniem klucza."
                )
            if _has_encrypted_tokens(selected_url):
                raise RuntimeError(
                    "Baza zawiera zaszyfrowane tokeny, lecz brak klucza. "
                    "Przywróć oryginalny FORM_SHARE_TOKEN_ENCRYPTION_KEY."
                )
            key = Fernet.generate_key().decode("ascii")
            status = "utworzono i utrwalono nowy klucz"

        _write_key_atomically(path, contents, key)
        return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()
    try:
        status = ensure_form_share_key(args.env_file)
    except RuntimeError as exc:
        print(f"Inicjalizacja klucza: {exc}", file=sys.stderr)
        return 1
    print(f"Inicjalizacja klucza: {status}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
