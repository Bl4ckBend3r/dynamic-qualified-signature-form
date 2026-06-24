from __future__ import annotations

import logging
from typing import Iterable

from services.legacy_fallback_report_service import LegacyFallbackReportService


AREAS = ("documents", "workflow", "decisions")
logger = logging.getLogger(__name__)


class LegacyFallbackReadinessService:
    def __init__(self, report_service: LegacyFallbackReportService | None = None) -> None:
        self.report_service = report_service or LegacyFallbackReportService()

    def build_readiness_report(
        self,
        db,
        *,
        area: str = "all",
        limit: int | None = None,
        submission_id: str | None = None,
    ) -> dict:
        selected_areas = self._selected_areas(area)
        fallback_report = self.report_service.build_fallback_report(db, limit=limit, submission_id=submission_id)
        readiness = self.evaluate_report(fallback_report, selected_areas)
        readiness["fallback_summary"] = self.report_service.summarize_fallback_usage(fallback_report)
        readiness["processed_submissions"] = fallback_report["processed_submissions"]
        readiness["requires_schema_upgrade"] = bool(fallback_report.get("schema_mismatch"))
        readiness["fallback_records"] = [
            record for record in fallback_report["fallback_records"] if record.get("area") in selected_areas
        ]
        readiness["errors"] = [
            error for error in fallback_report["errors"] if error.get("area") in {*selected_areas, "submission"}
        ]
        for area_name, details in readiness["areas"].items():
            if not details["ready"]:
                logger.warning(
                    "strict_readiness_blocker area=%s blockers=%s reason=readiness_failed",
                    area_name,
                    details["blocking_fallback_records"],
                )
        return readiness

    def evaluate_report(self, fallback_report: dict, areas: Iterable[str] | None = None) -> dict:
        selected_areas = tuple(areas or AREAS)
        details = {
            "documents": self._documents_readiness(fallback_report),
            "workflow": self._workflow_readiness(fallback_report),
            "decisions": self._decisions_readiness(fallback_report),
        }
        selected_details = {area: details[area] for area in selected_areas}
        return {
            "ready": all(detail["ready"] for detail in selected_details.values()),
            "areas": selected_details,
        }

    def build_rollout_plan(
        self,
        db,
        *,
        limit: int | None = None,
        submission_id: str | None = None,
    ) -> dict:
        readiness = self.build_readiness_report(db, area="all", limit=limit, submission_id=submission_id)
        recommendations = {}
        for area, details in readiness["areas"].items():
            recommendations[area] = {
                "ready": details["ready"],
                "recommended_action": (
                    "enable_strict"
                    if details["ready"] and not readiness["requires_schema_upgrade"]
                    else "keep_fallback"
                ),
                "blocking_fallback_records": details["blocking_fallback_records"],
                "requires_schema_upgrade": readiness["requires_schema_upgrade"],
            }
        return {
            "ready": readiness["ready"] and not readiness["requires_schema_upgrade"],
            "recommendations": recommendations,
            "processed_submissions": readiness["processed_submissions"],
            "fallback_summary": readiness["fallback_summary"],
            "requires_schema_upgrade": readiness["requires_schema_upgrade"],
            "errors": readiness["errors"],
        }

    def _selected_areas(self, area: str) -> tuple[str, ...]:
        normalized = str(area or "all").strip().lower()
        if normalized == "all":
            return AREAS
        if normalized not in AREAS:
            raise ValueError(f"Nieznany obszar readiness: {area}")
        return (normalized,)

    def _documents_readiness(self, report: dict) -> dict:
        counters = report["documents"]
        blockers = (
            counters["missing_submission_file"]
            + counters["missing_storage_path"]
            + counters["missing_physical_file"]
            + counters["ambiguous"]
            + counters["errors"]
        )
        return {
            "ready": blockers == 0,
            "blocking_fallback_records": blockers,
            "missing_metadata": counters["missing_submission_file"],
            "missing_storage_path": counters["missing_storage_path"],
            "missing_physical_file": counters["missing_physical_file"],
            "ambiguous": counters["ambiguous"],
            "errors": counters["errors"],
        }

    def _workflow_readiness(self, report: dict) -> dict:
        counters = report["workflow"]
        blockers = counters["missing_events"] + counters["errors"]
        return {
            "ready": blockers == 0,
            "blocking_fallback_records": blockers,
            "missing_events": counters["missing_events"],
            "errors": counters["errors"],
        }

    def _decisions_readiness(self, report: dict) -> dict:
        counters = report["decisions"]
        blockers = counters["using_legacy_fallback"] + counters["ambiguous"] + counters["errors"]
        return {
            "ready": blockers == 0,
            "blocking_fallback_records": blockers,
            "missing_decisions": counters["using_legacy_fallback"],
            "ambiguous": counters["ambiguous"],
            "errors": counters["errors"],
        }
