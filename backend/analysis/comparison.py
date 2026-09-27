from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from math import ceil, isfinite
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from backend.algorithm.snapshot_adapter import DELTA_MIN, build_algorithm_comparison_facts
from backend.domain.run_snapshot import RunSnapshot

COMPARISON_SCHEMA_VERSION = "comparison.v1"
MAX_ANALYSIS_RUNS = 50
OBJECTIVE_DEFINITION_FIELDS = (
    "preference_mode",
    "alpha",
    "aircraft_type_weight",
    "f2_resource_reference_quantities",
    "f2_resource_weights",
    "f3_time_reference_slots",
    "f3_tardiness_coefficient",
    "unmet_demand_penalty",
)


class ComparisonError(ValueError):
    pass


@dataclass(frozen=True)
class ComparabilityCheck:
    comparable: bool
    reasons: Tuple[str, ...] = ()
    differences: Tuple[str, ...] = ()

    def require(self) -> None:
        if not self.comparable:
            raise ComparisonError("; ".join(self.reasons))


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _payload(snapshot: RunSnapshot) -> Dict[str, Any]:
    if not isinstance(snapshot, RunSnapshot):
        raise ComparisonError("comparison requires RunSnapshot inputs")
    return snapshot.to_dict()


def _config(payload: Mapping[str, Any]) -> Dict[str, Any]:
    cfg = payload.get("run_config")
    if not isinstance(cfg, dict):
        raise ComparisonError("snapshot run_config is missing")
    return cfg


def _archived_v5_selected_scenario(payload: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    situation = payload.get("situation") or {}
    selected_id = (payload.get("run_config") or {}).get("damage_scenario_id")
    if selected_id is None:
        return None
    for row in situation.get("damage_scenarios") or []:
        if isinstance(row, Mapping) and row.get("damage_scenario_id") == selected_id:
            return row
    raise ComparisonError(f"selected damage scenario is absent from archived v5 snapshot: {selected_id}")


def _archived_v5_effective_range(payload: Mapping[str, Any]) -> List[int]:
    """Reproduce the frozen v5 loose-horizon policy without invoking the current solver."""

    situation = payload.get("situation") or {}
    missions = [row for row in situation.get("missions") or [] if isinstance(row, Mapping)]
    scenario = _archived_v5_selected_scenario(payload)
    starts = [int(row["window_start_slot"]) for row in missions]
    ends = [int(row["window_end_slot"]) - 1 for row in missions]
    events = [row for row in (scenario or {}).get("events") or [] if isinstance(row, Mapping)]
    starts.extend(int(row["start_slot"]) for row in events)
    ends.extend(int(row["end_slot"]) - 1 for row in events)
    t_min = min(starts) if starts else 0
    base_end = max(ends) if ends else t_min

    mission_types = {
        str(req["aircraft_type_id"])
        for mission in missions
        for req in mission.get("aircraft_requirements") or []
        if isinstance(req, Mapping)
    }
    aircraft_rows = {
        str(row.get("aircraft_type_id")): row
        for row in (payload.get("catalogs") or {}).get("aircraft_types") or []
        if isinstance(row, Mapping)
    }
    try:
        speeds = [float(aircraft_rows[type_id]["speed_kmh"]) for type_id in mission_types]
    except (KeyError, TypeError, ValueError) as exc:
        raise ComparisonError("archived v5 aircraft speed facts are incomplete") from exc
    if any(value <= 0 for value in speeds):
        raise ComparisonError("archived v5 aircraft speed facts must be positive")

    distances = [
        float(row["distance_km"])
        for row in payload.get("od_distances") or []
        if isinstance(row, Mapping)
    ]
    max_dist = max(distances, default=0.0)
    min_speed = min(speeds, default=1.0)
    max_fly_windows = int(ceil((max_dist / min_speed) * (60.0 / DELTA_MIN))) if max_dist else 0
    max_tau_work = max(
        (
            int(req["tau_work_windows"])
            for mission in missions
            for req in mission.get("aircraft_requirements") or []
            if isinstance(req, Mapping)
        ),
        default=0,
    )
    max_delay = max(
        (
            max(
                int((event.get("effect") or {}).get("departure_delay_slots") or 0),
                int((event.get("effect") or {}).get("return_delay_slots") or 0),
            )
            for event in events
            if event.get("damage_type") == "navigation_delay"
        ),
        default=0,
    )
    return [int(t_min), int(max(t_min, base_end + 2 * max_fly_windows + max_tau_work + 2 * max_delay))]


def _archived_v5_comparison_facts(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Project immutable v5 inputs for read-only comparison; never adapts them for solving."""

    situation = payload.get("situation") or {}
    catalogs = payload.get("catalogs") or {}
    airport_rows = []
    airport_resources = []
    airport_ids = []
    for item in situation.get("airports") or []:
        if not isinstance(item, Mapping):
            continue
        airport = item.get("airport") or {}
        profile = item.get("operational_profile") or {}
        airport_id = str(airport.get("airport_id") or profile.get("airport_id") or "")
        airport_ids.append(airport_id)
        support = [row for row in profile.get("aircraft_support") or [] if isinstance(row, Mapping)]
        airport_rows.append({
            "airport_id": airport_id,
            "lon": float(airport["longitude"]),
            "lat": float(airport["latitude"]),
            "capacity": int(profile.get("capacity_per_window") or 0),
            "supported_aircraft": {
                str(row["aircraft_type_id"]): int(row.get("initial_quantity") or 0)
                for row in support
            },
            "tau_reset": {
                str(row["aircraft_type_id"]): int(row.get("tau_reset_windows") or 0)
                for row in support
            },
        })
        airport_resources.append({
            "airport_id": airport_id,
            "resource_stocks": sorted(
                (
                    {
                        "resource_type_id": row.get("resource_type_id"),
                        "initial_quantity": row.get("initial_quantity"),
                        "storage_capacity": row.get("storage_capacity"),
                    }
                    for row in profile.get("resource_stocks") or []
                    if isinstance(row, Mapping)
                ),
                key=lambda row: str(row["resource_type_id"]),
            ),
            "resource_replenishments": sorted(
                (dict(row) for row in item.get("resource_replenishments") or [] if isinstance(row, Mapping)),
                key=lambda row: (
                    str(row.get("resource_type_id")),
                    int(row.get("start_slot") or 0),
                    int(row.get("end_slot") or 0),
                ),
            ),
        })

    mission_rows = []
    mission_ids = []
    for mission in situation.get("missions") or []:
        if not isinstance(mission, Mapping):
            continue
        mission_id = str(mission.get("mission_id") or "")
        mission_ids.append(mission_id)
        requirements = [
            row for row in mission.get("aircraft_requirements") or [] if isinstance(row, Mapping)
        ]
        mission_rows.append({
            "mission_id": mission_id,
            "lon": float(mission["longitude"]),
            "lat": float(mission["latitude"]),
            "_duty_window": [int(mission["window_start_slot"]), int(mission["window_end_slot"])],
            "required_sorties": {
                str(row["aircraft_type_id"]): int(row["required_sorties"])
                for row in requirements
            },
            "tau_work": {
                str(row["aircraft_type_id"]): int(row["tau_work_windows"])
                for row in requirements
            },
        })

    od = {
        (str(row["airport_id"]), str(row["mission_id"])): float(row["distance_km"])
        for row in payload.get("od_distances") or []
        if isinstance(row, Mapping)
    }
    resource_types = {
        str(row["resource_type_id"]): row
        for row in catalogs.get("resource_types") or []
        if isinstance(row, Mapping)
    }
    requirements_by_aircraft: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in catalogs.get("aircraft_resource_requirements") or []:
        if not isinstance(row, Mapping):
            continue
        requirements_by_aircraft.setdefault(str(row["aircraft_type_id"]), {})[
            str(row["resource_type_id"])
        ] = {"basis": row.get("basis"), "quantity": float(row["quantity"])}

    aircraft_parameters = {}
    for row in catalogs.get("aircraft_types") or []:
        if not isinstance(row, Mapping):
            continue
        type_id = str(row["aircraft_type_id"])
        uses = requirements_by_aircraft.get(type_id, {})
        aircraft_parameters[type_id] = {
            "speed": float(row["speed_kmh"]),
            "max_range": float(row["max_range_km"]),
            "reserve_ratio": float(row["reserve_ratio"]),
            "capacity_factor": float(row["departure_capacity_occupancy_factor"]),
            "arrival_capacity_factor": float(row["arrival_capacity_occupancy_factor"]),
            "resource_requirements": uses,
        }

    selected = _archived_v5_selected_scenario(payload)
    return {
        "schema": payload.get("schema"),
        "base_problem": {
            "airports": airport_rows,
            "missions": mission_rows,
            "distance": {
                "airports": airport_ids,
                "missions": mission_ids,
                "matrix": [[od[(aid, mid)] for mid in mission_ids] for aid in airport_ids],
            },
            "aircraft_parameters": aircraft_parameters,
            "resource_types": sorted(
                (
                    {
                        "resource_type_id": resource_id,
                        "category": row.get("category"),
                        "unit": row.get("unit"),
                    }
                    for resource_id, row in resource_types.items()
                ),
                key=lambda row: row["resource_type_id"],
            ),
            "airport_resources": airport_resources,
            "dynamic_inventory": payload.get("dynamic_inventory"),
        },
        "effective_range": _archived_v5_effective_range(payload),
        "effective_timeview": {
            "selected_damage_scenario": selected,
            "resource_inventory_model": (payload.get("run_config") or {}).get("resource_inventory_model"),
            "dynamic_inventory": payload.get("dynamic_inventory"),
        },
    }


def _effective_input_differences(base: RunSnapshot, other: RunSnapshot) -> List[str]:
    a = _comparison_facts(base)
    b = _comparison_facts(other)
    reasons: List[str] = []
    if a.get("schema") != b.get("schema"):
        reasons.append("snapshot schema differs")
    pa = a.get("base_problem") or {}
    pb = b.get("base_problem") or {}
    for field, label in (
        ("airports", "base airport inputs differ"),
        ("missions", "base mission inputs differ"),
        ("distance", "OD distance inputs differ"),
        ("aircraft_parameters", "aircraft type/requirement inputs differ"),
        ("resource_types", "resource type inputs differ"),
        ("airport_resources", "airport resource/replenishment inputs differ"),
    ):
        if _canon(pa.get(field)) != _canon(pb.get(field)):
            reasons.append(label)
    if _canon(a.get("effective_range")) != _canon(b.get("effective_range")):
        reasons.append("effective time range differs")
    return reasons


def _effective_state_differs(base: RunSnapshot, other: RunSnapshot) -> bool:
    a = _comparison_facts(base)
    b = _comparison_facts(other)
    return _canon(a.get("effective_timeview")) != _canon(b.get("effective_timeview"))


@lru_cache(maxsize=64)
def _comparison_facts(snapshot: RunSnapshot) -> Dict[str, Any]:
    # RunSnapshot is immutable and hashable; the returned projection stays private and
    # is never mutated.  Candidate scans otherwise rematerialize the same base Run many
    # times while checking different comparison modes.
    payload = snapshot.to_dict()
    if payload.get("schema") == "run_input_snapshot_v5":
        return _archived_v5_comparison_facts(payload)
    return build_algorithm_comparison_facts(snapshot)


def _same_fields(configs: Sequence[Mapping[str, Any]], fields: Iterable[str]) -> List[str]:
    reasons: List[str] = []
    if not configs:
        return reasons
    first = configs[0]
    for field in fields:
        ref = _canon(first.get(field))
        if any(_canon(cfg.get(field)) != ref for cfg in configs[1:]):
            reasons.append(f"run_config.{field} differs")
    return reasons


def check_multi_scenario_comparable(base: RunSnapshot, other: RunSnapshot) -> ComparabilityCheck:
    """Same plan/configuration; only the selected damage scenario may differ."""
    a, b = _payload(base), _payload(other)
    reasons = _effective_input_differences(base, other)
    ca, cb = _config(a), _config(b)
    for field in sorted(set(ca) | set(cb)):
        if field == "damage_scenario_id":
            continue
        if _canon(ca.get(field)) != _canon(cb.get(field)):
            reasons.append(f"run_config.{field} differs")
    return ComparabilityCheck(not reasons, tuple(reasons))


def check_configuration_comparable(base: RunSnapshot, other: RunSnapshot) -> ComparabilityCheck:
    """Configuration comparison under the same Situation and damage condition.

    Preference/alpha, cluster settings, core airports and aircraft weights are allowed to
    vary. Solver time limit and algorithm seed are frozen so the comparison does not mix
    business configuration changes with a different search budget/random stream.
    """
    a, b = _payload(base), _payload(other)
    reasons = _effective_input_differences(base, other)
    ca, cb = _config(a), _config(b)
    if ca.get("damage_scenario_id") != cb.get("damage_scenario_id"):
        reasons.append("damage_scenario_id differs")
    elif _effective_state_differs(base, other):
        reasons.append("selected damage effective input differs")
    reasons.extend(_same_fields([ca, cb], ("mip_time_limit_s", "algorithm_seed")))
    return ComparabilityCheck(not reasons, tuple(reasons))


def check_exploratory_comparable(base: RunSnapshot, other: RunSnapshot) -> ComparabilityCheck:
    """Describe cross-condition input differences without claiming strict comparability."""

    a, b = _payload(base), _payload(other)
    differences = _effective_input_differences(base, other)
    ca, cb = _config(a), _config(b)
    for field in sorted(set(ca) | set(cb)):
        if _canon(ca.get(field)) != _canon(cb.get(field)):
            differences.append(f"run_config.{field} differs")
    if _effective_state_differs(base, other):
        differences.append("effective damage/state input differs")
    return ComparabilityCheck(True, (), tuple(dict.fromkeys(differences)))


def check_objective_comparable(*snapshots: RunSnapshot) -> ComparabilityCheck:
    """Whether raw solver objectives share the same coefficient definition.

    Clustering changes the feasible path set, but it does not by itself change an
    objective coefficient. Core-airport identities and their outer-search reward do not
    enter the final MIP objective; path coefficient calibration and resolved preference
    weights do, so those fields are compared explicitly.
    """

    configs = [_config(_payload(snapshot)) for snapshot in snapshots]
    reasons = _same_fields(configs, OBJECTIVE_DEFINITION_FIELDS)
    return ComparabilityCheck(not reasons, tuple(reasons))


def check_r0_r1_r2(r0: RunSnapshot, r1: RunSnapshot, r2: RunSnapshot) -> ComparabilityCheck:
    """Validate the fixed Results roles used by the damage/optimization workspace.

    R0 = no damage / no cluster
    R1 = target damage / no cluster
    R2 = same target damage / cluster enabled
    """
    p0, p1, p2 = _payload(r0), _payload(r1), _payload(r2)
    reasons = _effective_input_differences(r0, r1) + _effective_input_differences(r0, r2)
    if len({r0.run_id, r1.run_id, r2.run_id}) != 3:
        reasons.append("R0/R1/R2 must be three distinct Run IDs")
    c0, c1, c2 = _config(p0), _config(p1), _config(p2)

    if c0.get("damage_scenario_id") is not None:
        reasons.append("R0 must have no damage scenario")
    if bool(c0.get("cluster_enabled")):
        reasons.append("R0 must have clustering disabled")
    if c1.get("damage_scenario_id") is None:
        reasons.append("R1 must select a damage scenario")
    if bool(c1.get("cluster_enabled")):
        reasons.append("R1 must have clustering disabled")
    if c2.get("damage_scenario_id") != c1.get("damage_scenario_id"):
        reasons.append("R2 must use the same damage scenario as R1")
    elif _effective_state_differs(r1, r2):
        reasons.append("R1/R2 selected damage effective input differs")
    if not bool(c2.get("cluster_enabled")):
        reasons.append("R2 must have clustering enabled")

    reasons.extend(
        _same_fields(
            [c0, c1, c2],
            (
                "preference_mode",
                "alpha",
                "aircraft_type_weight",
                "mip_time_limit_s",
                "algorithm_seed",
            ),
        )
    )
    # Stable/deterministic error order without hiding duplicate root causes.
    reasons = list(dict.fromkeys(reasons))
    return ComparabilityCheck(not reasons, tuple(reasons))


def _ensure_metrics(snapshot: RunSnapshot, metrics: Mapping[str, Any]) -> None:
    if metrics.get("run_id") != snapshot.run_id:
        raise ComparisonError(f"Metrics run_id does not match snapshot: {snapshot.run_id}")
    if not isinstance(metrics.get("time_axis"), dict):
        raise ComparisonError(f"Metrics time_axis missing: {snapshot.run_id}")


def _aligned_series(metrics: Mapping[str, Any], field: str, windows: Sequence[int]) -> List[float]:
    axis = metrics["time_axis"]
    own_windows = list(axis.get("windows") or [])
    series = ((metrics.get("timeline") or {}).get(field) or [])
    if len(own_windows) != len(series):
        raise ComparisonError(f"timeline.{field} length mismatch for run {metrics.get('run_id')}")
    by_t = {int(t): float(v) for t, v in zip(own_windows, series)}
    return [by_t.get(int(t), 0.0) for t in windows]


def _delta(a: Sequence[float], b: Sequence[float]) -> List[float]:
    if len(a) != len(b):
        raise ComparisonError("series length mismatch")
    return [float(y) - float(x) for x, y in zip(a, b)]


def _scalar_delta(r0: Any, r1: Any, r2: Any) -> Dict[str, Any]:
    def num(v: Any) -> Optional[float]:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    a, b, c = num(r0), num(r1), num(r2)
    return {
        "R0": r0,
        "R1": r1,
        "R2": r2,
        "damage_delta": None if a is None or b is None else b - a,
        "cluster_delta": None if b is None or c is None else c - b,
    }


def build_r0_r1_r2_comparison(
    *,
    r0_snapshot: RunSnapshot,
    r0_metrics: Mapping[str, Any],
    r1_snapshot: RunSnapshot,
    r1_metrics: Mapping[str, Any],
    r2_snapshot: RunSnapshot,
    r2_metrics: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build backend-derived comparison facts for the first Results workspace."""
    check = check_r0_r1_r2(r0_snapshot, r1_snapshot, r2_snapshot)
    check.require()
    for snap, metrics in ((r0_snapshot, r0_metrics), (r1_snapshot, r1_metrics), (r2_snapshot, r2_metrics)):
        _ensure_metrics(snap, metrics)
    metric_schemas = {
        str(m.get("schema_version") or "")
        for m in (r0_metrics, r1_metrics, r2_metrics)
    }
    if len(metric_schemas) != 1 or "" in metric_schemas:
        raise ComparisonError("Metrics schema_version differs or is missing")
    slot_minutes = {
        int((m.get("time_axis") or {}).get("slot_minutes") or 0)
        for m in (r0_metrics, r1_metrics, r2_metrics)
    }
    if len(slot_minutes) != 1 or 0 in slot_minutes:
        raise ComparisonError("Metrics time-axis slot size differs or is missing")

    windows = sorted(
        set(r0_metrics["time_axis"].get("windows") or [])
        | set(r1_metrics["time_axis"].get("windows") or [])
        | set(r2_metrics["time_axis"].get("windows") or [])
    )
    d0 = _aligned_series(r0_metrics, "departures_total", windows)
    d1 = _aligned_series(r1_metrics, "departures_total", windows)
    d2 = _aligned_series(r2_metrics, "departures_total", windows)
    ret0 = _aligned_series(r0_metrics, "returns_total", windows)
    ret1 = _aligned_series(r1_metrics, "returns_total", windows)
    ret2 = _aligned_series(r2_metrics, "returns_total", windows)

    def summary_metric(key: str) -> Dict[str, Any]:
        return _scalar_delta(
            (r0_metrics.get("summary") or {}).get(key),
            (r1_metrics.get("summary") or {}).get(key),
            (r2_metrics.get("summary") or {}).get(key),
        )

    airport_ids = sorted(
        set((r0_metrics.get("airports") or {}).keys())
        | set((r1_metrics.get("airports") or {}).keys())
        | set((r2_metrics.get("airports") or {}).keys())
    )
    airports: Dict[str, Any] = {}
    for aid in airport_ids:
        departure_totals = []
        departure_shares = []
        for metrics in (r0_metrics, r1_metrics, r2_metrics):
            row = ((metrics.get("airports") or {}).get(aid) or {})
            departure_totals.append(row.get("departures_total"))
            departure_shares.append(row.get("departure_share"))
        airports[aid] = {
            "departures_total": _scalar_delta(*departure_totals),
            "departure_share": _scalar_delta(*departure_shares),
        }

    mission_ids = sorted(
        set((r0_metrics.get("tasks") or {}).keys())
        | set((r1_metrics.get("tasks") or {}).keys())
        | set((r2_metrics.get("tasks") or {}).keys())
    )
    tasks: Dict[str, Any] = {}
    for mid in mission_ids:
        tasks[mid] = {}
        for field in ("required_total", "scheduled_total"):
            vals = [
                ((m.get("tasks") or {}).get(mid) or {}).get(field)
                for m in (r0_metrics, r1_metrics, r2_metrics)
            ]
            tasks[mid][field] = _scalar_delta(*vals)

    aircraft_ids = sorted(
        set((r0_metrics.get("aircraft") or {}).keys())
        | set((r1_metrics.get("aircraft") or {}).keys())
        | set((r2_metrics.get("aircraft") or {}).keys())
    )
    aircraft: Dict[str, Any] = {}
    for fid in aircraft_ids:
        vals = [((m.get("aircraft") or {}).get(fid) or {}).get("scheduled_total") for m in (r0_metrics, r1_metrics, r2_metrics)]
        aircraft[fid] = {"scheduled_total": _scalar_delta(*vals)}

    resource_min: Dict[str, Any] = {}
    for category in ("fuel", "material", "munition"):
        vals = []
        details = []
        for metrics in (r0_metrics, r1_metrics, r2_metrics):
            row = (((metrics.get("resources") or {}).get("category_min_remaining_ratio") or {}).get(category))
            details.append(row)
            vals.append(None if row is None else row.get("ratio"))
        resource_min[category] = {
            **_scalar_delta(*vals),
            "details": {"R0": details[0], "R1": details[1], "R2": details[2]},
        }

    collaboration = {
        key: _scalar_delta(
            (r0_metrics.get("collaboration") or {}).get(key),
            (r1_metrics.get("collaboration") or {}).get(key),
            (r2_metrics.get("collaboration") or {}).get(key),
        )
        for key in ("departure_hhi", "cross_return_ratio")
    }

    role_summaries = {
        "R0": _comparison_summary(r0_metrics),
        "R1": _comparison_summary(r1_metrics),
        "R2": _comparison_summary(r2_metrics),
    }
    run_summaries = {
        r0_snapshot.run_id: _run_summary_projection(r0_metrics),
        r1_snapshot.run_id: _run_summary_projection(r1_metrics),
        r2_snapshot.run_id: _run_summary_projection(r2_metrics),
    }
    objective_check = check_objective_comparable(r0_snapshot, r1_snapshot, r2_snapshot)
    slot_minutes_value = next(iter(slot_minutes))

    peak_window = _scalar_delta(
        role_summaries["R0"].get("peak_window"),
        role_summaries["R1"].get("peak_window"),
        role_summaries["R2"].get("peak_window"),
    )
    peak_sorties = _scalar_delta(
        role_summaries["R0"].get("peak_sorties"),
        role_summaries["R1"].get("peak_sorties"),
        role_summaries["R2"].get("peak_sorties"),
    )
    max_share = _scalar_delta(
        ((role_summaries["R0"].get("max_airport_departure") or {}).get("share")),
        ((role_summaries["R1"].get("max_airport_departure") or {}).get("share")),
        ((role_summaries["R2"].get("max_airport_departure") or {}).get("share")),
    )
    min_resource = _scalar_delta(
        ((role_summaries["R0"].get("minimum_resource_remaining") or {}).get("ratio")),
        ((role_summaries["R1"].get("minimum_resource_remaining") or {}).get("ratio")),
        ((role_summaries["R2"].get("minimum_resource_remaining") or {}).get("ratio")),
    )
    participant_count = _scalar_delta(
        role_summaries["R0"].get("participating_airport_count"),
        role_summaries["R1"].get("participating_airport_count"),
        role_summaries["R2"].get("participating_airport_count"),
    )
    peak_time_delta_minutes = {
        "damage_delta": None if peak_window["damage_delta"] is None else peak_window["damage_delta"] * slot_minutes_value,
        "cluster_delta": None if peak_window["cluster_delta"] is None else peak_window["cluster_delta"] * slot_minutes_value,
    }
    scheme = {
        role: {
            "selected_cluster": list((metrics.get("collaboration") or {}).get("selected_cluster") or []),
            "participating_airports": list((metrics.get("collaboration") or {}).get("participating_airports") or []),
            "departure_hhi": (metrics.get("collaboration") or {}).get("departure_hhi"),
            "cross_return_ratio": (metrics.get("collaboration") or {}).get("cross_return_ratio"),
        }
        for role, metrics in (("R0", r0_metrics), ("R1", r1_metrics), ("R2", r2_metrics))
    }

    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "roles": {
            "R0": r0_snapshot.run_id,
            "R1": r1_snapshot.run_id,
            "R2": r2_snapshot.run_id,
        },
        "definitions": {
            "damage_delta": "R1-R0",
            "cluster_delta": "R2-R1",
        },
        "objective_comparable": objective_check.comparable,
        "objective_comparability_reasons": list(objective_check.reasons),
        "solver_comparison": _solver_comparison(
            [
                (r0_snapshot, r0_metrics),
                (r1_snapshot, r1_metrics),
                (r2_snapshot, r2_metrics),
            ],
            baseline_run_id=r0_snapshot.run_id,
            objective_check=objective_check,
        ),
        "run_summaries": run_summaries,
        "labels": _frozen_labels(r0_snapshot),
        "comparison_summary": role_summaries,
        "difference_overview": {
            "peak_window": peak_window,
            "peak_time_delta_minutes": peak_time_delta_minutes,
            "peak_sorties": peak_sorties,
            "max_airport_departure_share": max_share,
            "minimum_resource_remaining_ratio": min_resource,
            "participating_airport_count": participant_count,
        },
        "scheme": scheme,
        "timeline": {
            "windows": windows,
            "departures": {
                "R0": d0, "R1": d1, "R2": d2,
                "damage_delta": _delta(d0, d1),
                "cluster_delta": _delta(d1, d2),
            },
            "returns": {
                "R0": ret0, "R1": ret1, "R2": ret2,
                "damage_delta": _delta(ret0, ret1),
                "cluster_delta": _delta(ret1, ret2),
            },
            "by_airport": _timeline_object_rows(
                [(r0_snapshot, r0_metrics), (r1_snapshot, r1_metrics), (r2_snapshot, r2_metrics)],
                axis_name="by_airport",
            ),
            "by_mission": _timeline_object_rows(
                [(r0_snapshot, r0_metrics), (r1_snapshot, r1_metrics), (r2_snapshot, r2_metrics)],
                axis_name="by_mission",
            ),
            "by_aircraft": _timeline_object_rows(
                [(r0_snapshot, r0_metrics), (r1_snapshot, r1_metrics), (r2_snapshot, r2_metrics)],
                axis_name="by_aircraft",
            ),
        },
        "summary": {
            "required_sorties_total": summary_metric("required_sorties_total"),
            "fulfilled_sorties_total": summary_metric("fulfilled_sorties_total"),
            "unmet_sorties_total": summary_metric("unmet_sorties_total"),
            "additional_sorties_total": summary_metric("additional_sorties_total"),
            "scheduled_sorties_total": summary_metric("scheduled_sorties_total"),
            "completion_ratio": summary_metric("completion_ratio"),
            "participating_airport_count": summary_metric("participating_airport_count"),
        },
        "airports": airports,
        "tasks": tasks,
        "aircraft": aircraft,
        "resources": {"category_min_remaining_ratio": resource_min},
        "collaboration": collaboration,
    }



def _require_common_metrics_contract(
    rows: Sequence[Tuple[RunSnapshot, Mapping[str, Any]]],
) -> Tuple[int, List[int]]:
    if not rows:
        raise ComparisonError("comparison requires at least one Run")
    schemas = set()
    slot_sizes = set()
    windows_ref: Optional[List[int]] = None
    for snapshot, metrics in rows:
        _ensure_metrics(snapshot, metrics)
        schema = str(metrics.get("schema_version") or "")
        if not schema:
            raise ComparisonError(f"Metrics schema_version missing: {snapshot.run_id}")
        schemas.add(schema)
        axis = metrics.get("time_axis") or {}
        slot = int(axis.get("slot_minutes") or 0)
        if slot <= 0:
            raise ComparisonError(f"Metrics slot_minutes missing/invalid: {snapshot.run_id}")
        slot_sizes.add(slot)
        windows = [int(x) for x in (axis.get("windows") or [])]
        if not windows:
            raise ComparisonError(f"Metrics windows missing: {snapshot.run_id}")
        if windows_ref is None:
            windows_ref = windows
        elif windows != windows_ref:
            raise ComparisonError("Metrics time-axis windows differ")
    if len(schemas) != 1:
        raise ComparisonError("Metrics schema_version differs")
    if len(slot_sizes) != 1:
        raise ComparisonError("Metrics time-axis slot size differs")
    assert windows_ref is not None
    return next(iter(slot_sizes)), windows_ref


def _minimum_resource_summary(metrics: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    rows = []
    block = ((metrics.get("resources") or {}).get("category_min_remaining_ratio") or {})
    for category in ("fuel", "material", "munition"):
        row = block.get(category)
        if not isinstance(row, Mapping):
            continue
        ratio = row.get("ratio")
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
            continue
        rows.append((float(ratio), category, dict(row)))
    if not rows:
        return None
    ratio, category, detail = min(
        rows,
        key=lambda item: (
            item[0],
            item[1],
            str(item[2].get("airport_id") or ""),
            str(item[2].get("resource_type_id") or ""),
            int(item[2].get("window") or 0),
        ),
    )
    return {"ratio": ratio, "category": category, **detail}


def _run_summary_projection(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    """Copy the canonical per-Run facts shared by every Comparison mode."""
    summary = metrics.get("summary") or {}
    collaboration = metrics.get("collaboration") or {}
    peak = summary.get("peak_departure_slot")
    max_airport = summary.get("max_airport_departure")
    return {
        "mission_count": summary.get("mission_count"),
        "required_sorties_total": summary.get("required_sorties_total"),
        "fulfilled_sorties_total": summary.get("fulfilled_sorties_total"),
        "unmet_sorties_total": summary.get("unmet_sorties_total"),
        "additional_sorties_total": summary.get("additional_sorties_total"),
        "scheduled_sorties_total": summary.get("scheduled_sorties_total"),
        "returned_sorties_total": summary.get("returned_sorties_total"),
        "selected_cluster_count": summary.get("selected_cluster_count"),
        "participating_airport_count": summary.get("participating_airport_count"),
        "peak_departure_slot": dict(peak) if isinstance(peak, Mapping) else None,
        "max_airport_departure": (
            dict(max_airport) if isinstance(max_airport, Mapping) else None
        ),
        "minimum_resource_remaining": _minimum_resource_summary(metrics),
        "departure_hhi": collaboration.get("departure_hhi"),
        "cross_return_ratio": collaboration.get("cross_return_ratio"),
    }


def _frozen_labels(snapshot: RunSnapshot) -> Dict[str, Dict[str, str]]:
    """Project display labels exclusively from the immutable RunSnapshot."""
    payload = _payload(snapshot)
    situation = payload.get("situation") or {}
    catalogs = payload.get("catalogs") or {}
    airports: Dict[str, str] = {}
    for item in situation.get("airports") or []:
        airport = item.get("airport") if isinstance(item, Mapping) else None
        if not isinstance(airport, Mapping):
            continue
        airport_id = airport.get("airport_id")
        if isinstance(airport_id, str) and airport_id:
            airports[airport_id] = str(airport.get("airport_name") or airport_id)
    missions: Dict[str, str] = {}
    for mission in situation.get("missions") or []:
        if not isinstance(mission, Mapping):
            continue
        mission_id = mission.get("mission_id")
        if isinstance(mission_id, str) and mission_id:
            missions[mission_id] = str(mission.get("name") or mission_id)
    aircraft: Dict[str, str] = {}
    for item in catalogs.get("aircraft_types") or []:
        if not isinstance(item, Mapping):
            continue
        aircraft_type_id = item.get("aircraft_type_id")
        if isinstance(aircraft_type_id, str) and aircraft_type_id:
            aircraft[aircraft_type_id] = str(item.get("name") or aircraft_type_id)
    return {"airports": airports, "missions": missions, "aircraft": aircraft}


def _comparison_summary(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    summary = metrics.get("summary") or {}
    peak = summary.get("peak_departure_slot") or {}
    max_airport = summary.get("max_airport_departure") or {}
    collaboration = metrics.get("collaboration") or {}
    technical = metrics.get("technical") or {}
    return {
        "required_sorties_total": summary.get("required_sorties_total"),
        "fulfilled_sorties_total": summary.get("fulfilled_sorties_total"),
        "unmet_sorties_total": summary.get("unmet_sorties_total"),
        "additional_sorties_total": summary.get("additional_sorties_total"),
        "scheduled_sorties_total": summary.get("scheduled_sorties_total"),
        "completion_ratio": summary.get("completion_ratio"),
        "peak_window": peak.get("window"),
        "peak_sorties": peak.get("sorties"),
        "max_airport_departure": dict(max_airport) if isinstance(max_airport, Mapping) else None,
        "minimum_resource_remaining": _minimum_resource_summary(metrics),
        "participating_airport_count": summary.get("participating_airport_count"),
        "selected_cluster_count": summary.get("selected_cluster_count"),
        "departure_hhi": collaboration.get("departure_hhi"),
        "cross_return_ratio": collaboration.get("cross_return_ratio"),
        "objective": technical.get("objective"),
    }


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


_SOLVER_FACT_FIELDS = (
    "solver_status",
    "objective",
    "best_bound",
    "gap",
    "solve_time_s",
    "cluster_lp_objective",
    "f1",
    "f2",
    "f3",
)


def _solver_comparison(
    rows: Sequence[Tuple[RunSnapshot, Mapping[str, Any]]],
    *,
    baseline_run_id: str,
    objective_check: ComparabilityCheck,
) -> Dict[str, Any]:
    by_run: Dict[str, Any] = {}
    for snapshot, metrics in rows:
        technical = metrics.get("technical") or {}
        projected = {field: technical.get(field) for field in _SOLVER_FACT_FIELDS}
        projected["missing_fields"] = [
            field
            for field in _SOLVER_FACT_FIELDS
            if field not in technical or technical.get(field) is None
        ]
        by_run[snapshot.run_id] = projected

    baseline_objective = _numeric(by_run[baseline_run_id].get("objective"))
    for run_id, projected in by_run.items():
        objective = _numeric(projected.get("objective"))
        projected["objective_delta_vs_baseline"] = (
            objective - baseline_objective
            if objective_check.comparable
            and objective is not None
            and baseline_objective is not None
            else None
        )

    proven: List[Dict[str, Any]] = []
    if objective_check.comparable:
        run_ids = [snapshot.run_id for snapshot, _metrics in rows]
        for higher_run_id in run_ids:
            higher_lower_bound = _numeric(by_run[higher_run_id].get("objective"))
            if higher_lower_bound is None:
                continue
            for lower_run_id in run_ids:
                if lower_run_id == higher_run_id:
                    continue
                lower_upper_bound = _numeric(by_run[lower_run_id].get("best_bound"))
                if lower_upper_bound is None or higher_lower_bound <= lower_upper_bound:
                    continue
                proven.append({
                    "higher_run_id": higher_run_id,
                    "lower_run_id": lower_run_id,
                    "higher_lower_bound": higher_lower_bound,
                    "lower_upper_bound": lower_upper_bound,
                })

    return {
        "objective_definition_comparable": objective_check.comparable,
        "objective_comparability_reasons": list(objective_check.reasons),
        "objective_direction": "maximize",
        "bound_semantics": {
            "objective": "feasible_lower_bound",
            "best_bound": "dual_upper_bound",
            "gap": "relative_ratio",
        },
        "by_run": by_run,
        "proven_strict_orderings": proven,
        "physical_minimum_shortfall": {
            "available": False,
            "reason": "independent verification result is not stored in ordinary Metrics",
        },
    }


def _numeric_delta_tree(value: Any, baseline: Any) -> Any:
    current_number = _numeric(value)
    baseline_number = _numeric(baseline)
    if current_number is not None and baseline_number is not None:
        return current_number - baseline_number
    if isinstance(value, Mapping) and isinstance(baseline, Mapping):
        return {
            key: _numeric_delta_tree(value.get(key), baseline.get(key))
            for key in sorted(set(value) | set(baseline))
        }
    if isinstance(value, list) and isinstance(baseline, list):
        if len(value) != len(baseline):
            return None
        return [
            _numeric_delta_tree(item, base)
            for item, base in zip(value, baseline)
        ]
    return None


def _task_projection(metrics: Mapping[str, Any], mission_id: str) -> Dict[str, Any]:
    row = (metrics.get("tasks") or {}).get(mission_id)
    if not isinstance(row, Mapping):
        raise ComparisonError(f"task is absent from Metrics: {mission_id}")
    sources = {
        "required": row.get("required_by_aircraft") or {},
        "scheduled": row.get("scheduled_by_aircraft") or {},
        "fulfilled": row.get("fulfilled_by_aircraft") or {},
        "unmet": row.get("unmet_by_aircraft") or {},
        "additional": row.get("additional_by_aircraft") or {},
    }
    aircraft_types = sorted({
        str(aircraft_type)
        for source in sources.values()
        for aircraft_type in source
    })
    return {
        "mission_id": mission_id,
        "required_total": row.get("required_total"),
        "scheduled_total": row.get("scheduled_total"),
        "fulfilled_total": row.get("fulfilled_total"),
        "unmet_total": row.get("unmet_total"),
        "additional_total": row.get("additional_total"),
        "completion_ratio": row.get("completion_ratio"),
        "by_aircraft": {
            aircraft_type: {
                field: source.get(aircraft_type, 0)
                for field, source in sources.items()
            }
            for aircraft_type in aircraft_types
        },
        "by_origin_airport": dict(row.get("by_origin_airport") or {}),
        "departures_timeline": list(row.get("departures_timeline") or []),
        "returns_timeline": list(row.get("returns_timeline") or []),
    }


def _airport_projection(metrics: Mapping[str, Any], airport_id: str) -> Dict[str, Any]:
    row = (metrics.get("airports") or {}).get(airport_id)
    if not isinstance(row, Mapping):
        raise ComparisonError(f"airport is absent from Metrics: {airport_id}")
    capacity = row.get("capacity") or {}
    returned_total = _numeric((metrics.get("summary") or {}).get("returned_sorties_total"))
    airport_returns = _numeric(row.get("returns_total"))
    return {
        "airport_id": airport_id,
        "departures_total": row.get("departures_total"),
        "returns_total": row.get("returns_total"),
        "departure_share": row.get("departure_share"),
        "return_share": (
            airport_returns / returned_total
            if airport_returns is not None and returned_total not in (None, 0.0)
            else None
        ),
        "is_selected_cluster": row.get("is_selected_cluster"),
        "is_core": row.get("is_core"),
        "is_participating": row.get("is_participating"),
        "departures_timeline": list(row.get("departures_timeline") or []),
        "returns_timeline": list(row.get("returns_timeline") or []),
        "capacity": {
            field: list(capacity.get(field) or [])
            for field in (
                "available", "used_departure", "used_arrival", "used_total", "utilization"
            )
        },
    }


def _resource_projection(
    metrics: Mapping[str, Any], airport_id: str, resource_type_id: str
) -> Dict[str, Any]:
    resources = metrics.get("resources") or {}
    row = ((resources.get("by_airport") or {}).get(airport_id) or {}).get(resource_type_id)
    if not isinstance(row, Mapping):
        raise ComparisonError(
            f"resource is absent from Metrics: {airport_id}/{resource_type_id}"
        )
    initial = _numeric(row.get("initial"))
    boundary = list(row.get("damage_adjusted_base_boundary") or [])
    damage_adjusted_loss = [
        max(0.0, initial - float(value)) if initial is not None else None
        for value in boundary
    ]
    permanent_loss = (
        list(row.get("permanent_loss") or [])
        if "permanent_loss" in row
        else None
    )
    metadata = (resources.get("resource_types") or {}).get(resource_type_id) or {}
    return {
        "airport_id": airport_id,
        "resource_type_id": resource_type_id,
        "metadata": dict(metadata),
        "initial": row.get("initial"),
        "damage_adjusted_loss": damage_adjusted_loss,
        "permanent_loss": permanent_loss,
        "permanent_loss_recorded": "permanent_loss" in row,
        **{
            field: list(row.get(field) or [])
            for field in (
                "replenishment_actual",
                "replenishment_cumulative",
                "damage_adjusted_base_boundary",
                "available_before_consumption",
                "consumed_increment",
                "consumed_cumulative",
                "remaining",
                "remaining_ratio_initial",
            )
        },
    }


def _aircraft_projection(
    metrics: Mapping[str, Any], airport_id: str, aircraft_type_id: str
) -> Dict[str, Any]:
    inventory = metrics.get("aircraft_inventory") or {}
    row = ((inventory.get("by_airport") or {}).get(airport_id) or {}).get(aircraft_type_id)
    if not isinstance(row, Mapping):
        raise ComparisonError(
            f"aircraft inventory is absent from Metrics: {airport_id}/{aircraft_type_id}"
        )
    return {
        "airport_id": airport_id,
        "aircraft_type_id": aircraft_type_id,
        "baseline_initial_quantity": row.get("baseline_initial_quantity"),
        **{
            field: list(row.get(field) or [])
            for field in (
                "available_before_departure",
                "departures",
                "ready_releases",
                "available_after_departure",
                "in_use",
                "available_ratio_initial",
            )
        },
    }


def _collaboration_projection(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    row = metrics.get("collaboration") or {}
    return {
        "selected_cluster": list(row.get("selected_cluster") or []),
        "core_airports": list(row.get("core_airports") or []),
        "participating_airports": list(row.get("participating_airports") or []),
        "origin_airports": list(row.get("origin_airports") or []),
        "return_airports": list(row.get("return_airports") or []),
        "cross_return_sorties": row.get("cross_return_sorties"),
        "cross_return_ratio": row.get("cross_return_ratio"),
        "departure_hhi": row.get("departure_hhi"),
    }


_CHAIN_FIELDS = (
    "origin_airport_id",
    "mission_id",
    "return_airport_id",
    "aircraft_type",
    "depart_window",
    "return_window",
    "ready_window",
)


def _chain_quantities(
    solution: Mapping[str, Any],
    *,
    mission_id: Optional[str] = None,
    airport_id: Optional[str] = None,
    aircraft_type_id: Optional[str] = None,
) -> Dict[Tuple[Any, ...], float]:
    quantities: Dict[Tuple[Any, ...], float] = {}
    for row in solution.get("sortie_chains") or []:
        if not isinstance(row, Mapping):
            raise ComparisonError("Solution sortie_chains must contain objects")
        if mission_id is not None and row.get("mission_id") != mission_id:
            continue
        if aircraft_type_id is not None and row.get("aircraft_type") != aircraft_type_id:
            continue
        if airport_id is not None and airport_id not in {
            row.get("origin_airport_id"), row.get("return_airport_id")
        }:
            continue
        key = tuple(row.get(field) for field in _CHAIN_FIELDS)
        value = _numeric(row.get("sorties"))
        if value is None or value < 0:
            raise ComparisonError("Solution chain sorties must be nonnegative numeric values")
        quantities[key] = quantities.get(key, 0.0) + value
    return quantities


def build_object_comparison(
    runs: Sequence[Tuple[RunSnapshot, Mapping[str, Any], Mapping[str, Any]]],
    *,
    comparison_type: str,
    baseline_run_id: str,
    object_type: str,
    object_id: Optional[str] = None,
    airport_id: Optional[str] = None,
    resource_type_id: Optional[str] = None,
    aircraft_type_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return one selected object's absolute facts and deltas across Runs."""

    rows = list(runs)
    if not 2 <= len(rows) <= MAX_ANALYSIS_RUNS:
        raise ComparisonError(
            f"object comparison requires 2 to {MAX_ANALYSIS_RUNS} Runs"
        )
    run_ids = [snapshot.run_id for snapshot, _solution, _metrics in rows]
    if len(set(run_ids)) != len(run_ids):
        raise ComparisonError("object comparison requires distinct Run IDs")
    if baseline_run_id not in run_ids:
        raise ComparisonError("baseline_run_id must be one of run_ids")
    if comparison_type not in {"damage", "configuration", "exploratory"}:
        raise ComparisonError("comparison_type must be damage, configuration or exploratory")
    if object_type not in {"task", "airport", "resource", "aircraft", "collaboration"}:
        raise ComparisonError("unsupported object_type")

    by_id = {
        snapshot.run_id: (snapshot, solution, metrics)
        for snapshot, solution, metrics in rows
    }
    baseline_snapshot = by_id[baseline_run_id][0]
    comparability: Dict[str, Any] = {}
    for snapshot, solution, metrics in rows:
        _ensure_metrics(snapshot, metrics)
        if solution.get("run_id") != snapshot.run_id:
            raise ComparisonError(f"Solution run_id does not match snapshot: {snapshot.run_id}")
        if comparison_type == "damage":
            check = check_multi_scenario_comparable(baseline_snapshot, snapshot)
        elif comparison_type == "configuration":
            check = check_configuration_comparable(baseline_snapshot, snapshot)
        else:
            check = check_exploratory_comparable(baseline_snapshot, snapshot)
        if comparison_type != "exploratory":
            check.require()
        strict_modes = []
        if check_multi_scenario_comparable(baseline_snapshot, snapshot).comparable:
            strict_modes.append("damage")
        if check_configuration_comparable(baseline_snapshot, snapshot).comparable:
            strict_modes.append("configuration")
        comparability[snapshot.run_id] = {
            "strict_comparable": bool(strict_modes),
            "strict_modes": strict_modes,
            "reasons": list(check.reasons),
            "differences": list(check.differences),
        }

    if comparison_type != "exploratory":
        _require_common_metrics_contract([
            (snapshot, metrics) for snapshot, _solution, metrics in rows
        ])

    def project(metrics: Mapping[str, Any]) -> Dict[str, Any]:
        if object_type == "task":
            if not object_id:
                raise ComparisonError("task object_id is required")
            return _task_projection(metrics, object_id)
        if object_type == "airport":
            if not object_id:
                raise ComparisonError("airport object_id is required")
            return _airport_projection(metrics, object_id)
        if object_type == "resource":
            if not airport_id or not resource_type_id:
                raise ComparisonError("resource airport_id and resource_type_id are required")
            return _resource_projection(metrics, airport_id, resource_type_id)
        if object_type == "aircraft":
            if not airport_id or not aircraft_type_id:
                raise ComparisonError("aircraft airport_id and aircraft_type_id are required")
            return _aircraft_projection(metrics, airport_id, aircraft_type_id)
        return _collaboration_projection(metrics)

    absolute = {
        snapshot.run_id: project(metrics)
        for snapshot, _solution, metrics in rows
    }
    baseline = absolute[baseline_run_id]
    objective_check = check_objective_comparable(
        *(snapshot for snapshot, _solution, _metrics in rows)
    )
    out = {
        "schema_version": "comparison-object.v1",
        "comparison_type": comparison_type,
        "descriptive_only": comparison_type == "exploratory",
        "baseline_run_id": baseline_run_id,
        "run_ids": run_ids,
        "comparability": comparability,
        "time_axes": {
            snapshot.run_id: dict(metrics.get("time_axis") or {})
            for snapshot, _solution, metrics in rows
        },
        "labels_by_run": {
            snapshot.run_id: _frozen_labels(snapshot)
            for snapshot, _solution, _metrics in rows
        },
        "object": {
            "type": object_type,
            "object_id": object_id,
            "airport_id": airport_id,
            "resource_type_id": resource_type_id,
            "aircraft_type_id": aircraft_type_id,
        },
        "baseline": {"run_id": baseline_run_id, "absolute": baseline},
        "runs": {
            run_id: {
                "absolute": value,
                "delta_vs_baseline": _numeric_delta_tree(value, baseline),
            }
            for run_id, value in absolute.items()
        },
        "solver_comparison": _solver_comparison(
            [(snapshot, metrics) for snapshot, _solution, metrics in rows],
            baseline_run_id=baseline_run_id,
            objective_check=objective_check,
        ),
    }

    if object_type == "collaboration":
        quantities = {
            snapshot.run_id: _chain_quantities(
                solution,
                mission_id=object_id,
                airport_id=airport_id,
                aircraft_type_id=aircraft_type_id,
            )
            for snapshot, solution, _metrics in rows
        }
        keys = sorted({key for by_run in quantities.values() for key in by_run})
        chain_changes = []
        for key in keys:
            chain = dict(zip(_CHAIN_FIELDS, key))
            chain["cross_airport_return"] = (
                chain["origin_airport_id"] != chain["return_airport_id"]
            )
            baseline_value = quantities[baseline_run_id].get(key, 0.0)
            chain_changes.append({
                "chain": chain,
                "by_run": {
                    run_id: {
                        "sorties": by_run.get(key, 0.0),
                        "delta_vs_baseline": by_run.get(key, 0.0) - baseline_value,
                    }
                    for run_id, by_run in quantities.items()
                },
            })
        out["chain_changes"] = chain_changes
    return out


def _extrema(values: Mapping[str, Any], *, low_name: str, high_name: str) -> Dict[str, Any]:
    numeric = [(rid, _numeric(value)) for rid, value in values.items()]
    numeric = [(rid, value) for rid, value in numeric if value is not None]
    if not numeric:
        return {low_name: None, high_name: None}
    low = min(value for _rid, value in numeric)
    high = max(value for _rid, value in numeric)
    return {
        low_name: {"value": low, "run_ids": [rid for rid, value in numeric if value == low]},
        high_name: {"value": high, "run_ids": [rid for rid, value in numeric if value == high]},
    }


def _full_object_rows(
    rows: Sequence[Tuple[RunSnapshot, Mapping[str, Any]]],
    *,
    block_name: str,
    value_fields: Sequence[str],
) -> Dict[str, Any]:
    ids = sorted({
        str(object_id)
        for _snapshot, metrics in rows
        for object_id in ((metrics.get(block_name) or {}).keys())
    })
    out: Dict[str, Any] = {}
    for object_id in ids:
        by_run: Dict[str, Any] = {}
        for snapshot, metrics in rows:
            source = ((metrics.get(block_name) or {}).get(object_id) or {})
            by_run[snapshot.run_id] = {field: source.get(field) for field in value_fields}
        out[object_id] = by_run
    return out


def _timeline_object_rows(
    rows: Sequence[Tuple[RunSnapshot, Mapping[str, Any]]],
    *,
    axis_name: str,
) -> Dict[str, Any]:
    object_ids = sorted({
        str(object_id)
        for _snapshot, metrics in rows
        for object_id in (((metrics.get("timeline") or {}).get(axis_name) or {}).keys())
    })
    output: Dict[str, Any] = {}
    for object_id in object_ids:
        by_run: Dict[str, Any] = {}
        for snapshot, metrics in rows:
            row = (((metrics.get("timeline") or {}).get(axis_name) or {}).get(object_id) or {})
            by_run[snapshot.run_id] = {
                "departures": list(row.get("departures") or []),
                "returns": list(row.get("returns") or []),
            }
        output[object_id] = by_run
    return output


def build_multi_scenario_comparison(
    runs: Sequence[Tuple[RunSnapshot, Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Build deterministic facts for the 2–6 Run multi-scenario workspace.

    The function does not rank a scenario as better/worse.  `difference_overview` only
    reports mathematical extrema explicitly allowed by the UI contract.
    """
    rows = list(runs)
    if not 2 <= len(rows) <= 6:
        raise ComparisonError("multi-scenario comparison requires 2 to 6 Runs")
    run_ids = [snapshot.run_id for snapshot, _metrics in rows]
    if len(set(run_ids)) != len(run_ids):
        raise ComparisonError("multi-scenario comparison requires distinct Run IDs")

    base_snapshot = rows[0][0]
    for snapshot, _metrics in rows[1:]:
        check_multi_scenario_comparable(base_snapshot, snapshot).require()
    slot_minutes, windows = _require_common_metrics_contract(rows)

    summaries = {snapshot.run_id: _comparison_summary(metrics) for snapshot, metrics in rows}
    run_summaries = {
        snapshot.run_id: _run_summary_projection(metrics)
        for snapshot, metrics in rows
    }
    objective_check = check_objective_comparable(*(snapshot for snapshot, _metrics in rows))
    configurations = {
        snapshot.run_id: {
            "damage_scenario_id": (_config(snapshot.to_dict())).get("damage_scenario_id"),
            "run_config": _config(snapshot.to_dict()),
        }
        for snapshot, _metrics in rows
    }

    timeline = {
        "windows": windows,
        "slot_minutes": slot_minutes,
        "departures": {
            snapshot.run_id: list((metrics.get("timeline") or {}).get("departures_total") or [])
            for snapshot, metrics in rows
        },
        "returns": {
            snapshot.run_id: list((metrics.get("timeline") or {}).get("returns_total") or [])
            for snapshot, metrics in rows
        },
        "by_airport": _timeline_object_rows(rows, axis_name="by_airport"),
        "by_mission": _timeline_object_rows(rows, axis_name="by_mission"),
        "by_aircraft": _timeline_object_rows(rows, axis_name="by_aircraft"),
    }

    resources: Dict[str, Any] = {}
    for category in ("fuel", "material", "munition"):
        resources[category] = {
            snapshot.run_id: (((metrics.get("resources") or {}).get("category_min_remaining_ratio") or {}).get(category))
            for snapshot, metrics in rows
        }

    scheme = {
        snapshot.run_id: {
            "selected_cluster": list((metrics.get("collaboration") or {}).get("selected_cluster") or []),
            "participating_airports": list((metrics.get("collaboration") or {}).get("participating_airports") or []),
            "departure_hhi": (metrics.get("collaboration") or {}).get("departure_hhi"),
            "cross_return_ratio": (metrics.get("collaboration") or {}).get("cross_return_ratio"),
        }
        for snapshot, metrics in rows
    }

    peak_values = {rid: row.get("peak_sorties") for rid, row in summaries.items()}
    peak_windows = {rid: row.get("peak_window") for rid, row in summaries.items()}
    max_shares = {
        rid: ((row.get("max_airport_departure") or {}).get("share"))
        for rid, row in summaries.items()
    }
    min_resources = {
        rid: ((row.get("minimum_resource_remaining") or {}).get("ratio"))
        for rid, row in summaries.items()
    }
    participant_counts = {rid: row.get("participating_airport_count") for rid, row in summaries.items()}
    fulfilled_totals = {rid: row.get("fulfilled_sorties_total") for rid, row in summaries.items()}
    unmet_totals = {rid: row.get("unmet_sorties_total") for rid, row in summaries.items()}
    additional_totals = {rid: row.get("additional_sorties_total") for rid, row in summaries.items()}
    scheduled_totals = {rid: row.get("scheduled_sorties_total") for rid, row in summaries.items()}
    completion_ratios = {rid: row.get("completion_ratio") for rid, row in summaries.items()}

    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "mode": "multi_scenario",
        "run_ids": run_ids,
        "configurations": configurations,
        "objective_comparable": objective_check.comparable,
        "objective_comparability_reasons": list(objective_check.reasons),
        "solver_comparison": _solver_comparison(
            rows,
            baseline_run_id=rows[0][0].run_id,
            objective_check=objective_check,
        ),
        "run_summaries": run_summaries,
        "labels": _frozen_labels(rows[0][0]),
        "summary": summaries,
        "timeline": timeline,
        "difference_overview": {
            "fulfilled_sorties_total": _extrema(fulfilled_totals, low_name="lowest", high_name="highest"),
            "unmet_sorties_total": _extrema(unmet_totals, low_name="lowest", high_name="highest"),
            "additional_sorties_total": _extrema(additional_totals, low_name="lowest", high_name="highest"),
            "scheduled_sorties_total": _extrema(scheduled_totals, low_name="lowest", high_name="highest"),
            "completion_ratio": _extrema(completion_ratios, low_name="lowest", high_name="highest"),
            "peak_sorties": _extrema(peak_values, low_name="lowest", high_name="highest"),
            "peak_window": _extrema(peak_windows, low_name="earliest", high_name="latest"),
            "max_airport_departure_share": _extrema(max_shares, low_name="lowest", high_name="highest"),
            "minimum_resource_remaining_ratio": _extrema(min_resources, low_name="lowest", high_name="highest"),
            "participating_airport_count": _extrema(participant_counts, low_name="lowest", high_name="highest"),
        },
        "airports": _full_object_rows(rows, block_name="airports", value_fields=("departures_total", "departure_share")),
        "tasks": _full_object_rows(
            rows,
            block_name="tasks",
            value_fields=("required_total", "scheduled_total"),
        ),
        "aircraft": _full_object_rows(rows, block_name="aircraft", value_fields=("scheduled_total", "scheduled_share")),
        "resources": {"category_min_remaining_ratio": resources},
        "scheme": scheme,
    }


def _delta_from_baseline(value: Any, baseline: Any) -> Optional[float]:
    a = _numeric(value)
    b = _numeric(baseline)
    if a is None or b is None:
        return None
    return a - b


def build_configuration_comparison(
    runs: Sequence[Tuple[RunSnapshot, Mapping[str, Any]]],
    *,
    baseline_run_id: str,
) -> Dict[str, Any]:
    """Build 2–5 Run configuration comparison facts relative to one real baseline Run."""
    rows = list(runs)
    if not 2 <= len(rows) <= 5:
        raise ComparisonError("configuration comparison requires 2 to 5 Runs")
    run_ids = [snapshot.run_id for snapshot, _metrics in rows]
    if len(set(run_ids)) != len(run_ids):
        raise ComparisonError("configuration comparison requires distinct Run IDs")
    if baseline_run_id not in run_ids:
        raise ComparisonError("baseline_run_id must be one of run_ids")

    by_id = {snapshot.run_id: (snapshot, metrics) for snapshot, metrics in rows}
    baseline_snapshot, baseline_metrics = by_id[baseline_run_id]
    for snapshot, _metrics in rows:
        if snapshot.run_id != baseline_run_id:
            check_configuration_comparable(baseline_snapshot, snapshot).require()
    slot_minutes, windows = _require_common_metrics_contract(rows)

    summaries = {snapshot.run_id: _comparison_summary(metrics) for snapshot, metrics in rows}
    run_summaries = {
        snapshot.run_id: _run_summary_projection(metrics)
        for snapshot, metrics in rows
    }
    objective_check = check_objective_comparable(*(snapshot for snapshot, _metrics in rows))
    baseline_summary = summaries[baseline_run_id]
    summary_deltas: Dict[str, Any] = {}
    for rid, summary in summaries.items():
        min_resource = (summary.get("minimum_resource_remaining") or {}).get("ratio")
        base_resource = (baseline_summary.get("minimum_resource_remaining") or {}).get("ratio")
        share = (summary.get("max_airport_departure") or {}).get("share")
        base_share = (baseline_summary.get("max_airport_departure") or {}).get("share")
        slot_delta = _delta_from_baseline(summary.get("peak_window"), baseline_summary.get("peak_window"))
        summary_deltas[rid] = {
            "required_sorties_total_delta": _delta_from_baseline(
                summary.get("required_sorties_total"), baseline_summary.get("required_sorties_total")
            ),
            "fulfilled_sorties_total_delta": _delta_from_baseline(
                summary.get("fulfilled_sorties_total"), baseline_summary.get("fulfilled_sorties_total")
            ),
            "unmet_sorties_total_delta": _delta_from_baseline(
                summary.get("unmet_sorties_total"), baseline_summary.get("unmet_sorties_total")
            ),
            "additional_sorties_total_delta": _delta_from_baseline(
                summary.get("additional_sorties_total"), baseline_summary.get("additional_sorties_total")
            ),
            "scheduled_sorties_total_delta": _delta_from_baseline(
                summary.get("scheduled_sorties_total"), baseline_summary.get("scheduled_sorties_total")
            ),
            "completion_ratio_delta": _delta_from_baseline(
                summary.get("completion_ratio"), baseline_summary.get("completion_ratio")
            ),
            "peak_window_delta_slots": slot_delta,
            "peak_time_delta_minutes": None if slot_delta is None else slot_delta * slot_minutes,
            "peak_sorties_delta": _delta_from_baseline(summary.get("peak_sorties"), baseline_summary.get("peak_sorties")),
            "max_airport_departure_share_delta": _delta_from_baseline(share, base_share),
            "minimum_resource_remaining_ratio_delta": _delta_from_baseline(min_resource, base_resource),
            "participating_airport_count_delta": _delta_from_baseline(
                summary.get("participating_airport_count"), baseline_summary.get("participating_airport_count")
            ),
            "departure_hhi_delta": _delta_from_baseline(summary.get("departure_hhi"), baseline_summary.get("departure_hhi")),
            "cross_return_ratio_delta": _delta_from_baseline(summary.get("cross_return_ratio"), baseline_summary.get("cross_return_ratio")),
        }

    baseline_dep = list((baseline_metrics.get("timeline") or {}).get("departures_total") or [])
    baseline_ret = list((baseline_metrics.get("timeline") or {}).get("returns_total") or [])
    timeline = {
        "windows": windows,
        "slot_minutes": slot_minutes,
        "departures": {},
        "returns": {},
    }
    for snapshot, metrics in rows:
        rid = snapshot.run_id
        dep = list((metrics.get("timeline") or {}).get("departures_total") or [])
        ret = list((metrics.get("timeline") or {}).get("returns_total") or [])
        timeline["departures"][rid] = {
            "values": dep,
            "delta_vs_baseline": _delta(baseline_dep, dep),
        }
        timeline["returns"][rid] = {
            "values": ret,
            "delta_vs_baseline": _delta(baseline_ret, ret),
        }

    timeline["by_airport"] = _timeline_object_rows(rows, axis_name="by_airport")
    timeline["by_mission"] = _timeline_object_rows(rows, axis_name="by_mission")
    timeline["by_aircraft"] = _timeline_object_rows(rows, axis_name="by_aircraft")

    airport_ids = sorted({aid for _s, m in rows for aid in (m.get("airports") or {})})
    airports: Dict[str, Any] = {}
    for aid in airport_ids:
        base_row = ((baseline_metrics.get("airports") or {}).get(aid) or {})
        out = {}
        for snapshot, metrics in rows:
            row = ((metrics.get("airports") or {}).get(aid) or {})
            out[snapshot.run_id] = {
                "departures_total": row.get("departures_total"),
                "departure_share": row.get("departure_share"),
                "departures_total_delta": _delta_from_baseline(row.get("departures_total"), base_row.get("departures_total")),
                "departure_share_delta": _delta_from_baseline(row.get("departure_share"), base_row.get("departure_share")),
            }
        airports[aid] = out

    def object_with_delta(block_name: str, value_field: str) -> Dict[str, Any]:
        ids = sorted({oid for _s, m in rows for oid in (m.get(block_name) or {})})
        output: Dict[str, Any] = {}
        base_block = baseline_metrics.get(block_name) or {}
        for oid in ids:
            base_value = ((base_block.get(oid) or {}).get(value_field))
            output[oid] = {
                snapshot.run_id: {
                    "value": ((metrics.get(block_name) or {}).get(oid) or {}).get(value_field),
                    "delta_vs_baseline": _delta_from_baseline(
                        ((metrics.get(block_name) or {}).get(oid) or {}).get(value_field),
                        base_value,
                    ),
                }
                for snapshot, metrics in rows
            }
        return output

    def task_rows_with_deltas() -> Dict[str, Any]:
        fields = ("required_total", "scheduled_total")
        ids = sorted({oid for _snapshot, metrics in rows for oid in (metrics.get("tasks") or {})})
        output: Dict[str, Any] = {}
        base_block = baseline_metrics.get("tasks") or {}
        for oid in ids:
            base_row = base_block.get(oid) or {}
            output[oid] = {}
            for snapshot, metrics in rows:
                source = ((metrics.get("tasks") or {}).get(oid) or {})
                row = {
                    field: source.get(field)
                    for field in fields
                }
                row.update({
                    f"{field}_delta_vs_baseline": _delta_from_baseline(
                        source.get(field), base_row.get(field)
                    )
                    for field in fields
                })
                # Preserve the established scheduled-total aliases for API consumers.
                row["value"] = row["scheduled_total"]
                row["delta_vs_baseline"] = row["scheduled_total_delta_vs_baseline"]
                output[oid][snapshot.run_id] = row
        return output

    base_resource = ((baseline_metrics.get("resources") or {}).get("category_min_remaining_ratio") or {})
    resource_rows: Dict[str, Any] = {}
    for category in ("fuel", "material", "munition"):
        base_detail = base_resource.get(category)
        base_ratio = None if not isinstance(base_detail, Mapping) else base_detail.get("ratio")
        by_run: Dict[str, Any] = {}
        for snapshot, metrics in rows:
            detail = (((metrics.get("resources") or {}).get("category_min_remaining_ratio") or {}).get(category))
            ratio = None if not isinstance(detail, Mapping) else detail.get("ratio")
            by_run[snapshot.run_id] = {
                "detail": detail,
                "ratio_delta_vs_baseline": _delta_from_baseline(ratio, base_ratio),
            }
        resource_rows[category] = by_run

    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "mode": "configuration",
        "baseline_run_id": baseline_run_id,
        "run_ids": run_ids,
        "configurations": {
            snapshot.run_id: _config(snapshot.to_dict()) for snapshot, _metrics in rows
        },
        "objective_comparable": objective_check.comparable,
        "objective_comparability_reasons": list(objective_check.reasons),
        "solver_comparison": _solver_comparison(
            rows,
            baseline_run_id=baseline_run_id,
            objective_check=objective_check,
        ),
        "run_summaries": run_summaries,
        "labels": _frozen_labels(rows[0][0]),
        "summary": summaries,
        "summary_deltas_vs_baseline": summary_deltas,
        "timeline": timeline,
        "airports": airports,
        "tasks": task_rows_with_deltas(),
        "aircraft": object_with_delta("aircraft", "scheduled_total"),
        "resources": {"category_min_remaining_ratio": resource_rows},
        "scheme": {
            snapshot.run_id: {
                "selected_cluster": list((metrics.get("collaboration") or {}).get("selected_cluster") or []),
                "participating_airports": list((metrics.get("collaboration") or {}).get("participating_airports") or []),
                "departure_hhi": (metrics.get("collaboration") or {}).get("departure_hhi"),
                "cross_return_ratio": (metrics.get("collaboration") or {}).get("cross_return_ratio"),
            }
            for snapshot, metrics in rows
        },
    }


def build_exploratory_comparison(
    runs: Sequence[Tuple[RunSnapshot, Mapping[str, Any]]],
    *,
    baseline_run_id: str,
) -> Dict[str, Any]:
    """Build a summary-only cross-condition comparison.

    Different horizons, demand levels and configurations are accepted deliberately.
    The response labels every difference and leaves object timelines to the dedicated
    object query instead of returning every Run's full arrays here.
    """

    rows = list(runs)
    if not 2 <= len(rows) <= MAX_ANALYSIS_RUNS:
        raise ComparisonError(
            f"exploratory comparison requires 2 to {MAX_ANALYSIS_RUNS} Runs"
        )
    run_ids = [snapshot.run_id for snapshot, _metrics in rows]
    if len(set(run_ids)) != len(run_ids):
        raise ComparisonError("exploratory comparison requires distinct Run IDs")
    if baseline_run_id not in run_ids:
        raise ComparisonError("baseline_run_id must be one of run_ids")
    for snapshot, metrics in rows:
        _ensure_metrics(snapshot, metrics)

    by_id = {snapshot.run_id: (snapshot, metrics) for snapshot, metrics in rows}
    baseline_snapshot, baseline_metrics = by_id[baseline_run_id]
    summaries = {
        snapshot.run_id: _comparison_summary(metrics)
        for snapshot, metrics in rows
    }
    baseline_summary = summaries[baseline_run_id]
    scalar_fields = (
        "required_sorties_total",
        "fulfilled_sorties_total",
        "unmet_sorties_total",
        "additional_sorties_total",
        "scheduled_sorties_total",
        "completion_ratio",
        "participating_airport_count",
        "selected_cluster_count",
        "departure_hhi",
        "cross_return_ratio",
    )
    descriptive_deltas = {
        run_id: {
            f"{field}_delta_vs_baseline": _delta_from_baseline(
                summary.get(field), baseline_summary.get(field)
            )
            for field in scalar_fields
        }
        for run_id, summary in summaries.items()
    }
    comparability: Dict[str, Any] = {}
    for snapshot, _metrics in rows:
        exploratory = check_exploratory_comparable(baseline_snapshot, snapshot)
        damage = check_multi_scenario_comparable(baseline_snapshot, snapshot)
        configuration = check_configuration_comparable(baseline_snapshot, snapshot)
        strict_modes = []
        if damage.comparable:
            strict_modes.append("damage")
        if configuration.comparable:
            strict_modes.append("configuration")
        comparability[snapshot.run_id] = {
            "strict_comparable": bool(strict_modes),
            "strict_modes": strict_modes,
            "differences": list(exploratory.differences),
        }

    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "mode": "exploratory",
        "descriptive_only": True,
        "baseline_run_id": baseline_run_id,
        "run_ids": run_ids,
        "comparability": comparability,
        "configurations": {
            snapshot.run_id: _config(snapshot.to_dict())
            for snapshot, _metrics in rows
        },
        "time_axes": {
            snapshot.run_id: dict(metrics.get("time_axis") or {})
            for snapshot, metrics in rows
        },
        "labels_by_run": {
            snapshot.run_id: _frozen_labels(snapshot)
            for snapshot, _metrics in rows
        },
        "run_summaries": {
            snapshot.run_id: _run_summary_projection(metrics)
            for snapshot, metrics in rows
        },
        "summary": summaries,
        "descriptive_deltas_vs_baseline": descriptive_deltas,
        "objective_comparable": check_objective_comparable(
            *(snapshot for snapshot, _metrics in rows)
        ).comparable,
        "solver_comparison": _solver_comparison(
            rows,
            baseline_run_id=baseline_run_id,
            objective_check=check_objective_comparable(
                *(snapshot for snapshot, _metrics in rows)
            ),
        ),
        "scheme": {
            snapshot.run_id: {
                "selected_cluster": list(
                    (metrics.get("collaboration") or {}).get("selected_cluster") or []
                ),
                "participating_airports": list(
                    (metrics.get("collaboration") or {}).get("participating_airports") or []
                ),
                "departure_hhi": (metrics.get("collaboration") or {}).get(
                    "departure_hhi"
                ),
                "cross_return_ratio": (metrics.get("collaboration") or {}).get(
                    "cross_return_ratio"
                ),
            }
            for snapshot, metrics in rows
        },
        "object_query": {
            "required_for_timelines": True,
            "supported_types": [
                "task", "airport", "resource", "aircraft", "collaboration"
            ],
        },
    }


__all__ = [
    "COMPARISON_SCHEMA_VERSION",
    "MAX_ANALYSIS_RUNS",
    "ComparisonError",
    "ComparabilityCheck",
    "check_multi_scenario_comparable",
    "check_configuration_comparable",
    "check_exploratory_comparable",
    "check_objective_comparable",
    "check_r0_r1_r2",
    "build_r0_r1_r2_comparison",
    "build_multi_scenario_comparison",
    "build_configuration_comparison",
    "build_exploratory_comparison",
    "build_object_comparison",
]
