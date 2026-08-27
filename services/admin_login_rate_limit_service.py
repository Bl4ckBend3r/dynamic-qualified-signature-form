from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from models import AdminLoginAttempt


@dataclass(frozen=True)
class LoginRateLimitPolicy:
    short_attempts: int = 5
    short_window_seconds: int = 60
    long_attempts: int = 30
    long_window_seconds: int = 3600


class AdminLoginRateLimitService:
    """Database-backed login limiter shared by all application workers."""

    def __init__(self, secret_key: str, policy: LoginRateLimitPolicy) -> None:
        self._secret_key = str(secret_key or "").encode("utf-8")
        self.policy = policy

    def key_hash(self, client_address: str, email: str) -> str:
        value = "\0".join(
            (
                str(client_address or "unknown").strip(),
                str(email or "").strip().casefold(),
            )
        ).encode("utf-8")
        return hmac.new(self._secret_key, value, hashlib.sha256).hexdigest()

    def is_limited(
        self,
        db,
        *,
        client_address: str,
        email: str,
        now: datetime | None = None,
    ) -> bool:
        checked_at = now or datetime.now(timezone.utc)
        key_hash = self.key_hash(client_address, email)
        return self._count(db, key_hash, checked_at, self.policy.short_window_seconds) >= self.policy.short_attempts or self._count(
            db,
            key_hash,
            checked_at,
            self.policy.long_window_seconds,
        ) >= self.policy.long_attempts

    def record_failure(
        self,
        db,
        *,
        client_address: str,
        email: str,
        now: datetime | None = None,
    ) -> bool:
        checked_at = now or datetime.now(timezone.utc)
        key_hash = self.key_hash(client_address, email)
        db.add(AdminLoginAttempt(key_hash=key_hash, attempted_at=checked_at))
        db.flush()
        cutoff = checked_at - timedelta(seconds=self.policy.long_window_seconds)
        db.execute(delete(AdminLoginAttempt).where(AdminLoginAttempt.attempted_at < cutoff))
        return self.is_limited(
            db,
            client_address=client_address,
            email=email,
            now=checked_at,
        )

    def clear_failures(self, db, *, client_address: str, email: str) -> None:
        db.execute(
            delete(AdminLoginAttempt).where(
                AdminLoginAttempt.key_hash == self.key_hash(client_address, email)
            )
        )

    @staticmethod
    def _count(db, key_hash: str, now: datetime, window_seconds: int) -> int:
        cutoff = now - timedelta(seconds=window_seconds)
        return int(
            db.execute(
                select(func.count(AdminLoginAttempt.id)).where(
                    AdminLoginAttempt.key_hash == key_hash,
                    AdminLoginAttempt.attempted_at >= cutoff,
                )
            ).scalar_one()
            or 0
        )
