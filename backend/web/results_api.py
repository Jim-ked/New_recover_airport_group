from __future__ import annotations

from typing import Any

from backend.analysis.comparison import MAX_ANALYSIS_RUNS
from backend.auth.principal import Principal
from backend.services.run_result_service import RunResultService
from backend.web.error_mapping import map_expected_error
from backend.web.http import (
    ApiInputError,
    ApiResponse,
    reject_unknown,
    require_object,
    required_nonblank_string,
    required_nonblank_string_list,
)


class ResultsApi:
    """Thin Results HTTP adapter over canonical successful Run facts."""

    def __init__(self, *, result_service: RunResultService) -> None:
        self.results = result_service

    @staticmethod
    def _handle(call) -> ApiResponse:
        try:
            return call()
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            raise

    def comparable_runs(
        self,
        *,
        principal: Principal,
        base_run_id: Any,
        mode: Any,
    ) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            if not isinstance(base_run_id, str) or not base_run_id.strip():
                raise ApiInputError("base_run_id must be a nonblank string", field="base_run_id")
            if mode not in {"multi_scenario", "configuration", "exploratory"}:
                raise ApiInputError(
                    "mode must be multi_scenario, configuration or exploratory",
                    field="mode",
                )
            return ApiResponse(
                self.results.list_comparable_successful(
                    base_run_id,
                    mode=mode,
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)


    def damage_candidates(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            return ApiResponse(
                self.results.list_damage_comparison_candidates(
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)

    def damage_comparison(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            body = require_object(raw_body)
            reject_unknown(body, {"r0_run_id", "r1_run_id", "r2_run_id"})
            r0 = required_nonblank_string(body, "r0_run_id")
            r1 = required_nonblank_string(body, "r1_run_id")
            r2 = required_nonblank_string(body, "r2_run_id")
            return ApiResponse(
                self.results.compare_r0_r1_r2(
                    r0_run_id=r0,
                    r1_run_id=r1,
                    r2_run_id=r2,
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)

    def scenario_comparison(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            body = require_object(raw_body)
            reject_unknown(body, {"run_ids"})
            run_ids = required_nonblank_string_list(
                body, "run_ids", minimum=2, maximum=6, unique=True
            )
            return ApiResponse(
                self.results.compare_multi_scenario(
                    run_ids=tuple(run_ids),
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)

    def configuration_comparison(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            body = require_object(raw_body)
            reject_unknown(body, {"run_ids", "baseline_run_id"})
            run_ids = required_nonblank_string_list(
                body, "run_ids", minimum=2, maximum=5, unique=True
            )
            baseline_run_id = required_nonblank_string(body, "baseline_run_id")
            if baseline_run_id not in run_ids:
                raise ApiInputError(
                    "baseline_run_id must be one of run_ids", field="baseline_run_id"
                )
            return ApiResponse(
                self.results.compare_configuration(
                    run_ids=tuple(run_ids),
                    baseline_run_id=baseline_run_id,
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)

    def exploratory_comparison(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            body = require_object(raw_body)
            reject_unknown(body, {"run_ids", "baseline_run_id"})
            run_ids = required_nonblank_string_list(
                body, "run_ids", minimum=2, maximum=MAX_ANALYSIS_RUNS, unique=True
            )
            baseline_run_id = required_nonblank_string(body, "baseline_run_id")
            if baseline_run_id not in run_ids:
                raise ApiInputError(
                    "baseline_run_id must be one of run_ids", field="baseline_run_id"
                )
            return ApiResponse(
                self.results.compare_exploratory(
                    run_ids=tuple(run_ids),
                    baseline_run_id=baseline_run_id,
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)

    def object_comparison(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("results.read")
            body = require_object(raw_body)
            reject_unknown(body, {
                "run_ids",
                "comparison_type",
                "baseline_run_id",
                "object_type",
                "object_id",
                "airport_id",
                "resource_type_id",
                "aircraft_type_id",
            })
            run_ids = required_nonblank_string_list(
                body, "run_ids", minimum=2, maximum=MAX_ANALYSIS_RUNS, unique=True
            )
            baseline_run_id = required_nonblank_string(body, "baseline_run_id")
            if baseline_run_id not in run_ids:
                raise ApiInputError(
                    "baseline_run_id must be one of run_ids", field="baseline_run_id"
                )
            comparison_type = required_nonblank_string(body, "comparison_type")
            if comparison_type not in {"damage", "configuration", "exploratory"}:
                raise ApiInputError(
                    "comparison_type must be damage, configuration or exploratory",
                    field="comparison_type",
                )
            object_type = required_nonblank_string(body, "object_type")
            if object_type not in {"task", "airport", "resource", "aircraft", "collaboration"}:
                raise ApiInputError("unsupported object_type", field="object_type")

            optional: dict[str, str | None] = {}
            for field in (
                "object_id", "airport_id", "resource_type_id", "aircraft_type_id"
            ):
                value = body.get(field)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise ApiInputError(f"{field} must be a nonblank string", field=field)
                optional[field] = value

            required_fields = {
                "task": ("object_id",),
                "airport": ("object_id",),
                "resource": ("airport_id", "resource_type_id"),
                "aircraft": ("airport_id", "aircraft_type_id"),
                "collaboration": (),
            }[object_type]
            for field in required_fields:
                if optional[field] is None:
                    raise ApiInputError(f"{field} is required", field=field)

            return ApiResponse(
                self.results.compare_object(
                    run_ids=tuple(run_ids),
                    comparison_type=comparison_type,
                    baseline_run_id=baseline_run_id,
                    object_type=object_type,
                    object_id=optional["object_id"],
                    airport_id=optional["airport_id"],
                    resource_type_id=optional["resource_type_id"],
                    aircraft_type_id=optional["aircraft_type_id"],
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)

    def export_data(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        """Return canonical report-source data without inventing a file format.

        PDF/DOCX/CSV rendering is a delivery concern and remains separately configurable.
        This endpoint guarantees that any renderer consumes exactly the same successful
        Run/comparison facts as the on-screen pages.
        """
        def action() -> ApiResponse:
            principal.require_permission("results.export")
            body = require_object(raw_body)
            kind = required_nonblank_string(body, "kind")
            if kind == "single_run":
                reject_unknown(body, {"kind", "run_id"})
                run_id = required_nonblank_string(body, "run_id")
                data = self.results.get_single_run(
                    run_id, actor_user_id=principal.user_id, is_admin=principal.is_admin
                )
                source_ids = [run_id]
            elif kind == "damage_comparison":
                reject_unknown(body, {"kind", "r0_run_id", "r1_run_id", "r2_run_id"})
                r0 = required_nonblank_string(body, "r0_run_id")
                r1 = required_nonblank_string(body, "r1_run_id")
                r2 = required_nonblank_string(body, "r2_run_id")
                data = self.results.compare_r0_r1_r2(
                    r0_run_id=r0, r1_run_id=r1, r2_run_id=r2,
                    actor_user_id=principal.user_id, is_admin=principal.is_admin,
                )
                source_ids = [r0, r1, r2]
            elif kind == "scenario_comparison":
                reject_unknown(body, {"kind", "run_ids"})
                run_ids = required_nonblank_string_list(body, "run_ids", minimum=2, maximum=6, unique=True)
                data = self.results.compare_multi_scenario(
                    run_ids=tuple(run_ids), actor_user_id=principal.user_id, is_admin=principal.is_admin
                )
                source_ids = run_ids
            elif kind == "configuration_comparison":
                reject_unknown(body, {"kind", "run_ids", "baseline_run_id"})
                run_ids = required_nonblank_string_list(body, "run_ids", minimum=2, maximum=5, unique=True)
                baseline = required_nonblank_string(body, "baseline_run_id")
                if baseline not in run_ids:
                    raise ApiInputError("baseline_run_id must be one of run_ids", field="baseline_run_id")
                data = self.results.compare_configuration(
                    run_ids=tuple(run_ids), baseline_run_id=baseline,
                    actor_user_id=principal.user_id, is_admin=principal.is_admin,
                )
                source_ids = run_ids
            else:
                raise ApiInputError(
                    "kind must be single_run, damage_comparison, scenario_comparison or configuration_comparison",
                    field="kind",
                )
            return ApiResponse({
                "schema_version": "report-data.v1",
                "kind": kind,
                "source_run_ids": list(source_ids),
                "data": data,
                "rendering": {
                    "status": "source_ready",
                    "supported_formats": ["pdf", "csv"],
                    "message": "PDF report and tidy CSV are rendered from this canonical source",
                },
            }, 200)
        return self._handle(action)



__all__ = ["ResultsApi"]
