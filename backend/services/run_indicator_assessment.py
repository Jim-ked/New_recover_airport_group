from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from backend.algorithm.assessment.ahp_weights import AHPError, compute_risk
from backend.algorithm.assessment.fuzzy_comprehensive import FuzzyInputError, evaluate_elements
from backend.domain.assessment import IndicatorAssessmentValue, RunIndicatorAssessment
from backend.storage.assessment_repository import AssessmentRepository
from backend.storage.indicator_repository import IndicatorRepository
from backend.storage.migrations.v024_indicator_assessment import FORMAL_SET_ID
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository


CALCULATION_VERSION = "run-indicator-assessment.v1"


class RunIndicatorAssessmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class IndicatorFact:
    code: str
    raw_value: float
    normalized_value: float
    evidence: Mapping[str, Any]


def _ratio(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunIndicatorAssessmentError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise RunIndicatorAssessmentError(f"{field} must be finite and in [0,1]")
    return number


def _positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunIndicatorAssessmentError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise RunIndicatorAssessmentError(f"{field} must be finite and positive")
    return number


def _series(raw: Any, field: str, size: int) -> list[float]:
    if not isinstance(raw, list) or len(raw) != size:
        raise RunIndicatorAssessmentError(f"{field} must cover the formal time axis")
    values: list[float] = []
    for index, value in enumerate(raw):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RunIndicatorAssessmentError(f"{field}[{index}] must be numeric")
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise RunIndicatorAssessmentError(f"{field}[{index}] must be finite and nonnegative")
        values.append(number)
    return values


def derive_indicator_facts(
    metrics: Mapping[str, Any],
    snapshot_payload: Mapping[str, Any],
) -> dict[str, IndicatorFact]:
    """Strict Metrics→Indicator mapping for the formal run assessment set."""
    if metrics.get("schema_version") != "metrics.v1":
        raise RunIndicatorAssessmentError("Metrics schema_version must be metrics.v1")
    time_axis = metrics.get("time_axis")
    windows = time_axis.get("windows") if isinstance(time_axis, Mapping) else None
    if not isinstance(windows, list) or not windows or not all(
        isinstance(window, int) and not isinstance(window, bool) for window in windows
    ):
        raise RunIndicatorAssessmentError("Metrics formal time axis is missing or invalid")
    window_count = len(windows)

    summary = metrics.get("summary")
    if not isinstance(summary, Mapping):
        raise RunIndicatorAssessmentError("Metrics summary is missing")
    completion = _ratio(summary.get("completion_ratio"), "completion_ratio")

    situation = snapshot_payload.get("situation")
    raw_airports = situation.get("airports") if isinstance(situation, Mapping) else None
    if not isinstance(raw_airports, list) or not raw_airports:
        raise RunIndicatorAssessmentError("Snapshot airport capacity facts are missing")
    baselines: dict[str, float] = {}
    for raw_airport in raw_airports:
        profile = raw_airport.get("operational_profile") if isinstance(raw_airport, Mapping) else None
        if not isinstance(profile, Mapping) or not isinstance(profile.get("airport_id"), str):
            raise RunIndicatorAssessmentError("Snapshot airport operational profile is incomplete")
        airport_id = str(profile["airport_id"])
        if airport_id in baselines:
            raise RunIndicatorAssessmentError(f"Snapshot airport is duplicated: {airport_id}")
        baselines[airport_id] = _positive(profile.get("capacity_per_window"), f"capacity baseline {airport_id}")

    airport_metrics = metrics.get("airports")
    if not isinstance(airport_metrics, Mapping):
        raise RunIndicatorAssessmentError("Metrics airport capacity is missing")
    capacity_available = 0.0
    capacity_baseline = 0.0
    for airport_id, baseline in baselines.items():
        row = airport_metrics.get(airport_id)
        capacity = row.get("capacity") if isinstance(row, Mapping) else None
        available = _series(
            capacity.get("available") if isinstance(capacity, Mapping) else None,
            f"capacity.available {airport_id}",
            window_count,
        )
        if any(value > baseline + 1e-7 for value in available):
            raise RunIndicatorAssessmentError(f"capacity.available exceeds frozen baseline for {airport_id}")
        capacity_available += sum(available)
        capacity_baseline += baseline * window_count
    capacity_rate = _ratio(capacity_available / capacity_baseline, "capacity availability rate")

    inventory = metrics.get("aircraft_inventory")
    inventory_by_airport = inventory.get("by_airport") if isinstance(inventory, Mapping) else None
    if not isinstance(inventory_by_airport, Mapping) or not inventory_by_airport:
        raise RunIndicatorAssessmentError("Metrics aircraft inventory is missing")
    aircraft_available_by_window = [0.0] * window_count
    aircraft_baseline_total = 0.0
    inventory_rows = 0
    for airport_id, by_type in inventory_by_airport.items():
        if not isinstance(by_type, Mapping):
            raise RunIndicatorAssessmentError(f"aircraft inventory is invalid for {airport_id}")
        for aircraft_type, row in by_type.items():
            if not isinstance(row, Mapping):
                raise RunIndicatorAssessmentError(f"aircraft inventory row is invalid: {airport_id}/{aircraft_type}")
            baseline = _positive(
                row.get("baseline_initial_quantity"),
                f"aircraft baseline {airport_id}/{aircraft_type}",
            )
            available = _series(
                row.get("available_before_departure"),
                f"aircraft available {airport_id}/{aircraft_type}",
                window_count,
            )
            # Cross-airport returns can legitimately make one airport/type exceed its
            # local initial stock. Conservation is therefore checked on the fleet-wide
            # total for each window, while airport rows remain the source facts.
            for index, value in enumerate(available):
                aircraft_available_by_window[index] += value
            aircraft_baseline_total += baseline
            inventory_rows += 1
    if inventory_rows == 0:
        raise RunIndicatorAssessmentError("Metrics aircraft inventory contains no formal rows")
    if any(value > aircraft_baseline_total + 1e-7 for value in aircraft_available_by_window):
        raise RunIndicatorAssessmentError("fleet-wide aircraft availability exceeds frozen initial stock")
    aircraft_available = sum(aircraft_available_by_window)
    aircraft_baseline = aircraft_baseline_total * window_count
    aircraft_rate = _ratio(aircraft_available / aircraft_baseline, "aircraft availability rate")

    resources = metrics.get("resources")
    resource_types = resources.get("resource_types") if isinstance(resources, Mapping) else None
    category_minimum = resources.get("category_min_remaining_ratio") if isinstance(resources, Mapping) else None
    if not isinstance(resource_types, Mapping) or not resource_types:
        raise RunIndicatorAssessmentError("Metrics resource metadata is missing")
    expected_categories = {
        str(item.get("category"))
        for item in resource_types.values()
        if isinstance(item, Mapping) and isinstance(item.get("category"), str)
    }
    if not expected_categories or not isinstance(category_minimum, Mapping):
        raise RunIndicatorAssessmentError("Metrics resource category facts are missing")
    missing_categories = expected_categories - set(category_minimum)
    if missing_categories:
        raise RunIndicatorAssessmentError(
            f"Metrics resource categories are incomplete: {sorted(missing_categories)}"
        )
    resource_ratios: dict[str, float] = {}
    for category in sorted(expected_categories):
        row = category_minimum.get(category)
        if not isinstance(row, Mapping):
            raise RunIndicatorAssessmentError(f"Metrics resource category is invalid: {category}")
        resource_ratios[category] = _ratio(row.get("ratio"), f"resource ratio {category}")
    resource_minimum = min(resource_ratios.values())

    return {
        "task_completion_rate": IndicatorFact(
            "task_completion_rate", completion, completion,
            {"metrics_path": "summary.completion_ratio"},
        ),
        "capacity_availability_rate": IndicatorFact(
            "capacity_availability_rate", capacity_rate, capacity_rate,
            {"available_sum": capacity_available, "frozen_baseline_sum": capacity_baseline, "windows": window_count},
        ),
        "aircraft_availability_rate": IndicatorFact(
            "aircraft_availability_rate", aircraft_rate, aircraft_rate,
            {
                "available_sum": aircraft_available,
                "frozen_initial_sum": aircraft_baseline,
                "frozen_initial_per_window": aircraft_baseline_total,
                "inventory_rows": inventory_rows,
                "validation": "fleet_wide_window_conservation",
            },
        ),
        "resource_reserve_retention_rate": IndicatorFact(
            "resource_reserve_retention_rate", resource_minimum, resource_minimum,
            {"minimum": resource_minimum, "by_category": resource_ratios, "aggregation": "minimum_of_unitless_category_ratios"},
        ),
    }


class RunIndicatorAssessmentService:
    def __init__(
        self,
        *,
        run_repository: RunRepository,
        snapshot_repository: RunSnapshotRepository,
        indicator_repository: IndicatorRepository,
        assessment_repository: AssessmentRepository,
        indicator_set_id: str = FORMAL_SET_ID,
    ) -> None:
        self.runs = run_repository
        self.snapshots = snapshot_repository
        self.indicators = indicator_repository
        self.assessments = assessment_repository
        self.indicator_set_id = indicator_set_id

    def evaluate(self, run_id: str) -> RunIndicatorAssessment:
        record = self.runs.get(run_id)
        if record is None or record.status != "succeeded":
            raise RunIndicatorAssessmentError(f"Run must be a complete succeeded result: {run_id}")
        snapshot = self.snapshots.get(run_id)
        payloads = self.runs.get_result_payloads(run_id)
        if snapshot is None or payloads is None:
            raise RunIndicatorAssessmentError(f"Run has no immutable Snapshot/Solution/Metrics: {run_id}")
        solution, metrics = payloads
        if not isinstance(solution, Mapping) or solution.get("run_id") != run_id:
            return self._save_unavailable(record, "Solution run_id is missing or inconsistent")
        chains = solution.get("sortie_chains")
        if not isinstance(chains, list) or not chains:
            return self._save_unavailable(record, "Solution has no valid sortie chains")

        weights = self.indicators.published_weights(self.indicator_set_id)
        source = weights.get("weight_source") if weights.get("weight_source") in {"expert", "ahp"} else "expert"
        version = str(weights.get("version") or "unrecorded")
        trace = weights.get("trace") if isinstance(weights.get("trace"), Mapping) else {}
        if weights.get("status") != "available":
            return self._save_unavailable(record, "Indicator weights are unavailable", version, source, trace)
        if source == "ahp" and trace.get("consistent") is not True:
            return self._save_unavailable(record, "AHP consistency validation did not pass", version, source, trace)

        try:
            facts = derive_indicator_facts(metrics, snapshot.to_dict())
            rows = weights.get("items")
            if not isinstance(rows, list) or not rows:
                raise RunIndicatorAssessmentError("Indicator weight rows are missing")
            values: list[IndicatorAssessmentValue] = []
            score_map: dict[str, float] = {}
            weight_map: dict[str, float] = {}
            for row in rows:
                if not isinstance(row, Mapping) or row.get("calculation_status") != "available":
                    raise RunIndicatorAssessmentError("Indicator calculation configuration is unavailable")
                code = str(row.get("code") or "")
                fact = facts.get(code)
                if fact is None:
                    raise RunIndicatorAssessmentError(f"Metrics input is unavailable for indicator: {code}")
                raw_weight = row.get("weight")
                if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
                    raise RunIndicatorAssessmentError(f"Indicator weight is invalid: {code}")
                weight = float(raw_weight)
                if not math.isfinite(weight) or weight < 0:
                    raise RunIndicatorAssessmentError(f"Indicator weight is invalid: {code}")
                item_source = row.get("weight_source")
                if item_source != source:
                    raise RunIndicatorAssessmentError(f"Indicator weight source is inconsistent: {code}")
                value = IndicatorAssessmentValue(
                    indicator_id=str(row.get("indicator_id") or ""),
                    code=code,
                    name=str(row.get("name") or code),
                    raw_value=fact.raw_value,
                    normalized_value=fact.normalized_value,
                    weight=weight,
                    weight_source=source,
                    calculation_type=str(row.get("calculation_type") or "unrecorded"),
                    evidence=fact.evidence,
                )
                values.append(value)
                score_map[code] = fact.normalized_value
                weight_map[code] = weight
            fuzzy = evaluate_elements(score_map, weight_map)
            risk = compute_risk(score_map, weight_map)
        except (RunIndicatorAssessmentError, AHPError, FuzzyInputError, ValueError) as exc:
            return self._save_unavailable(record, str(exc), version, source, trace)

        return self.assessments.save_run_evaluation(
            RunIndicatorAssessment(
                run_id=run_id,
                snapshot_hash=record.snapshot_hash,
                metrics_hash=record.metrics_hash,
                indicator_set_id=self.indicator_set_id,
                indicator_set_version=version,
                indicators=tuple(values),
                status="available",
                calculation_version=CALCULATION_VERSION,
                weight_source=source,
                weight_trace=trace,
                support_index=fuzzy.support_index,
                overall_membership=fuzzy.overall_membership,
                weighted_composite=risk.weighted_composite,
                risk_score=risk.risk_score,
                risk_level=risk.risk_level,
                risk_status="available",
            )
        )

    def _save_unavailable(
        self,
        record: Any,
        reason: str,
        version: str = "unrecorded",
        source: str = "expert",
        trace: Mapping[str, Any] | None = None,
    ) -> RunIndicatorAssessment:
        return self.assessments.save_run_evaluation(
            RunIndicatorAssessment(
                run_id=record.run_id,
                snapshot_hash=record.snapshot_hash,
                metrics_hash=record.metrics_hash,
                indicator_set_id=self.indicator_set_id,
                indicator_set_version=version,
                indicators=(),
                status="unavailable",
                unavailable_reason=reason,
                calculation_version=CALCULATION_VERSION,
                weight_source=source,
                weight_trace=dict(trace or {}),
                risk_status="unavailable",
                risk_reason=reason,
            )
        )


__all__ = [
    "CALCULATION_VERSION",
    "IndicatorFact",
    "RunIndicatorAssessmentError",
    "RunIndicatorAssessmentService",
    "derive_indicator_facts",
]
