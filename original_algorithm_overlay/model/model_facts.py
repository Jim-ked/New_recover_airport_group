# -*- coding: utf-8 -*-
"""Shared coefficient/state facts for the existing optimization model.

This module is a refactor aid, not another solver.  It centralizes facts that were
previously recomputed independently in ``model_builder`` and ``cluster_selector`` so
both stages consume the same path/resource/capacity semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Dict, Mapping, Sequence, Tuple

from .decision_vars import PathKey, PathMaps, SortiePath

DELTA_HOURS = 0.25
class ModelFactError(ValueError):
    pass


@dataclass(frozen=True)
class ObjectiveWeights:
    sortie: float
    resource: float
    time: float


@dataclass(frozen=True)
class PathObjectiveCoefficient:
    path_id: PathKey
    f1: float
    f2: float
    f3: float


@dataclass(frozen=True)
class ResourceUse:
    airport_id: str
    resource_type_id: str
    amount: float


def _mission_requirements(ds: Mapping[str, Any]) -> Dict[Tuple[str, str], int]:
    out: Dict[Tuple[str, str], int] = {}
    for mission in ds["static"]["missions"]:
        mid = mission["mission_id"]
        for f, amount in (mission.get("required_sorties") or {}).items():
            q = int(amount)
            if q > 0:
                out[(mid, f)] = q
    return out


def validate_hard_demand_paths(ds: Mapping[str, Any], maps: PathMaps) -> None:
    """Compatibility entry retained after required_sorties became soft demand.

    A positive baseline demand with no feasible path is no longer a structural model
    error.  The optimizer represents that shortage with an explicit UNMET variable.
    Existing callers may keep invoking this function during migration; it intentionally
    performs no rejection.
    """
    return None


def resolved_alpha(runtime: Mapping[str, Any]) -> ObjectiveWeights:
    """Read already-canonical RunConfig weights, with strict legacy preset support."""
    mode = str(runtime.get("preference_mode") or "")
    presets = {
        "sortie_max": (0.8, 0.1, 0.1),
        "resource_min": (0.1, 0.8, 0.1),
        "time_min": (0.1, 0.1, 0.8),
    }
    if mode in presets:
        a = presets[mode]
    elif mode == "custom":
        raw = runtime.get("alpha")
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise ModelFactError("custom preference requires canonical three-value alpha")
        a = tuple(float(v) for v in raw)
        if any(v <= 0 for v in a) or abs(sum(a) - 1.0) > 1e-8:
            raise ModelFactError("custom alpha must already be positive and normalized")
    else:
        raise ModelFactError(f"unsupported preference_mode: {mode!r}")
    return ObjectiveWeights(*a)


def _type_weights(runtime: Mapping[str, Any], aircraft_types: Sequence[str]) -> Dict[str, float]:
    raw = runtime.get("aircraft_type_weight") or {}
    if not isinstance(raw, dict):
        raise ModelFactError("aircraft_type_weight must be object")
    out = {f: 1.0 for f in aircraft_types}
    for f, value in raw.items():
        if f not in out:
            raise ModelFactError(f"unknown aircraft_type_weight key: {f}")
        v = float(value)
        if v <= 0:
            raise ModelFactError(f"aircraft_type_weight must be positive: {f}")
        out[f] = v
    return out


def _configured_positive_mapping(runtime: Mapping[str, Any], field: str) -> Dict[str, float]:
    raw = runtime.get(field)
    if not isinstance(raw, dict):
        raise ModelFactError(f"{field} is required and must be an object")
    out: Dict[str, float] = {}
    for key, value in raw.items():
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ModelFactError(f"{field}.{key} must be numeric") from exc
        if not isfinite(number) or number <= 0:
            raise ModelFactError(f"{field}.{key} must be positive")
        out[str(key)] = number
    return out


def _configured_number(
    runtime: Mapping[str, Any], field: str, *, positive: bool
) -> float:
    raw = runtime.get(field)
    if raw is None:
        raise ModelFactError(f"{field} is required")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ModelFactError(f"{field} must be numeric") from exc
    invalid = value <= 0 if positive else value < 0
    if not isfinite(value) or invalid:
        qualifier = "positive" if positive else "nonnegative"
        raise ModelFactError(f"{field} must be {qualifier}")
    return value


def path_resource_use(path: SortiePath, run_params: Mapping[str, Any]) -> Tuple[ResourceUse, ...]:
    """Actual airport support-resource demand for one complete sortie.

    Base use covers outbound flight + mission work + return flight for ``per_hour``
    resources.  For resources whose canonical category is ``fuel``, the manually
    confirmed onboard reserve policy is applied to the *demand coefficient*:

        actual_fuel_required = base_mission_fuel / (1 - reserve_ratio)

    The resulting amount is then charged to the airport-local shared resource pool.
    Navigation delay and ``tau_reset`` are not included because no confirmed rule says
    they consume these mission resources.
    """
    cfg = (run_params.get("aircrafts") or {}).get(path.aircraft_type_id)
    if not isinstance(cfg, dict):
        raise ModelFactError(f"missing aircraft configuration: {path.aircraft_type_id}")
    reqs = cfg.get("resource_requirements") or {}
    if not isinstance(reqs, dict):
        raise ModelFactError(f"resource_requirements must be object: {path.aircraft_type_id}")
    resource_types = run_params.get("resource_types") or {}
    if not isinstance(resource_types, dict):
        raise ModelFactError("resource_types must be object")
    try:
        reserve_ratio = float(cfg["reserve_ratio"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelFactError(f"reserve_ratio missing/invalid for {path.aircraft_type_id}") from exc
    if not (0.0 <= reserve_ratio < 1.0):
        raise ModelFactError(f"reserve_ratio must satisfy 0 <= r < 1 for {path.aircraft_type_id}")

    active_hours = (
        path.outbound_flight_slots + path.tau_work_windows + path.return_flight_slots
    ) * DELTA_HOURS
    rows = []
    for rid, spec in sorted(reqs.items()):
        if not isinstance(spec, dict):
            raise ModelFactError(f"invalid resource requirement: {path.aircraft_type_id}/{rid}")
        meta = resource_types.get(rid)
        if not isinstance(meta, dict):
            raise ModelFactError(f"resource type metadata missing: {rid}")
        basis = spec.get("basis")
        qty = float(spec.get("quantity"))
        if qty < 0:
            raise ModelFactError(f"negative resource requirement: {path.aircraft_type_id}/{rid}")
        if basis == "per_sortie":
            amount = qty
        elif basis == "per_hour":
            amount = qty * active_hours
        else:
            raise ModelFactError(f"unsupported resource basis: {basis!r}")
        if meta.get("category") == "fuel" and amount > 0:
            amount = amount / (1.0 - reserve_ratio)
        if amount > 0:
            rows.append(ResourceUse(path.origin_airport_id, rid, float(amount)))
    return tuple(rows)


def resource_use_by_path(maps: PathMaps, run_params: Mapping[str, Any]) -> Dict[PathKey, Tuple[ResourceUse, ...]]:
    return {p.key: path_resource_use(p, run_params) for p in maps.path_records}


def capacity_coefficients(
    maps: PathMaps,
    run_params: Mapping[str, Any],
) -> Tuple[Dict[Tuple[str, int, PathKey], float], Dict[Tuple[str, int, PathKey], float]]:
    dep: Dict[Tuple[str, int, PathKey], float] = {}
    arr: Dict[Tuple[str, int, PathKey], float] = {}
    acfg = run_params.get("aircrafts") or {}
    for p in maps.path_records:
        cfg = acfg.get(p.aircraft_type_id) or {}
        try:
            dep_f = float(cfg["capacity_factor"])
            arr_f = float(cfg["arrival_capacity_factor"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelFactError(f"capacity occupancy factors missing for {p.aircraft_type_id}") from exc
        if dep_f <= 0 or arr_f <= 0:
            raise ModelFactError(f"capacity occupancy factors must be positive for {p.aircraft_type_id}")
        dep[(p.origin_airport_id, p.depart_slot, p.key)] = dep_f
        arr[(p.return_airport_id, p.landing_slot, p.key)] = arr_f
    return dep, arr


def objective_coefficients(
    ds: Mapping[str, Any],
    maps: PathMaps,
    run_params: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> Dict[PathKey, PathObjectiveCoefficient]:
    """Return fixed path coefficients shared verbatim by cluster LP and final MIP.

    F1 is the actual sortie's aircraft-type weight. F2 is a per-resource weighted cost
    against experiment-fixed reference quantities. F3 is normalized absolute mission
    completion time plus an optional tardiness surcharge. No coefficient depends on the
    candidate cluster's path population or on damage-adjusted resource availability.
    """
    types = sorted({p.aircraft_type_id for p in maps.path_records})
    type_w = _type_weights(runtime, types)
    references = _configured_positive_mapping(runtime, "f2_resource_reference_quantities")
    resource_weights = _configured_positive_mapping(runtime, "f2_resource_weights")
    if set(references) != set(resource_weights):
        missing_references = sorted(set(resource_weights) - set(references))
        missing_weights = sorted(set(references) - set(resource_weights))
        raise ModelFactError(
            "F2 reference/weight resource IDs differ; "
            f"missing_references={missing_references}, missing_weights={missing_weights}"
        )
    time_reference = _configured_number(runtime, "f3_time_reference_slots", positive=True)
    tardiness_coefficient = _configured_number(
        runtime, "f3_tardiness_coefficient", positive=False
    )

    range_start = int((ds.get("range") or (0,))[0])
    mission_end: Dict[str, int] = {}
    for mission in ds["static"]["missions"]:
        duty = mission.get("_duty_window")
        if not isinstance(duty, (tuple, list)) or len(duty) != 2:
            raise ModelFactError(f"mission duty window missing/invalid: {mission.get('mission_id')}")
        mission_end[str(mission["mission_id"])] = range_start + int(duty[1])

    base_use = resource_use_by_path(maps, run_params)
    used_resource_ids = {row.resource_type_id for rows in base_use.values() for row in rows}
    missing = sorted(used_resource_ids - set(references))
    if missing:
        raise ModelFactError(f"F2 reference/weight missing for resource: {missing[0]}")

    out: Dict[PathKey, PathObjectiveCoefficient] = {}
    for p in maps.path_records:
        f1 = type_w[p.aircraft_type_id]
        f2 = sum(
            resource_weights[row.resource_type_id]
            * row.amount
            / references[row.resource_type_id]
            for row in base_use[p.key]
        )
        completion = range_start + p.mission_arrival_slot + p.tau_work_windows
        tardiness = max(0, completion - mission_end[p.mission_id])
        f3 = (completion + tardiness_coefficient * tardiness) / time_reference
        out[p.key] = PathObjectiveCoefficient(p.key, f1, f2, f3)
    return out


def demand_rows(ds: Mapping[str, Any], maps: PathMaps) -> Dict[Tuple[str, str], Tuple[int, Tuple[PathKey, ...]]]:
    """Baseline demand rows used by the soft-demand penalty model.

    Empty path tuples are valid.  In that case the full baseline requirement is carried
    by the optimizer's UNMET variable rather than turning model construction into a hard
    infeasibility.
    """
    by_mf: Dict[Tuple[str, str], list[PathKey]] = {}
    for p in maps.path_records:
        by_mf.setdefault((p.mission_id, p.aircraft_type_id), []).append(p.key)
    rows = {}
    for key, required in _mission_requirements(ds).items():
        rows[key] = (required, tuple(sorted(by_mf.get(key, []))))
    return rows


def aircraft_events(maps: PathMaps):
    """Indexes for path-based aircraft flow recurrence."""
    departures: Dict[Tuple[str, str, int], list[PathKey]] = {}
    ready: Dict[Tuple[str, str, int], list[PathKey]] = {}
    for p in maps.path_records:
        departures.setdefault((p.origin_airport_id, p.aircraft_type_id, p.depart_slot), []).append(p.key)
        ready.setdefault((p.return_airport_id, p.aircraft_type_id, p.ready_slot), []).append(p.key)
    return (
        {k: tuple(sorted(v)) for k, v in departures.items()},
        {k: tuple(sorted(v)) for k, v in ready.items()},
    )


__all__ = [
    "ModelFactError",
    "ObjectiveWeights",
    "PathObjectiveCoefficient",
    "ResourceUse",
    "validate_hard_demand_paths",
    "resolved_alpha",
    "path_resource_use",
    "resource_use_by_path",
    "capacity_coefficients",
    "objective_coefficients",
    "demand_rows",
    "aircraft_events",
    "validate_schedule_base",
]


def validate_schedule_base(
    ds: Mapping[str, Any],
    maps: PathMaps,
    run_params: Mapping[str, Any],
    quantities: Mapping[PathKey, float],
    *,
    integer_required: bool = True,
    tolerance: float = 1e-7,
) -> None:
    """Independent physical-invariant check for a candidate path solution.

    Mission ``required_sorties`` is deliberately excluded because it is now a soft
    planning target handled by the optimization objective.  This validator checks only
    physical facts: path identity, capacity, aircraft flow and shared resources.
    """
    path_by_key = {p.key: p for p in maps.path_records}
    unknown = sorted(set(quantities) - set(path_by_key))
    if unknown:
        raise ModelFactError(f"solution references unknown path: {unknown[0]}")
    q: Dict[PathKey, float] = {}
    for key, raw in quantities.items():
        value = float(raw)
        if value < -tolerance:
            raise ModelFactError(f"negative sortie quantity: {key}")
        if integer_required and abs(value - round(value)) > tolerance:
            raise ModelFactError(f"non-integer sortie quantity: {key}")
        if value > tolerance:
            q[key] = value

    # Airport departure+arrival capacity.
    dep_coef, arr_coef = capacity_coefficients(maps, run_params)
    cap = ds["timeview"].get("cap") or {}
    T = int(ds["timeview"]["T"])
    airports = [a["airport_id"] for a in ds["static"]["airports"]]
    for aid in airports:
        series = cap.get(aid)
        if not isinstance(series, list) or len(series) < T:
            raise ModelFactError(f"capacity series missing/short: {aid}")
        for t in range(T):
            used = 0.0
            for (a, slot, path_id), coef in dep_coef.items():
                if a == aid and slot == t:
                    used += coef * q.get(path_id, 0.0)
            for (a, slot, path_id), coef in arr_coef.items():
                if a == aid and slot == t:
                    used += coef * q.get(path_id, 0.0)
            if used > float(series[t]) + tolerance:
                raise ModelFactError(f"capacity violated: airport={aid}, slot={t}, used={used}, limit={series[t]}")

    # Aircraft is a recyclable flow.  Shock at t acts before departures at t; ready at t
    # is available at t.  A declared loss greater than current valid quantity is invalid.
    departures, ready = aircraft_events(maps)
    z0 = ds["timeview"].get("z0") or {}
    shock = ds["timeview"].get("aircraft_shock") or {}
    types = sorted({p.aircraft_type_id for p in maps.path_records})
    for aid in airports:
        for f in types:
            available = float((z0.get(aid) or {}).get(f, 0.0))
            seq = ((shock.get(aid) or {}).get(f) or [0] * T)
            for t in range(T):
                if t > 0:
                    available += sum(q.get(pid, 0.0) for pid in ready.get((aid, f, t), ()))
                delta = float(seq[t]) if t < len(seq) else 0.0
                available += delta
                if available < -tolerance:
                    raise ModelFactError(
                        f"aircraft damage exceeds current valid quantity: airport={aid}, aircraft={f}, slot={t}"
                    )
                dep = sum(q.get(pid, 0.0) for pid in departures.get((aid, f, t), ()))
                if dep > available + tolerance:
                    raise ModelFactError(
                        f"aircraft inventory violated: airport={aid}, aircraft={f}, slot={t}, "
                        f"depart={dep}, available={available}"
                    )
                available -= dep

    # Consumables are airport-local shared pools.  Recovery only changes the external
    # availability boundary; cumulative mission consumption is never reset.
    use = resource_use_by_path(maps, run_params)
    resource_limits = ds["timeview"].get("resources") or {}
    for aid in airports:
        for rid, series in (resource_limits.get(aid) or {}).items():
            if len(series) < T:
                raise ModelFactError(f"resource series missing/short: {aid}/{rid}")
            cumulative = 0.0
            departing: Dict[int, float] = {}
            for p in maps.path_records:
                if p.origin_airport_id != aid:
                    continue
                amount = sum(row.amount for row in use[p.key] if row.resource_type_id == rid)
                if amount:
                    departing[p.depart_slot] = departing.get(p.depart_slot, 0.0) + amount * q.get(p.key, 0.0)
            for t in range(T):
                cumulative += departing.get(t, 0.0)
                if cumulative > float(series[t]) + tolerance:
                    raise ModelFactError(
                        f"shared resource violated: airport={aid}, resource={rid}, slot={t}, "
                        f"used={cumulative}, available={series[t]}"
                    )
