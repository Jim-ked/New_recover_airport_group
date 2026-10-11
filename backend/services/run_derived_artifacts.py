from __future__ import annotations

import logging

from backend.services.run_capacity_assessment import RunCapacityAssessmentService
from backend.services.run_indicator_assessment import RunIndicatorAssessmentService
from backend.services.run_service import RunService


class RunDerivedArtifactsService:
    """Independent post-success artifacts; failures never transition canonical Run state."""

    def __init__(self, *, capacity: RunCapacityAssessmentService,
                 indicators: RunIndicatorAssessmentService, run_service: RunService):
        self.capacity = capacity
        self.indicators = indicators
        self.run_service = run_service
        self.logger = logging.getLogger("airport_group.assessment")

    def _event(self, run_id: str, artifact: str, status: str, payload: dict) -> None:
        event = f"run_{artifact}_assessment_{status}"
        self.logger.log(logging.WARNING if status in {"failed", "unavailable"} else logging.INFO,
                        "%s run_id=%s payload=%s", event, run_id, payload)
        try:
            self.run_service.append_worker_event(
                run_id, level="WARNING" if status in {"failed", "unavailable"} else "INFO",
                stage="persistence", event=event, message=event, payload=payload,
            )
        except Exception:
            self.logger.exception("derived artifact event could not be saved event=%s run_id=%s", event, run_id)

    def generate(self, run_id: str) -> None:
        try:
            result = self.capacity.generate_capacity_assessment(run_id)
            status = {"generated": "persisted", "skipped": "skipped"}.get(result["status"], "failed")
            self._event(run_id, "capacity", status, result)
        except Exception as exc:
            self._event(run_id, "capacity", "failed", {"error": str(exc)})
        try:
            result = self.indicators.evaluate(run_id)
            self._event(run_id, "indicator", "persisted" if result.status == "available" else "unavailable", {
                "assessment_status": result.status, "unavailable_reason": result.unavailable_reason,
                "snapshot_hash": result.snapshot_hash, "metrics_hash": result.metrics_hash,
            })
        except Exception as exc:
            self._event(run_id, "indicator", "failed", {"error": str(exc)})
