from __future__ import annotations

from typing import Any, Mapping, Protocol


class SignatureVerificationBackend(Protocol):
    """Minimal contract shared by local signature-verification engines."""

    name: str

    def verify(self, pdf_bytes: bytes) -> Mapping[str, Any]: ...

