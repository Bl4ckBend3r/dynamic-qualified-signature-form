from __future__ import annotations

from services.status_catalog import (
    LEGACY_STATUS_MAP as _LEGACY_STATUS_MAP,
    ProcessStatusCode,
    normalize_status as _normalize_status,
)


DRAFT = ProcessStatusCode.DRAFT.value
SUBMITTED = ProcessStatusCode.SUBMITTED.value
WAITING_FOR_REVIEW = ProcessStatusCode.WAITING_FOR_REVIEW.value
REVIEW_ACCEPTED = ProcessStatusCode.REVIEW_ACCEPTED.value
REVIEW_REJECTED = ProcessStatusCode.REVIEW_REJECTED.value
WAITING_FOR_CORRECTION = ProcessStatusCode.WAITING_FOR_CORRECTION.value
CORRECTED = ProcessStatusCode.CORRECTED.value
WAITING_FOR_DOCUMENT = ProcessStatusCode.WAITING_FOR_DOCUMENT.value
WAITING_FOR_SIGNATURE = ProcessStatusCode.WAITING_FOR_SIGNATURE.value
SIGNATURE_INVALID = ProcessStatusCode.SIGNATURE_INVALID.value
COMPLETED = ProcessStatusCode.COMPLETED.value
CANCELLED = ProcessStatusCode.CANCELLED.value

LEGACY_STATUS_MAP = {legacy: status.value for legacy, status in _LEGACY_STATUS_MAP.items()}
TARGET_STATUSES = {status.value for status in ProcessStatusCode}


def normalize_status(status: str | None) -> str:
    return _normalize_status(status).value
