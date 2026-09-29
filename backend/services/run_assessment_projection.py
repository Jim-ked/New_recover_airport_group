from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from backend.domain.assessment import AssessmentResult
from backend.storage.assessment_repository import AssessmentRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository


CAPACITY_METRIC_KIND = "capacity_availability_v1"


class RunAssessmentProjectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapacityObservation:
    airport_id: str
    baseline_capacity: float
    period_occupancy: float | None
    availability_curve: tuple[tuple[int, float], ...]
    effective_windows: int
    unavailable_windows: int


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunAssessmentProjectionError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise RunAssessmentProjectionError(f"{field} must be finite")
    return number


def derive_capacity_observation(
    *,
    airport_id: str,
    baseline_capacity: Any,
    windows: Sequence[Any],
    capacity: Mapping[str, Any],
) -> CapacityObservation:
    """Derive descriptive capacity facts without assigning risk or capability scores."""
    baseline = _finite_number(baseline_capacity, "baseline_capacity")
    if baseline <= 0:
        raise RunAssessmentProjectionError("baseline_capacity must be positive")
    available = capacity.get("available")
    used_total = capacity.get("used_total")
    if not isinstance(available, list) or not isinstance(used_total, list):
        raise RunAssessmentProjectionError("capacity arrays are missing")
    if len(windows) == 0 or len(available) != len(windows) or len(used_total) != len(windows):
        raise RunAssessmentProjectionError("capacity arrays must cover the formal time axis")

    curve: list[tuple[int, float]] = []
    denominator = 0.0
    numerator = 0.0
    unavailable_windows = 0
    for index, raw_window in enumerate(windows):
        if isinstance(raw_window, bool) or not isinstance(raw_window, int):
            raise RunAssessmentProjectionError("time windows must be integers")
        current_available = _finite_number(available[index], f"available[{index}]")
        current_used = _finite_number(used_total[index], f"used_total[{index}]")
        if current_available < 0 or current_used < 0:
            raise RunAssessmentProjectionError("capacity values cannot be negative")
        if current_used > current_available + 1e-7:
            raise RunAssessmentProjectionError("used capacity exceeds available capacity")
        ratio = current_available / baseline
        if ratio > 1.0 + 1e-7:
            raise RunAssessmentProjectionError("available capacity exceeds frozen baseline")
        curve.append((raw_window, min(1.0, max(0.0, ratio))))
        if current_available > 0:
            denominator += current_available
            numerator += current_used
        else:
            unavailable_windows += 1

    occupancy = None if denominator == 0 else numerator / denominator
    return CapacityObservation(
        airport_id=airport_id,
        baseline_capacity=baseline,
        period_occupancy=occupancy,
        availability_curve=tuple(curve),
        effective_windows=len(windows) - unavailable_windows,
        unavailable_windows=unavailable_windows,
    )


def project_run_capacity_assessment(
    *,
    run_id: str,
    scenario_label: str,
    run_repository: RunRepository,
    snapshot_repository: RunSnapshotRepository,
    assessment_repository: AssessmentRepository,
) -> list[AssessmentResult]:
    record = run_repository.get(run_id)
    snapshot = snapshot_repository.get(run_id)
    payloads = run_repository.get_result_payloads(run_id)
    if record is None or record.status != "succeeded" or snapshot is None or payloads is None:
        raise RunAssessmentProjectionError(f"Run is not a complete succeeded result: {run_id}")
    _solution, metrics = payloads
    time_axis = metrics.get("time_axis")
    airports_metrics = metrics.get("airports")
    snapshot_payload = snapshot.to_dict()
    situation = snapshot_payload.get("situation")
    if not isinstance(time_axis, Mapping) or not isinstance(time_axis.get("windows"), list):
        raise RunAssessmentProjectionError("Metrics has no formal time axis")
    if not isinstance(airports_metrics, Mapping) or not isinstance(situation, Mapping):
        raise RunAssessmentProjectionError("Run facts are missing airport data")

    baselines: dict[str, float] = {}
    raw_airports = situation.get("airports")
    if not isinstance(raw_airports, list):
        raise RunAssessmentProjectionError("Snapshot has no airports")
    for raw in raw_airports:
        if not isinstance(raw, Mapping):
            continue
        profile = raw.get("operational_profile")
        if isinstance(profile, Mapping) and isinstance(profile.get("airport_id"), str):
            baselines[profile["airport_id"]] = _finite_number(
                profile.get("capacity_per_window"), "capacity_per_window"
            )

    persisted: list[AssessmentResult] = []
    for airport_id in sorted(airports_metrics):
        airport_metrics = airports_metrics[airport_id]
        if airport_id not in baselines or not isinstance(airport_metrics, Mapping):
            raise RunAssessmentProjectionError(f"missing frozen capacity facts for {airport_id}")
        capacity = airport_metrics.get("capacity")
        if not isinstance(capacity, Mapping):
            raise RunAssessmentProjectionError(f"missing capacity Metrics for {airport_id}")
        observation = derive_capacity_observation(
            airport_id=airport_id,
            baseline_capacity=baselines[airport_id],
            windows=time_axis["windows"],
            capacity=capacity,
        )
        values = [value for _window, value in observation.availability_curve]
        minimum_index = values.index(min(values))
        remaining = len(values) - minimum_index - 1
        recovery_slope = (
            max(0.0, values[-1] - values[minimum_index]) / remaining
            if remaining > 0 else 0.0
        )
        result = AssessmentResult.from_mapping(
            {
                "id": f"v5cap:{run_id}:{airport_id}",
                "airport_id": airport_id,
                "scenario_type": scenario_label,
                "curves": dict(observation.availability_curve),
                "decline_rate": 1.0 - min(values),
                "recovery_slope": recovery_slope,
                "initial_capacity": observation.baseline_capacity,
                "metric_kind": CAPACITY_METRIC_KIND,
                "matrix_value": observation.period_occupancy,
                "source_run_id": run_id,
                "snapshot_hash": record.snapshot_hash,
                "metrics_hash": record.metrics_hash,
                "effective_windows": observation.effective_windows,
                "unavailable_windows": observation.unavailable_windows,
            }
        )
        persisted.append(assessment_repository.save_result(result))
    return persisted


__all__ = [
    "CAPACITY_METRIC_KIND",
    "CapacityObservation",
    "RunAssessmentProjectionError",
    "derive_capacity_observation",
    "project_run_capacity_assessment",
]
