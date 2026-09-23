from __future__ import annotations

import hashlib
import hmac
import secrets


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