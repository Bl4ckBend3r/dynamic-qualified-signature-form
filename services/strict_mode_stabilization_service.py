from __future__ import annotations

from typing import Iterable

from services.legacy_fallback_readiness_service import AREAS, LegacyFallbackReadinessService
from services.legacy_fallback_report_service import LegacyFallbackReportService


STRICT_FLAG_BY_AREA = {
    "documents": "STRICT_DOCUMENT_METADATA_READ",
    "workflow": "STRICT_WORKFLOW_HISTORY_READ",
    "decisions": "STRICT_DECISION_AUDIT_READ",
}


class StrictModeStabilizationService:
    def __init__(
        self,
        *,
        report_service: LegacyFallbackReportService | None = None,
        readiness_service: LegacyFallbackReadinessService | None = None,
        strict_flags: dict[str, bool] | None = None,
    ) -> None:
        self.report_service = report_service or LegacyFallbackReportService()
        self.readiness_service = readiness_service or LegacyFallbackReadinessService(self.report_service)
        self.strict_flags = strict_flags or {}

    def build_stabilization_report(
        self,
        db,
        *,
        area: str = "all",
        limit: int | None = None,
        submission_id: str | None = None,
    ) -> dict:
        selected_areas = self._selected_areas(area)
        fallback_report = self.report_service.build_fallback_report(db, limit=limit, submission_id=submission_id)
        readiness = self.readiness_service.evaluate_report(fallback_report, selected_areas)
        fallback_summary = self.report_service.summarize_fallback_usage(fallback_report)
        schema_mismatch_areas = {
            error.get("area")
            for error in fallback_report["errors"]
            if error.get("category") == "schema_mismatch"
        }
        areas = {}
        for area_name in selected_areas:
            readiness_details = readiness["areas"][area_name]
            fallbacks_detected = int(fallback_summary.get(area_name, 0))
            strict_enabled = self._strict_enabled(area_name)
            requires_schema_upgrade = bool(fallback_report.get("schema_mismatch")) and (
                area_name in schema_mismatch_areas or "submission" in schema_mismatch_areas
            )
            strict_events_detected = (
                int(readiness_details.get("blocking_fallback_records", 0)) if strict_enabled else 0
            )
            migration_candidate = (
                strict_enabled
                and readiness_details["ready"]
                and fallbacks_detected == 0
                and strict_events_detected == 0
                and not requires_schema_upgrade
            )
            areas[area_name] = {
                "strict_enabled": strict_enabled,
                "readiness_ready": readiness_details["ready"],
                "fallbacks_detected": fallbacks_detected,
                "strict_events_detected": strict_events_detected,
                "migration_candidate": migration_candidate,
                "requires_backfill": not readiness_details["ready"] or fallbacks_detected > 0,
                "requires_schema_upgrade": requires_schema_upgrade,
                "blocking_fallback_records": readiness_details["blocking_fallback_records"],
                "recommended_action": self._recommended_action(
                    strict_enabled=strict_enabled,
                    readiness_ready=readiness_details["ready"],
                    migration_candidate=migration_candidate,
                    requires_schema_upgrade=requires_schema_upgrade,
                ),
            }
        return {
            "ready_for_legacy_removal": (
                not fallback_report.get("schema_mismatch")
                and all(details["migration_candidate"] for details in areas.values())
            ),
            "requires_schema_upgrade": bool(fallback_report.get("schema_mismatch")),
            "processed_submissions": fallback_report["processed_submissions"],
            "areas": areas,
            "errors": [
                error for error in fallback_report["errors"] if error.get("area") in {*selected_areas, "submission"}
            ],
        }

    def _selected_areas(self, area: str) -> tuple[str, ...]:
        normalized = str(area or "all").strip().lower()
        if normalized == "all":
            return AREAS
        if normalized not in AREAS:
            raise ValueError(f"Nieznany obszar stabilizacji strict mode: {area}")
        return (normalized,)

    def _strict_enabled(self, area: str) -> bool:
        return bool(self.strict_flags.get(STRICT_FLAG_BY_AREA[area], False))

    def _recommended_action(
        self,
        *,
        strict_enabled: bool,
        readiness_ready: bool,
        migration_candidate: bool,
        requires_schema_upgrade: bool = False,
    ) -> str:
        if requires_schema_upgrade:
            return "keep_fallback"
        if migration_candidate:
            return "ready_for_legacy_removal"
        if strict_enabled:
            return "stabilize"
        if readiness_ready:
            return "enable_strict"
        return "keep_fallback"
