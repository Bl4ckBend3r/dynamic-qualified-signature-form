from __future__ import annotations

import secrets


class AccessTokenService:
    def generate_token(self) -> str:
        return secrets.token_urlsafe(32)

    def verify_token(self, submission: dict, token: str | None) -> bool:
        expected = str(submission.get("access_token") or "").strip()
        provided = str(token or "").strip()
        if not expected:
            return True
        return secrets.compare_digest(expected, provided)
