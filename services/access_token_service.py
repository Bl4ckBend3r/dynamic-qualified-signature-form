from __future__ import annotations

import secrets


class AccessTokenService:
    def generate_token(self) -> str:
        return secrets.token_urlsafe(32)

    def verify_required_token(self, submission: dict, token: str | None) -> bool:
        expected = str(submission.get("access_token") or "").strip()
        provided = str(token or "").strip()
        return bool(expected and provided and secrets.compare_digest(expected, provided))
