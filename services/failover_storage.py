from __future__ import annotations

import logging
import time

import requests


logger = logging.getLogger(__name__)


def _is_connectivity_error(exc: BaseException) -> bool:
    current: BaseException | None = exc

    while current is not None:
        if isinstance(
            current,
            (
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ),
        ):
            return True

        current = current.__cause__ or current.__context__

    return False


def _is_not_found_error(exc: BaseException) -> bool:
    current: BaseException | None = exc

    while current is not None:
        if "HTTP 404" in str(current):
            return True

        current = current.__cause__ or current.__context__

    return False


class FailoverStorage:
    """
    Primary storage: Nextcloud.
    Fallback storage: local filesystem.

    Lokalny storage jest aktywowany tylko w przypadku błędu
    połączenia z usługą sieciową.
    """

    _LOCAL_READ_ON_404 = {
        "read_bytes",
        "read_text_or_empty",
        "get_file_bytes",
        "get_pdf_bytes",
        "read_form_json",
    }

    def __init__(
        self,
        primary,
        fallback,
        *,
        retry_seconds: int = 30,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.retry_seconds = max(int(retry_seconds), 1)

        self._primary_unavailable_until = 0.0

    @property
    def primary_available(self) -> bool:
        return time.monotonic() >= self._primary_unavailable_until

    def _mark_primary_unavailable(self, exc: BaseException) -> None:
        self._primary_unavailable_until = (
            time.monotonic() + self.retry_seconds
        )

        logger.warning(
            "storage_failover_activated retry_seconds=%s error=%s",
            self.retry_seconds,
            type(exc).__name__,
        )

    def _mark_primary_available(self) -> None:
        if self._primary_unavailable_until:
            logger.info("storage_failover_primary_restored")

        self._primary_unavailable_until = 0.0

    def __getattr__(self, name: str):
        primary_attribute = getattr(self.primary, name)

        if not callable(primary_attribute):
            return primary_attribute

        fallback_attribute = getattr(self.fallback, name)

        def delegated(*args, **kwargs):
            # Circuit breaker aktywny — bez kolejnego oczekiwania
            # na timeout korzystamy chwilowo z dysku lokalnego.
            if not self.primary_available:
                return fallback_attribute(*args, **kwargs)

            try:
                result = primary_attribute(*args, **kwargs)

            except Exception as exc:
                if _is_connectivity_error(exc):
                    self._mark_primary_unavailable(exc)

                    return fallback_attribute(*args, **kwargs)

                # Plik mógł zostać utworzony lokalnie podczas awarii
                # Nextcloud. Po odzyskaniu sieci nadal musi być dostępny.
                if (
                    name in self._LOCAL_READ_ON_404
                    and _is_not_found_error(exc)
                ):
                    return fallback_attribute(*args, **kwargs)

                raise

            self._mark_primary_available()

            # Plik utworzony podczas awarii może istnieć wyłącznie
            # lokalnie.
            if name == "exists" and result is False:
                return fallback_attribute(*args, **kwargs)

            return result

        return delegated