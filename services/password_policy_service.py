from __future__ import annotations

from dataclasses import dataclass


MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 4096
COMMON_PRIVILEGED_PASSWORDS = frozenset(
    {
        "123456789012",
        "adminadminadmin",
        "administrator",
        "haslohaslohaslo",
        "letmeinletmein",
        "password1234",
        "passwordpassword",
        "qwertyqwerty",
        "zaq12wsxzaq1",
    }
)


@dataclass(frozen=True)
class PasswordPolicyResult:
    valid: bool
    errors: tuple[str, ...]


class PasswordPolicyService:
    def validate(self, password: str | None) -> PasswordPolicyResult:
        value = str(password or "")
        errors: list[str] = []
        if len(value) < MIN_PASSWORD_LENGTH:
            errors.append("Hasło musi mieć minimum 12 znaków.")
        if len(value) > MAX_PASSWORD_LENGTH:
            errors.append("Hasło jest zbyt długie.")
        if value.strip().casefold() in COMMON_PRIVILEGED_PASSWORDS:
            errors.append("Hasło jest zbyt popularne.")
        return PasswordPolicyResult(valid=not errors, errors=tuple(errors))

    def require_valid(self, password: str | None) -> None:
        result = self.validate(password)
        if not result.valid:
            raise ValueError(result.errors[0])
