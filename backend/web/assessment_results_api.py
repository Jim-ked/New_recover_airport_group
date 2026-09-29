from __future__ import annotations

from typing import Any

from backend.auth.principal import Principal
from backend.storage.airport_repository import AirportRepository
from backend.storage.assessment_repository import AssessmentRepository
from backend.web.error_mapping import map_expected_error
from backend.web.http import ApiInputError, ApiResponse, parse_positive_int


class AssessmentResultsApi:
    """Read-only adapter for already-persisted assessment artifacts."""

    def __init__(
        self,
        *,
        assessment_repository: AssessmentRepository,
        airport_repository: AirportRepository,
    ) -> None:
        self.assessment = assessment_repository
        self.airports = airport_repository

    @staticmethod
    def _handle(call) -> ApiResponse:
        try:
            return call()
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            raise

    def overview(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            results, total = self.assessment.list_results(limit=500)
            risks = self.assessment.list_risks()
            airport_names = {
                airport.airport_id: airport.airport_name
                for airport in self.airports.list_airports()
            }
            cells: dict[str, dict[str, dict[str, Any]]] = {}
            scenarios: set[str] = set()
            for result in results:
                airport_cells = cells.setdefault(result.airport_id, {})
                if result.scenario_type in airport_cells:
                    continue
                scenarios.add(result.scenario_type)
                airport_cells[result.scenario_type] = {
                    "result_id": result.id,
                    "support_index": result.initial_capacity,
                    "decline_rate": result.decline_rate,
                    "recovery_slope": result.recovery_slope,
                    "task_threshold": result.task_threshold,
                    "steps": len(result.curves),
                    "created_at": result.created_at,
                }
            return ApiResponse(
                {
                    "schema_version": "assessment-visual.v1",
                    "scenario_types": sorted(scenarios),
                    "airport_ids": sorted(cells),
                    "airport_names": airport_names,
                    "cells": cells,
                    "risks": [
                        {
                            "airport_id": risk.airport_id,
                            "risk_level": risk.risk_level,
                            "risk_score": risk.risk_score,
                            "updated_at": risk.updated_at,
                        }
                        for risk in risks
                    ],
                    "total_results": total,
                },
                200,
            )

        return self._handle(action)

    def curves(self, *, principal: Principal, airport_id: Any) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            if not isinstance(airport_id, str) or not airport_id.strip():
                raise ApiInputError("airport_id must be a nonblank string", field="airport_id")
            results, total = self.assessment.list_results(airport_id=airport_id, limit=100)
            return ApiResponse(
                {
                    "schema_version": "assessment-visual.v1",
                    "airport_id": airport_id,
                    "series": [
                        {
                            "result_id": result.id,
                            "scenario_type": result.scenario_type,
                            "created_at": result.created_at,
                            "steps": [step for step, _ in result.curves],
                            "support": [support for _, support in result.curves],
                            "decline_rate": result.decline_rate,
                            "recovery_slope": result.recovery_slope,
                            "task_threshold": result.task_threshold,
                            "initial_capacity": result.initial_capacity,
                        }
                        for result in results
                    ],
                    "total": total,
                },
                200,
            )

        return self._handle(action)

    def comparisons(self, *, principal: Principal, limit: Any = None) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            parsed_limit = parse_positive_int(limit, field="limit", default=50, maximum=500)
            records, total = self.assessment.list_comparisons(limit=parsed_limit)
            return ApiResponse(
                {
                    "schema_version": "assessment-visual.v1",
                    "items": [
                        {
                            "id": record.id,
                            "base_airport_id": record.base_airport_id,
                            "base_scenario_type": record.base_scenario_type,
                            "compared_airport_id": record.compared_airport_id,
                            "compared_scenario_type": record.compared_scenario_type,
                            "conclusion": record.conclusion,
                            "log": record.log,
                            "created_at": record.created_at,
                        }
                        for record in records
                    ],
                    "total": total,
                },
                200,
            )

        return self._handle(action)


__all__ = ["AssessmentResultsApi"]
