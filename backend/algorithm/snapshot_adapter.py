from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.damage import DamageScenario, NavigationDelayEffect
from backend.domain.run_config import RunConfig
from backend.domain.run_snapshot import RunSnapshot, SNAPSHOT_SCHEMA
from backend.domain.situation import Situation
from backend.services.damage_projection_service import DamageProjection, project_damage
from backend.services.snapshot_materialization import SnapshotMaterializationError, materialize_situation

DELTA_MIN = 15


class AlgorithmInputError(ValueError):
    """Canonical snapshot cannot be represented safely at the algorithm boundary."""


@dataclass(frozen=True)
class AlgorithmInputBundle:
    """Input closure consumed by the existing algorithm main chain.

    `ds`, `run_params` and `runtime` deliberately retain the public shapes used by the
    original `decision_vars -> cluster_selector -> model_builder` chain.  The values are
    reconstructed from one immutable RunSnapshot only; no file path or mutable catalog is
    consulted here.
    """

    ds: Dict[str, Any]
    run_params: Dict[str, Any]
    runtime: Dict[str, Any]


def _situation_from_payload(raw: Mapping[str, Any]) -> Situation:
    try:
        return materialize_situation(raw)
    except SnapshotMaterializationError as exc:
        raise AlgorithmInputError(f"invalid frozen Situation in RunSnapshot: {exc}") from exc


def _catalogs_from_payload(raw: Mapping[str, Any]):
    try:
        aircraft = tuple(AircraftType.from_mapping(v) for v in raw.get("aircraft_types", []))
        resources = tuple(ResourceType.from_mapping(v) for v in raw.get("resource_types", []))
        requirements = tuple(
            AircraftResourceRequirement.from_mapping(v)
            for v in raw.get("aircraft_resource_requirements", [])
        )
    except (TypeError, ValueError) as exc:
        raise AlgorithmInputError(f"invalid frozen catalog data in RunSnapshot: {exc}") from exc
    return aircraft, resources, requirements


def _distance_rows(raw: Sequence[Mapping[str, Any]]) -> Dict[Tuple[str, str], float]:
    out: Dict[Tuple[str, str], float] = {}
    for row in raw:
        try:
            key = (str(row["airport_id"]), str(row["mission_id"]))
            value = float(row["distance_km"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AlgorithmInputError(f"invalid frozen OD row: {row!r}") from exc
        if key in out:
            raise AlgorithmInputError(f"duplicate frozen OD pair: {key}")
        if value < 0:
            raise AlgorithmInputError(f"negative frozen OD distance: {key}")
        out[key] = value
    return out


def _mission_aircraft_ids(situation: Situation) -> set[str]:
    return {
        row.aircraft_type_id
        for mission in situation.missions
        for row in mission.aircraft_requirements
    }


def _max_navigation_delay(scenario: Optional[DamageScenario]) -> int:
    if scenario is None:
        return 0
    mx = 0
    for event in scenario.events:
        if isinstance(event.effect, NavigationDelayEffect):
            mx = max(mx, event.effect.departure_delay_slots, event.effect.return_delay_slots)
    return mx


def _compute_horizon(
    situation: Situation,
    aircraft_types: Sequence[AircraftType],
    od: Mapping[Tuple[str, str], float],
    scenario: Optional[DamageScenario],
) -> Tuple[int, int]:
    """Preserve the original loose-horizon policy using canonical half-open windows.

    The old code used closed `Tstart-end`; the new Mission/Damage objects use [start,end),
    so the last active slot is `end - 1`.  The loose margin is otherwise intentionally
    kept aligned with the GitHub implementation to avoid changing search behaviour while
    the algorithm is refactored.
    """

    starts: List[int] = [m.window_start_slot for m in situation.missions]
    ends: List[int] = [m.window_end_slot - 1 for m in situation.missions]
    if scenario is not None:
        starts.extend(e.start_slot for e in scenario.events)
        ends.extend(e.end_slot - 1 for e in scenario.events)

    t_min = min(starts) if starts else 0
    base_end = max(ends) if ends else t_min

    mission_types = _mission_aircraft_ids(situation)
    speed_by_type = {
        item.aircraft_type_id: float(item.speed_kmh)
        for item in aircraft_types
        if item.aircraft_type_id in mission_types and item.speed_kmh is not None
    }
    missing_speed = sorted(mission_types - set(speed_by_type))
    if missing_speed:
        raise AlgorithmInputError(f"mission aircraft types missing speed_kmh: {missing_speed}")
    if mission_types and any(v <= 0 for v in speed_by_type.values()):
        raise AlgorithmInputError("mission aircraft speed_kmh must be positive")

    max_dist = max(od.values(), default=0.0)
    min_speed = min(speed_by_type.values(), default=1.0)
    max_fly_windows = int(ceil((max_dist / min_speed) * (60.0 / DELTA_MIN))) if max_dist > 0 else 0
    max_tau_work = max(
        (row.tau_work_windows for m in situation.missions for row in m.aircraft_requirements),
        default=0,
    )
    max_delay = _max_navigation_delay(scenario)
    margin = 2 * max_fly_windows + max_tau_work + 2 * max_delay
    t_max = max(t_min, base_end + margin)
    return int(t_min), int(t_max)


def _baseline_projection(situation: Situation, *, horizon_slots: int) -> DamageProjection:
    capacity: Dict[str, Tuple[int, ...]] = {}
    resources: Dict[str, Dict[str, Tuple[float, ...]]] = {}
    dep: Dict[str, Tuple[int, ...]] = {}
    ret: Dict[str, Tuple[int, ...]] = {}
    for item in situation.airports:
        profile = item.operational_profile
        if not profile.configuration_complete or profile.capacity_per_window is None:
            raise AlgorithmInputError(f"airport operational profile incomplete: {item.airport_id}")
        capacity[item.airport_id] = tuple([int(profile.capacity_per_window)] * horizon_slots)
        resources[item.airport_id] = {
            row.resource_type_id: tuple([float(row.initial_quantity or 0.0)] * horizon_slots)
            for row in profile.resource_stocks
        }
        dep[item.airport_id] = tuple([0] * horizon_slots)
        ret[item.airport_id] = tuple([0] * horizon_slots)
    return DamageProjection(capacity, resources, dep, ret, ())


def _crop(seq: Sequence[Any], t_min: int, t_max: int) -> List[Any]:
    return list(seq[t_min:t_max + 1])


def _resource_maps(
    situation: Situation,
    resources: Sequence[ResourceType],
    projection: DamageProjection,
    t_min: int,
    t_max: int,
):
    by_id = {r.resource_type_id: r for r in resources}
    resource_ids = sorted(by_id)
    generic: Dict[str, Dict[str, List[float]]] = {}
    materials: Dict[str, Dict[str, List[float]]] = {}
    munitions: Dict[str, Dict[str, List[float]]] = {}
    fuel_by_id: Dict[str, Dict[str, List[float]]] = {}

    for item in situation.airports:
        aid = item.airport_id
        source = projection.resource_available.get(aid, {})
        generic[aid] = {}
        materials[aid] = {}
        munitions[aid] = {}
        fuel_by_id[aid] = {}
        for rid in resource_ids:
            seq = source.get(rid)
            if seq is None:
                # Complete operational profile: an absent stock row means zero stock.
                seq = tuple([0.0] * (t_max + 1))
            cropped = [float(v) for v in _crop(seq, t_min, t_max)]
            generic[aid][rid] = cropped
            category = by_id[rid].category
            if category == "material":
                materials[aid][rid] = cropped
            elif category == "munition":
                munitions[aid][rid] = cropped
            elif category == "fuel":
                fuel_by_id[aid][rid] = cropped

    fuel_ids = sorted(r.resource_type_id for r in resources if r.category == "fuel")
    legacy_fuel: Dict[str, List[float]] = {}
    if len(fuel_ids) == 1:
        rid = fuel_ids[0]
        legacy_fuel = {aid: fuel_by_id[aid][rid] for aid in fuel_by_id}
    elif not fuel_ids:
        legacy_fuel = {a.airport_id: [0.0] * (t_max - t_min + 1) for a in situation.airports}
    # Multiple fuel types intentionally have no lossy legacy aggregate. The generic
    # `resources` structure is authoritative for the in-place model_builder refactor.

    return generic, fuel_by_id, legacy_fuel, materials, munitions


def _build_replenishment_maps(
    situation: Situation,
    resources: Sequence[ResourceType],
    *,
    t_min: int,
    t_max: int,
) -> Tuple[
    Dict[str, Dict[str, List[float]]],
    Dict[str, Dict[str, List[float]]],
    Dict[str, Dict[str, List[float]]],
]:
    """Return capacity, actual-arrival, and cumulative-arrival series.

    Actual replenishment is a frozen Situation fact. Missing schedule entries mean zero.
    The baseline capacity is only a ceiling and never creates stock automatically.
    Entries before the cropped run horizon are folded into the cumulative series so the
    first visible slot sees stock that has already arrived.
    """

    T = t_max - t_min + 1
    resource_ids = sorted(r.resource_type_id for r in resources)
    capacity: Dict[str, Dict[str, List[float]]] = {}
    actual: Dict[str, Dict[str, List[float]]] = {}
    cumulative: Dict[str, Dict[str, List[float]]] = {}

    for item in situation.airports:
        aid = item.airport_id
        stock_by_id = {
            row.resource_type_id: row
            for row in item.operational_profile.resource_stocks
        }
        capacity[aid] = {}
        actual[aid] = {}
        cumulative[aid] = {}

        schedule_by_resource: Dict[str, Dict[int, float]] = {}
        for row in item.resource_replenishments:
            schedule_by_resource.setdefault(row.resource_type_id, {})[row.slot] = float(row.quantity)

        for rid in resource_ids:
            stock = stock_by_id.get(rid)
            cap = 0.0 if stock is None else float(stock.replenishment_capacity_per_window or 0.0)
            capacity[aid][rid] = [cap] * T

            schedule = schedule_by_resource.get(rid, {})
            prior = sum(float(q) for slot, q in schedule.items() if slot < t_min)
            visible: List[float] = []
            running = prior
            cumul: List[float] = []
            for abs_slot in range(t_min, t_max + 1):
                q = float(schedule.get(abs_slot, 0.0))
                if q < -1e-12 or q > cap + 1e-9:
                    raise AlgorithmInputError(
                        f"replenishment violates frozen capacity: {aid}/{rid}/slot={abs_slot}"
                    )
                visible.append(q)
                running += q
                cumul.append(running)
            actual[aid][rid] = visible
            cumulative[aid][rid] = cumul

    return capacity, actual, cumulative


def _build_timeview(
    situation: Situation,
    resources: Sequence[ResourceType],
    scenario: Optional[DamageScenario],
    *,
    t_min: int,
    t_max: int,
) -> Dict[str, Any]:
    horizon_abs = max(1, t_max + 1)
    projection = (
        project_damage(situation, scenario, horizon_slots=horizon_abs)
        if scenario is not None
        else _baseline_projection(situation, horizon_slots=horizon_abs)
    )

    base_boundary, _fuel_by_id_base, _legacy_fuel_base, _mats_base, _muns_base = _resource_maps(
        situation, resources, projection, t_min, t_max
    )
    replenishment_capacity, replenishment_actual, replenishment_cumulative = _build_replenishment_maps(
        situation, resources, t_min=t_min, t_max=t_max
    )

    # The optimizer consumes an effective cumulative stock boundary:
    # damage-adjusted baseline stock + actual replenishment that has arrived through t.
    # Replenishment capacity alone never changes this boundary.
    effective_resources: Dict[str, Dict[str, List[float]]] = {}
    for item in situation.airports:
        aid = item.airport_id
        effective_resources[aid] = {}
        for rid, base_seq in (base_boundary.get(aid) or {}).items():
            cumul = (replenishment_cumulative.get(aid) or {}).get(rid)
            if not isinstance(cumul, list) or len(cumul) != len(base_seq):
                raise AlgorithmInputError(f"replenishment series missing/short: {aid}/{rid}")
            effective_resources[aid][rid] = [
                float(base_seq[i]) + float(cumul[i])
                for i in range(len(base_seq))
            ]

    by_id = {r.resource_type_id: r for r in resources}
    fuel_by_id: Dict[str, Dict[str, List[float]]] = {}
    mats: Dict[str, Dict[str, List[float]]] = {}
    muns: Dict[str, Dict[str, List[float]]] = {}
    for aid, rows in effective_resources.items():
        fuel_by_id[aid] = {}
        mats[aid] = {}
        muns[aid] = {}
        for rid, seq in rows.items():
            meta = by_id.get(rid)
            if meta is None:
                raise AlgorithmInputError(f"resource metadata missing: {rid}")
            if meta.category == "fuel":
                fuel_by_id[aid][rid] = seq
            elif meta.category == "material":
                mats[aid][rid] = seq
            elif meta.category == "munition":
                muns[aid][rid] = seq

    fuel_ids = sorted(r.resource_type_id for r in resources if r.category == "fuel")
    legacy_fuel: Dict[str, List[float]] = {}
    if len(fuel_ids) == 1:
        rid = fuel_ids[0]
        legacy_fuel = {aid: fuel_by_id[aid][rid] for aid in fuel_by_id}
    elif not fuel_ids:
        legacy_fuel = {a.airport_id: [0.0] * (t_max - t_min + 1) for a in situation.airports}

    z0: Dict[str, Dict[str, int]] = {}
    shocks: Dict[str, Dict[str, List[int]]] = {}
    T = t_max - t_min + 1
    for item in situation.airports:
        aid = item.airport_id
        z0[aid] = {
            row.aircraft_type_id: int(row.initial_quantity or 0)
            for row in item.operational_profile.aircraft_support
        }
        shocks[aid] = {key: [0] * T for key in z0[aid]}

    # Frozen aircraft loss uses positive loss in the domain and negative delta in the
    # existing inventory recurrence. Losses before t_min are folded into the new z0.
    for shock in projection.aircraft_loss_shocks:
        for aircraft_type_id, loss in shock.aircraft_loss:
            if aircraft_type_id not in z0.get(shock.airport_id, {}):
                raise AlgorithmInputError(
                    f"damage references unsupported aircraft type: {shock.airport_id}/{aircraft_type_id}"
                )
            if shock.start_slot < t_min:
                z0[shock.airport_id][aircraft_type_id] = max(
                    0, z0[shock.airport_id][aircraft_type_id] - int(loss)
                )
            elif shock.start_slot <= t_max:
                shocks[shock.airport_id][aircraft_type_id][shock.start_slot - t_min] -= int(loss)

    return {
        "cap": {aid: _crop(seq, t_min, t_max) for aid, seq in projection.capacity_per_window.items()},
        # Effective cumulative boundary consumed by model_facts/model_builder.
        "resources": effective_resources,
        # Explicit components retained for analysis/audit. Do not infer one from the other.
        "resource_base_boundary": base_boundary,
        "resource_replenishment_capacity": replenishment_capacity,
        "resource_replenishment_actual": replenishment_actual,
        "resource_replenishment_cumulative": replenishment_cumulative,
        "fuel_by_resource": fuel_by_id,
        "fuel": legacy_fuel,
        "materials": mats,
        "munitions": muns,
        "radar_out_delay": {
            aid: _crop(seq, t_min, t_max) for aid, seq in projection.departure_delay_slots.items()
        },
        "radar_ret_delay": {
            aid: _crop(seq, t_min, t_max) for aid, seq in projection.return_delay_slots.items()
        },
        "z0": z0,
        "aircraft_shock": shocks,
        "T": T,
    }


def _build_static(situation: Situation, od: Mapping[Tuple[str, str], float]) -> Dict[str, Any]:
    airport_ids = [a.airport_id for a in situation.airports]
    mission_ids = [m.mission_id for m in situation.missions]
    airports = []
    for item in situation.airports:
        profile = item.operational_profile
        airports.append({
            "airport_id": item.airport_id,
            "name": item.airport.airport_name,
            "lon": float(item.airport.longitude),
            "lat": float(item.airport.latitude),
            "capacity": int(profile.capacity_per_window or 0),
            "supported_aircraft": {
                row.aircraft_type_id: int(row.initial_quantity or 0)
                for row in profile.aircraft_support
            },
            "tau_reset": {
                row.aircraft_type_id: int(row.tau_reset_windows or 0)
                for row in profile.aircraft_support
            },
        })

    missions = []
    for mission in situation.missions:
        missions.append({
            "mission_id": mission.mission_id,
            "name": mission.name,
            "lon": float(mission.longitude),
            "lat": float(mission.latitude),
            # Keep the original field name but use a relative, half-open pair.  The
            # optimized decision_vars understands this field explicitly.
            "_duty_window": (mission.window_start_slot, mission.window_end_slot),
            "required_sorties": {
                row.aircraft_type_id: int(row.required_sorties)
                for row in mission.aircraft_requirements
            },
            "tau_work": {
                row.aircraft_type_id: int(row.tau_work_windows)
                for row in mission.aircraft_requirements
            },
        })

    distance = {
        "airports": airport_ids,
        "missions": mission_ids,
        "matrix": [[float(od[(aid, mid)]) for mid in mission_ids] for aid in airport_ids],
    }
    return {
        "airports": airports,
        "missions": missions,
        "scenarios": {"scenarios": []},
        "distance": distance,
        "base_capacity": {a["airport_id"]: float(a["capacity"]) for a in airports},
    }


def _build_run_params(
    aircraft_types: Sequence[AircraftType],
    resource_types: Sequence[ResourceType],
    requirements: Sequence[AircraftResourceRequirement],
) -> Dict[str, Any]:
    req_by_aircraft: Dict[str, Dict[str, Dict[str, float]]] = {}
    res_by_id = {r.resource_type_id: r for r in resource_types}
    for row in requirements:
        req_by_aircraft.setdefault(row.aircraft_type_id, {})[row.resource_type_id] = {
            "basis": row.basis,
            "quantity": float(row.quantity),
        }

    aircrafts: Dict[str, Dict[str, Any]] = {}
    for item in aircraft_types:
        reqs = req_by_aircraft.get(item.aircraft_type_id, {})
        cfg: Dict[str, Any] = {
            "speed": float(item.speed_kmh),
            "max_range": float(item.max_range_km),
            "reserve_ratio": float(item.reserve_ratio),
            "capacity_factor": float(item.departure_capacity_occupancy_factor),
            "arrival_capacity_factor": float(item.arrival_capacity_occupancy_factor),
            # Canonical generic resource requirements. The optimized model_builder will
            # consume this directly; legacy fields below remain only where lossless.
            "resource_requirements": reqs,
        }

        fuel_rows = [
            (rid, spec) for rid, spec in reqs.items()
            if res_by_id.get(rid) is not None and res_by_id[rid].category == "fuel"
        ]
        if len(fuel_rows) == 1 and fuel_rows[0][1]["basis"] == "per_hour":
            cfg["fuel_resource_id"] = fuel_rows[0][0]
            cfg["fuel_rate"] = float(fuel_rows[0][1]["quantity"])
        materials_usage = {
            rid: spec["quantity"] for rid, spec in reqs.items()
            if res_by_id.get(rid) is not None
            and res_by_id[rid].category == "material"
            and spec["basis"] == "per_sortie"
        }
        munitions_usage = {
            rid: spec["quantity"] for rid, spec in reqs.items()
            if res_by_id.get(rid) is not None
            and res_by_id[rid].category == "munition"
            and spec["basis"] == "per_sortie"
        }
        cfg["materials_usage"] = materials_usage
        cfg["munitions_usage"] = munitions_usage
        aircrafts[item.aircraft_type_id] = cfg

    return {
        "aircrafts": aircrafts,
        "resource_types": {r.resource_type_id: r.to_dict() for r in resource_types},
    }


def build_algorithm_input(snapshot: RunSnapshot) -> AlgorithmInputBundle:
    """Build the existing algorithm's in-memory inputs from one immutable RunSnapshot."""

    if not isinstance(snapshot, RunSnapshot):
        raise AlgorithmInputError("snapshot must be RunSnapshot")
    payload = snapshot.to_dict()
    if payload.get("schema") != SNAPSHOT_SCHEMA:
        raise AlgorithmInputError(f"unsupported snapshot schema: {payload.get('schema')!r}")

    situation = _situation_from_payload(payload.get("situation") or {})
    aircraft_types, resource_types, requirements = _catalogs_from_payload(payload.get("catalogs") or {})
    try:
        runtime_obj = RunConfig.from_mapping(payload.get("run_config") or {})
    except ValueError as exc:
        raise AlgorithmInputError(f"invalid frozen RunConfig: {exc}") from exc

    od = _distance_rows(payload.get("od_distances") or [])
    expected = {
        (a.airport_id, m.mission_id)
        for a in situation.airports
        for m in situation.missions
    }
    if set(od) != expected:
        missing = sorted(expected - set(od))
        extra = sorted(set(od) - expected)
        raise AlgorithmInputError(f"frozen OD cross-product mismatch; missing={missing[:3]}, extra={extra[:3]}")

    scenario: Optional[DamageScenario] = None
    if runtime_obj.damage_scenario_id is not None:
        try:
            scenario = situation.get_damage_scenario(runtime_obj.damage_scenario_id)
        except KeyError as exc:
            raise AlgorithmInputError(
                f"selected damage scenario absent from frozen Situation: {runtime_obj.damage_scenario_id}"
            ) from exc

    t_min, t_max = _compute_horizon(situation, aircraft_types, od, scenario)
    timeview = _build_timeview(
        situation,
        resource_types,
        scenario,
        t_min=t_min,
        t_max=t_max,
    )
    static = _build_static(situation, od)

    # Shift half-open duty windows to the same relative time axis as cropped timeview.
    for mission in static["missions"]:
        start_abs, end_abs = mission["_duty_window"]
        mission["_duty_window"] = (int(start_abs - t_min), int(end_abs - t_min))

    ds = {
        "static": static,
        "timeview": timeview,
        "distance": static["distance"],
        "range": (t_min, t_max),
        "snapshot_hash": snapshot.content_hash,
        "run_id": snapshot.run_id,
    }
    runtime = runtime_obj.to_dict()
    # Existing cluster_selector interprets list-valued core_airports as a fixed 2.0
    # internal benefit multiplier, matching the original UI intent.
    runtime["core_airports"] = list(runtime_obj.core_airports)
    run_params = _build_run_params(aircraft_types, resource_types, requirements)
    return AlgorithmInputBundle(ds=ds, run_params=run_params, runtime=runtime)
