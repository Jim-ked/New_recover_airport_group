from __future__ import annotations

from typing import Any

from backend.auth.principal import Principal
from backend.domain.assessment import AssessmentResult
from backend.services.run_assessment_labels import frozen_run_label
from backend.services.run_comparison_record_service import RunComparisonRecordService
from backend.services.run_result_service import RunResultService
from backend.storage.run_comparison_repository import RunComparisonRepository
from backend.storage.airport_repository import AirportRepository
from backend.storage.assessment_repository import AssessmentRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.web.error_mapping import map_expected_error
from backend.web.http import ApiInputError, ApiResponse, parse_positive_int, parse_nonnegative_int


class AssessmentResultsApi:
    """Read-only adapter for already-persisted assessment artifacts."""

    def __init__(
        self,
        *,
        assessment_repository: AssessmentRepository,
        airport_repository: AirportRepository,
        run_repository: RunRepository,
        snapshot_repository: RunSnapshotRepository,
    ) -> None:
        self.assessment = assessment_repository
        self.airports = airport_repository
        self.runs = run_repository
        self.snapshots = snapshot_repository
        self.comparison_records = RunComparisonRecordService(
            result_service=RunResultService(run_repository=run_repository, snapshot_repository=snapshot_repository),
            repository=RunComparisonRepository(run_repository.db_path),
        )

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
            run_cells: dict[str, dict[str, dict[str, Any]]] = {}
            scenario_labels: dict[str, str] = {}
            scenarios: set[str] = set()
            metric_kinds: set[str] = set()
            for result in results:
                airport_cells = cells.setdefault(result.airport_id, {})
                scenarios.add(result.scenario_type)
                metric_kinds.add(result.metric_kind)
                cell = {
                    "result_id": result.id,
                    "support_index": result.initial_capacity,
                    "metric_kind": result.metric_kind,
                    "matrix_value": result.matrix_value,
                    "source_run_id": result.source_run_id,
                    "snapshot_hash": result.snapshot_hash,
                    "metrics_hash": result.metrics_hash,
                    "effective_windows": result.effective_windows,
                    "unavailable_windows": result.unavailable_windows,
                    "decline_rate": result.decline_rate,
                    "recovery_slope": result.recovery_slope,
                    "task_threshold": result.task_threshold,
                    "steps": len(result.curves),
                    "created_at": result.created_at,
                }
                # Keep the legacy latest-per-label matrix, and add an instance matrix.
                airport_cells.setdefault(result.scenario_type, cell)
                key = result.source_run_id or f"legacy:{result.scenario_type}"
                run_cells.setdefault(result.airport_id, {}).setdefault(key, cell)
                if key not in scenario_labels:
                    scenario_labels[key] = self._scenario_label(result)
            return ApiResponse(
                {
                    "schema_version": "assessment-visual.v1",
                    "scenario_types": sorted(scenarios),
                    "airport_ids": sorted(cells),
                    "airport_names": airport_names,
                    "cells": cells,
                    "scenario_keys": list(scenario_labels),
                    "scenario_labels": scenario_labels,
                    "run_cells": run_cells,
                    "metric_kinds": sorted(metric_kinds),
                    "risks": [
                        {
                            "airport_id": risk.airport_id,
                            "risk_level": risk.risk_level,
                            "risk_score": risk.risk_score,
                            "updated_at": risk.updated_at,
                        }
                        for risk in risks
                    ],
                    "risk_status": (
                        {"status": "available", "reason": None}
                        if risks else {
                            "status": "unavailable" if results else "no_data",
                            "reason": (
                                "未配置独立风险评估模型或正式专家评分"
                                if results else "暂无评估落库结果"
                            ),
                        }
                    ),
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
                            "scenario_key": result.source_run_id or f"legacy:{result.scenario_type}",
                            "scenario_label": self._scenario_label(result),
                            "created_at": result.created_at,
                            "steps": [step for step, _ in result.curves],
                            "support": [support for _, support in result.curves],
                            "values": [support for _, support in result.curves],
                            "metric_kind": result.metric_kind,
                            "metric_label": (
                                "起降容量可用比例"
                                if result.metric_kind == "capacity_availability_v1"
                                else "保障支撑度"
                            ),
                            "source_run_id": result.source_run_id,
                            "snapshot_hash": result.snapshot_hash,
                            "metrics_hash": result.metrics_hash,
                            "effective_windows": result.effective_windows,
                            "unavailable_windows": result.unavailable_windows,
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

    def _scenario_label(self, result: AssessmentResult) -> str:
        if result.source_run_id:
            snapshot = self.snapshots.get(result.source_run_id)
            if snapshot is not None and snapshot.content_hash == result.snapshot_hash:
                return frozen_run_label(snapshot.to_dict())
        return result.scenario_type

    def comparisons(self, *, principal: Principal, limit: Any = None, offset: Any = None) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            parsed_limit = parse_positive_int(limit, field="limit", default=50, maximum=500)
            parsed_offset = parse_nonnegative_int(offset, field="offset", default=0, maximum=1000000)
            return ApiResponse(self.comparison_records.list(principal=principal, limit=parsed_limit, offset=parsed_offset), 200)
        return self._handle(action)

    def evaluations(self, *, principal: Principal, limit: Any = None) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            parsed_limit = parse_positive_int(limit, field="limit", default=100, maximum=500)
            records, total = self.assessment.list_run_evaluations(
                owner_user_id=None if principal.is_admin else principal.user_id,
                limit=parsed_limit,
            )
            items: list[dict[str, Any]] = []
            for evaluation in records:
                item = evaluation.to_dict()
                run = self.runs.get(evaluation.run_id)
                snapshot = self.snapshots.get(evaluation.run_id)
                payload = snapshot.to_dict() if snapshot is not None else {}
                situation = payload.get("situation")
                run_config = payload.get("run_config")
                item["run"] = {
                    "owner_user_id": None if run is None else run.owner_user_id,
                    "situation_id": None if run is None else run.situation_id,
                    "situation_name": (
                        situation.get("name") or situation.get("situation_name")
                        if isinstance(situation, dict)
                        else None
                    ),
                    "damage_scenario_id": (
                        run_config.get("damage_scenario_id")
                        if isinstance(run_config, dict)
                        else None
                    ),
                }
                current = bool(
                    run is not None
                    and run.snapshot_hash == evaluation.snapshot_hash
                    and run.metrics_hash == evaluation.metrics_hash
                )
                item["provenance_status"] = "current" if current else "stale"
                if not current:
                    reason = "评价来源与当前 Run 结果不一致，请重新生成综合评价"
                    item.update({
                        "status": "unavailable",
                        "unavailable_reason": reason,
                        "indicators": [],
                        "support_index": None,
                        "overall_membership": None,
                        "weighted_composite": None,
                        "risk": {
                            "status": "unavailable",
                            "score": None,
                            "level": None,
                            "reason": reason,
                        },
                    })
                items.append(item)
            return ApiResponse(
                {
                    "schema_version": "assessment-evaluation.v1",
                    "items": items,
                    "total": total,
                },
                200,
            )

        return self._handle(action)


__all__ = ["AssessmentResultsApi"]
