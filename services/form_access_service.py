from __future__ import annotations
from pathlib import Path

import hashlib
import hmac
import secrets
import os
from cryptography.fernet import Fernet, InvalidToken

SHARE_TOKEN_BYTES = 32


def generate_share_token() -> tuple[str, str]:
    """
    Generuje tajny token formularza.

    Zwraca:
        (token_jawny, hash_do_bazy)
    """
    token = secrets.token_urlsafe(SHARE_TOKEN_BYTES)

    return token, hash_share_token(token)


def hash_share_token(token: str) -> str:
    return hashlib.sha256(
        str(token or "").encode("utf-8")
    ).hexdigest()


def verify_share_token(
    stored_hash: str,
    token: str,
) -> bool:
    stored_hash = str(stored_hash or "").strip()
    token = str(token or "").strip()

    if not stored_hash or not token:
        return False

    candidate_hash = hash_share_token(token)

    return hmac.compare_digest(
        stored_hash,
        candidate_hash,
    )

def _share_cipher() -> Fernet:
    key = ""
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.is_file():
        for entry in env_file.read_text(encoding="utf-8-sig").splitlines():
            entry = entry.strip().removeprefix("export ").strip()
            if entry.startswith("FORM_SHARE_TOKEN_ENCRYPTION_KEY="):
                key = entry.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not key:
        key = os.environ.get("FORM_SHARE_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("Brak FORM_SHARE_TOKEN_ENCRYPTION_KEY")
    return Fernet(key.encode("ascii"))


def encrypt_share_token(token: str) -> str:
    return _share_cipher().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_share_token(value: str | None) -> str | None:
    if not value:
        return None

    try:
        return _share_cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Nie można odszyfrować tokenu") from exc