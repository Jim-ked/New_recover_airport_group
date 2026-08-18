from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

from backend.algorithm.snapshot_adapter import DELTA_MIN, build_algorithm_input
from backend.domain.run_snapshot import RunSnapshot
from backend.domain.solution import Solution, SortieChain
from original_algorithm_overlay.model.decision_vars import (
    SortiePath,
    build_base_path_map,
    build_path_map_from_base,
)
from original_algorithm_overlay.model.model_facts import ModelFactError, path_resource_use

METRICS_SCHEMA_VERSION = "metrics.v1"
_EPS = 1e-8


class MetricsBuildError(RuntimeError):
    """Frozen Run facts cannot be projected into canonical Metrics safely."""


def _zero_series(n: int) -> List[float]:
    return [0.0] * n


def _int_series(n: int) -> List[int]:
    return [0] * n


def _build_demand_breakdown(
    required_by_aircraft: Mapping[str, int],
    scheduled_by_aircraft: Mapping[str, int],
) -> Dict[str, Any]:
    """Split scheduled sorties into baseline-demand fulfilment and additional capacity."""

    required: Dict[str, int] = {}
    scheduled: Dict[str, int] = {}
    for label, source, target in (
        ("required", required_by_aircraft, required),
        ("scheduled", scheduled_by_aircraft, scheduled),
    ):
        for raw_aircraft_id, raw_value in source.items():
            aircraft_id = str(raw_aircraft_id)
            if not aircraft_id:
                raise MetricsBuildError(f"{label} demand contains a blank aircraft type")
            if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value < 0:
                raise MetricsBuildError(
                    f"{label} sorties must be nonnegative integers: {aircraft_id}"
                )
            if raw_value > 0:
                target[aircraft_id] = raw_value

    aircraft_ids = sorted(set(required) | set(scheduled))
    fulfilled = {
        aircraft_id: min(scheduled.get(aircraft_id, 0), required.get(aircraft_id, 0))
        for aircraft_id in aircraft_ids
    }
    unmet = {
        aircraft_id: max(required.get(aircraft_id, 0) - scheduled.get(aircraft_id, 0), 0)
        for aircraft_id in aircraft_ids
    }
    additional = {
        aircraft_id: max(scheduled.get(aircraft_id, 0) - required.get(aircraft_id, 0), 0)
        for aircraft_id in aircraft_ids
    }

    for aircraft_id in aircraft_ids:
        required_value = required.get(aircraft_id, 0)
        scheduled_value = scheduled.get(aircraft_id, 0)
        if scheduled_value != fulfilled[aircraft_id] + additional[aircraft_id]:
            raise MetricsBuildError(
                f"scheduled demand invariant failed for aircraft type {aircraft_id}"
            )
        if required_value != fulfilled[aircraft_id] + unmet[aircraft_id]:
            raise MetricsBuildError(
                f"required demand invariant failed for aircraft type {aircraft_id}"
            )

    required_total = sum(required.values())
    scheduled_total = sum(scheduled.values())
    fulfilled_total = sum(fulfilled.values())
    unmet_total = sum(unmet.values())
    additional_total = sum(additional.values())
    if scheduled_total != fulfilled_total + additional_total:
        raise MetricsBuildError("scheduled demand invariant failed for mission total")
    if required_total != fulfilled_total + unmet_total:
        raise MetricsBuildError("required demand invariant failed for mission total")

    return {
        "required_by_aircraft": required,
        "scheduled_by_aircraft": scheduled,
        "fulfilled_by_aircraft": fulfilled,
        "unmet_by_aircraft": unmet,
        "additional_by_aircraft": additional,
        "required_total": required_total,
        "scheduled_total": scheduled_total,
        "fulfilled_total": fulfilled_total,
        "unmet_total": unmet_total,
        "additional_total": additional_total,
        "completion_ratio": (
            fulfilled_total / required_total if required_total > 0 else None
        ),
    }


def _path_for_chain(
    chain: SortieChain,
    *,
    path_by_key: Mapping[Tuple[str, str, str, str, int, int, int], SortiePath],
    offset: int,
) -> SortiePath:
    key = (
        chain.origin_airport_id,
        chain.mission_id,
        chain.return_airport_id,
        chain.aircraft_type,
        chain.depart_window - offset,
        chain.return_window - offset,
        chain.ready_window - offset,
    )
    path = path_by_key.get(key)
    if path is None:
        raise MetricsBuildError(
            "Solution sortie_chain is not present in the frozen RunSnapshot path set: "
            f"{chain.path_id}"
        )
    return path


def _snapshot_resource_baseline(payload: Mapping[str, Any]) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Frozen pre-damage stock and replenishment-throughput baselines."""

    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    situation = payload.get("situation") or {}
    for item in situation.get("airports") or []:
        airport = item.get("airport") or {}
        profile = item.get("operational_profile") or {}
        aid = str(airport.get("airport_id") or profile.get("airport_id") or "")
        if not aid:
            raise MetricsBuildError("frozen Situation airport is missing airport_id")
        rows: Dict[str, Dict[str, float]] = {}
        for stock in profile.get("resource_stocks") or []:
            rid = str(stock.get("resource_type_id") or "")
            if not rid:
                raise MetricsBuildError(f"airport {aid} contains resource stock without resource_type_id")
            initial = stock.get("initial_quantity")
            capacity = stock.get("replenishment_capacity_per_window")
            if initial is None:
                raise MetricsBuildError(f"airport {aid}/{rid} initial resource quantity is missing")
            if capacity is None:
                raise MetricsBuildError(f"airport {aid}/{rid} replenishment capacity is missing")
            initial_f = float(initial)
            capacity_f = float(capacity)
            if initial_f < 0 or capacity_f < 0:
                raise MetricsBuildError(f"airport {aid}/{rid} resource baseline is negative")
            rows[rid] = {
                "initial_quantity": initial_f,
                "replenishment_capacity_per_window": capacity_f,
            }
        out[aid] = rows
    return out


def _snapshot_initial_aircraft(payload: Mapping[str, Any]) -> Dict[str, Dict[str, int]]:
    """Pre-damage retained aircraft baseline frozen in the Situation snapshot."""
    out: Dict[str, Dict[str, int]] = {}
    situation = payload.get("situation") or {}
    for item in situation.get("airports") or []:
        airport = item.get("airport") or {}
        profile = item.get("operational_profile") or {}
        aid = str(airport.get("airport_id") or profile.get("airport_id") or "")
        if not aid:
            raise MetricsBuildError("frozen Situation airport is missing airport_id")
        rows: Dict[str, int] = {}
        for rec in profile.get("aircraft_support") or []:
            f = str(rec.get("aircraft_type_id") or "")
            if not f:
                raise MetricsBuildError(f"airport {aid} contains aircraft support without aircraft_type_id")
            q = rec.get("initial_quantity")
            if q is None:
                raise MetricsBuildError(f"airport {aid}/{f} initial aircraft quantity is missing")
            q_int = int(q)
            if q_int < 0:
                raise MetricsBuildError(f"airport {aid}/{f} initial aircraft quantity is negative")
            rows[f] = q_int
        out[aid] = rows
    return out


def _build_aircraft_inventory_metrics(
    *,
    payload: Mapping[str, Any],
    ds: Mapping[str, Any],
    chains_with_paths: Iterable[Tuple[SortieChain, SortiePath]],
    airports: List[str],
    aircraft_types: List[str],
    windows: List[int],
) -> Dict[str, Any]:
    """Retained/recyclable aircraft state, not consumable-resource remaining stock.

    Timing follows the model facts: ready-at-t is reusable at t; damage shock at t is
    applied before departures at t; departures temporarily remove aircraft until their
    ready window.  Ratios use the pre-damage frozen initial quantity as denominator.
    """
    n = len(windows)
    t0 = windows[0] if windows else 0
    baseline = _snapshot_initial_aircraft(payload)
    z0 = (ds.get("timeview") or {}).get("z0") or {}
    shocks = (ds.get("timeview") or {}).get("aircraft_shock") or {}

    dep = {(aid, f): _int_series(n) for aid in airports for f in aircraft_types}
    ready = {(aid, f): _int_series(n) for aid in airports for f in aircraft_types}
    for chain, _path in chains_with_paths:
        dep_i = chain.depart_window - t0
        ready_i = chain.ready_window - t0
        if not (0 <= dep_i < n) or not (0 <= ready_i <= n):
            raise MetricsBuildError(f"aircraft event outside metrics horizon: {chain.path_id}")
        dep[(chain.origin_airport_id, chain.aircraft_type)][dep_i] += int(chain.sorties)
        # ready_i == n is the terminal release after the last operational slot.
        if ready_i < n:
            ready[(chain.return_airport_id, chain.aircraft_type)][ready_i] += int(chain.sorties)

    by_airport: Dict[str, Any] = {}
    for aid in airports:
        rows: Dict[str, Any] = {}
        supported = set((baseline.get(aid) or {}).keys()) | set((z0.get(aid) or {}).keys())
        for f in sorted(supported):
            init = int((baseline.get(aid) or {}).get(f, 0))
            available = float((z0.get(aid) or {}).get(f, 0))
            shock_seq = ((shocks.get(aid) or {}).get(f) or [0] * n)
            if len(shock_seq) < n:
                raise MetricsBuildError(f"aircraft shock series missing/short: {aid}/{f}")
            before_departure: List[float] = []
            after_departure: List[float] = []
            ratio_initial: List[Optional[float]] = []
            in_use: List[float] = []
            for i in range(n):
                if i > 0:
                    available += ready[(aid, f)][i]
                available += float(shock_seq[i])
                if available < -_EPS:
                    raise MetricsBuildError(f"aircraft retained quantity became negative: {aid}/{f}/t={windows[i]}")
                if abs(available) <= _EPS:
                    available = 0.0
                before_departure.append(available)
                if dep[(aid, f)][i] > available + _EPS:
                    raise MetricsBuildError(f"aircraft inventory violated in Metrics: {aid}/{f}/t={windows[i]}")
                available -= dep[(aid, f)][i]
                if abs(available) <= _EPS:
                    available = 0.0
                after_departure.append(available)
                # The useful operational measure is occupied units, derived directly
                # from departures minus ready releases. Clamp only tiny numerical noise.
                occupied = sum(dep[(aid, f)][: i + 1]) - sum(ready[(aid, f)][: i + 1])
                in_use.append(float(max(0, occupied)))
                ratio_initial.append((before_departure[-1] / init) if init > 0 else None)
            rows[f] = {
                "baseline_initial_quantity": init,
                "available_before_departure": before_departure,
                "departures": dep[(aid, f)],
                "ready_releases": ready[(aid, f)],
                "available_after_departure": after_departure,
                "in_use": in_use,
                "available_ratio_initial": ratio_initial,
            }
        by_airport[aid] = rows
    return {"state_model": "retained_recyclable", "by_airport": by_airport}


def _resource_metadata(payload: Mapping[str, Any]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    catalogs = payload.get("catalogs") or {}
    for row in catalogs.get("resource_types") or []:
        rid = str(row.get("resource_type_id") or "")
        if not rid:
            raise MetricsBuildError("frozen ResourceType is missing resource_type_id")
        out[rid] = {
            "name": str(row.get("name") or rid),
            "category": str(row.get("category") or ""),
            "unit": str(row.get("unit") or ""),
        }
    return out


def _build_resource_metrics(
    *,
    payload: Mapping[str, Any],
    ds: Mapping[str, Any],
    run_params: Mapping[str, Any],
    chains_with_paths: Iterable[Tuple[SortieChain, SortiePath]],
    windows: List[int],
    participating_airports: Iterable[str],
) -> Dict[str, Any]:
    """Consumable-stock metrics with explicit replenishment flow.

    The denominator of every remaining ratio is the frozen pre-damage initial stock.
    Replenishment capacity is a ceiling only; actual replenishment comes from the frozen
    Situation schedule and is already included in the optimizer's effective stock
    boundary.
    """

    n = len(windows)
    t0 = windows[0] if windows else 0
    metadata = _resource_metadata(payload)
    baseline = _snapshot_resource_baseline(payload)
    tv = ds.get("timeview") or {}
    effective_raw = tv.get("resources") or {}
    base_boundary_raw = tv.get("resource_base_boundary") or {}
    replenishment_capacity_raw = tv.get("resource_replenishment_capacity") or {}
    replenishment_actual_raw = tv.get("resource_replenishment_actual") or {}
    replenishment_cumulative_raw = tv.get("resource_replenishment_cumulative") or {}

    consumed_increment: Dict[str, Dict[str, List[float]]] = defaultdict(dict)
    for aid, rows in baseline.items():
        for rid in rows:
            consumed_increment[aid][rid] = _zero_series(n)

    for chain, path in chains_with_paths:
        dep_idx = chain.depart_window - t0
        if dep_idx < 0 or dep_idx >= n:
            raise MetricsBuildError(f"departure window outside metrics horizon: {chain.path_id}")
        try:
            resource_rows = path_resource_use(path, run_params)
        except ModelFactError as exc:
            raise MetricsBuildError(f"cannot reuse model resource facts: {exc}") from exc
        for row in resource_rows:
            if row.airport_id != chain.origin_airport_id:
                raise MetricsBuildError(f"resource use airport drift for path {chain.path_id}")
            if row.resource_type_id not in metadata:
                raise MetricsBuildError(f"unknown resource type in model facts: {row.resource_type_id}")
            if row.resource_type_id not in consumed_increment[row.airport_id]:
                raise MetricsBuildError(
                    f"Solution consumes resource absent from frozen airport stock: "
                    f"{row.airport_id}/{row.resource_type_id}"
                )
            consumed_increment[row.airport_id][row.resource_type_id][dep_idx] += (
                float(row.amount) * int(chain.sorties)
            )

    participating = set(participating_airports)
    category_candidates: Dict[str, List[Tuple[float, str, str, int]]] = defaultdict(list)
    category_candidates_by_window: Dict[str, Dict[int, List[Tuple[float, str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    by_airport: Dict[str, Dict[str, Any]] = {}

    for aid in sorted(baseline):
        by_resource: Dict[str, Any] = {}
        for rid in sorted(baseline[aid]):
            init = float(baseline[aid][rid]["initial_quantity"])
            cap_scalar = float(baseline[aid][rid]["replenishment_capacity_per_window"])

            def _series(block: Mapping[str, Any], label: str) -> List[float]:
                seq = ((block.get(aid) or {}).get(rid))
                if not isinstance(seq, list) or len(seq) != n:
                    raise MetricsBuildError(f"{label} series missing/length mismatch: {aid}/{rid}")
                return [float(v) for v in seq]

            base_boundary = _series(base_boundary_raw, "resource base-boundary")
            effective = _series(effective_raw, "effective resource")
            capacity = _series(replenishment_capacity_raw, "replenishment capacity")
            actual = _series(replenishment_actual_raw, "replenishment actual")
            replenishment_cumulative = _series(
                replenishment_cumulative_raw, "replenishment cumulative"
            )

            for i in range(n):
                if abs(capacity[i] - cap_scalar) > _EPS:
                    raise MetricsBuildError(
                        f"replenishment capacity drift: {aid}/{rid}/t={windows[i]}"
                    )
                if actual[i] < -_EPS or actual[i] > capacity[i] + _EPS:
                    raise MetricsBuildError(
                        f"actual replenishment exceeds capacity: {aid}/{rid}/t={windows[i]}"
                    )
                expected_effective = base_boundary[i] + replenishment_cumulative[i]
                if abs(effective[i] - expected_effective) > _EPS:
                    raise MetricsBuildError(
                        f"effective stock boundary drift: {aid}/{rid}/t={windows[i]}"
                    )

            inc = [float(v) for v in consumed_increment[aid][rid]]
            cumul: List[float] = []
            running = 0.0
            for value in inc:
                running += value
                cumul.append(running)

            remaining: List[float] = []
            ratio_initial: List[Optional[float]] = []
            for i in range(n):
                rem = effective[i] - cumul[i]
                if rem < -_EPS:
                    raise MetricsBuildError(
                        f"resource invariant violated after solved schedule: {aid}/{rid}/t={windows[i]} "
                        f"available={effective[i]} consumed={cumul[i]}"
                    )
                if abs(rem) <= _EPS:
                    rem = 0.0
                remaining.append(rem)
                ratio = (rem / init) if init > 0 else None
                ratio_initial.append(ratio)
                if (
                    aid in participating
                    and ratio is not None
                    and metadata[rid]["category"] in {"fuel", "material", "munition"}
                ):
                    category = metadata[rid]["category"]
                    category_candidates[category].append(
                        (float(ratio), aid, rid, windows[i])
                    )
                    category_candidates_by_window[category][windows[i]].append(
                        (float(ratio), aid, rid)
                    )

            by_resource[rid] = {
                "initial": init,
                "replenishment_capacity_per_window": capacity,
                "replenishment_actual": actual,
                "replenishment_cumulative": replenishment_cumulative,
                "damage_adjusted_base_boundary": base_boundary,
                "available_before_consumption": effective,
                "consumed_increment": inc,
                "consumed_cumulative": cumul,
                "remaining": remaining,
                "remaining_ratio_initial": ratio_initial,
            }
        by_airport[aid] = by_resource

    category_min: Dict[str, Optional[Dict[str, Any]]] = {}
    category_timeline: Dict[str, List[Optional[Dict[str, Any]]]] = {}
    for category in ("fuel", "material", "munition"):
        rows = category_candidates.get(category) or []
        if not rows:
            category_min[category] = None
        else:
            ratio, aid, rid, window = min(rows, key=lambda x: (x[0], x[1], x[2], x[3]))
            category_min[category] = {
                "ratio": ratio,
                "airport_id": aid,
                "resource_type_id": rid,
                "window": window,
                "scope": "participating_airports",
                "denominator": "initial_stock",
            }

        timeline_rows: List[Optional[Dict[str, Any]]] = []
        for window in windows:
            candidates = (category_candidates_by_window.get(category) or {}).get(window) or []
            if not candidates:
                timeline_rows.append(None)
                continue
            ratio, aid, rid = min(candidates, key=lambda x: (x[0], x[1], x[2]))
            timeline_rows.append({
                "ratio": ratio,
                "airport_id": aid,
                "resource_type_id": rid,
                "window": window,
                "scope": "participating_airports",
                "denominator": "initial_stock",
            })
        category_timeline[category] = timeline_rows

    return {
        "state_model": "consumable_stock_with_replenishment",
        "resource_types": metadata,
        "by_airport": by_airport,
        "category_min_remaining_ratio": category_min,
        "category_min_remaining_ratio_timeline": category_timeline,
    }


def _build_capacity_metrics(
    *,
    ds: Mapping[str, Any],
    run_params: Mapping[str, Any],
    chains_with_paths: Iterable[Tuple[SortieChain, SortiePath]],
    airports: List[str],
    windows: List[int],
) -> Dict[str, Any]:
    n = len(windows)
    t0 = windows[0] if windows else 0
    available_raw = (ds.get("timeview") or {}).get("cap") or {}
    acfg = run_params.get("aircrafts") or {}

    dep_used = {aid: _zero_series(n) for aid in airports}
    arr_used = {aid: _zero_series(n) for aid in airports}
    for chain, _path in chains_with_paths:
        cfg = acfg.get(chain.aircraft_type)
        if not isinstance(cfg, dict):
            raise MetricsBuildError(f"missing aircraft config: {chain.aircraft_type}")
        dep_factor = float(cfg.get("capacity_factor"))
        arr_factor = float(cfg.get("arrival_capacity_factor"))
        dep_idx = chain.depart_window - t0
        arr_idx = chain.return_window - t0
        if dep_idx < 0 or dep_idx >= n or arr_idx < 0 or arr_idx >= n:
            raise MetricsBuildError(f"capacity event outside metrics horizon: {chain.path_id}")
        dep_used[chain.origin_airport_id][dep_idx] += dep_factor * chain.sorties
        arr_used[chain.return_airport_id][arr_idx] += arr_factor * chain.sorties

    by_airport: Dict[str, Any] = {}
    for aid in airports:
        seq = available_raw.get(aid)
        if not isinstance(seq, list) or len(seq) != n:
            raise MetricsBuildError(f"capacity series missing/length mismatch: {aid}")
        available = [float(v) for v in seq]
        total = [dep_used[aid][i] + arr_used[aid][i] for i in range(n)]
        util: List[Optional[float]] = []
        for i in range(n):
            if total[i] - available[i] > _EPS:
                raise MetricsBuildError(
                    f"capacity invariant violated after solved schedule: {aid}/t={windows[i]} "
                    f"available={available[i]} used={total[i]}"
                )
            if available[i] > 0:
                util.append(total[i] / available[i])
            elif abs(total[i]) <= _EPS:
                util.append(None)
            else:
                raise MetricsBuildError(f"positive capacity use with zero available capacity: {aid}/t={windows[i]}")
        by_airport[aid] = {
            "available": available,
            "used_departure": dep_used[aid],
            "used_arrival": arr_used[aid],
            "used_total": total,
            "utilization": util,
        }
    return {"by_airport": by_airport}


def build_metrics_core(
    snapshot: RunSnapshot,
    solution: Solution,
    *,
    technical: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Derive only Metrics leaves already fixed by Run/Solution/frontend contracts.

    This intentionally omits only still-unresolved semantics such as threshold-driven
    "方案关注" rules. Consumable replenishment is already frozen as a separate actual
    flow capped by replenishment capacity and is included in resource Metrics.

    Confirmed presentation facts are canonical here: peak means one native 15-minute
    slot, and airport concentration is raw departure-share HHI without grading.

    Those are not guessed here. Raw facts required to derive them later are preserved.
    """
    if not isinstance(snapshot, RunSnapshot):
        raise TypeError("build_metrics_core requires RunSnapshot")
    if not isinstance(solution, Solution):
        raise TypeError("build_metrics_core requires canonical Solution")
    if snapshot.run_id != solution.run_id:
        raise MetricsBuildError("RunSnapshot and Solution run_id mismatch")

    payload = snapshot.to_dict()
    bundle = build_algorithm_input(snapshot)
    ds, run_params, runtime = bundle.ds, bundle.run_params, bundle.runtime
    t_min, t_max = ds.get("range") or (0, -1)
    windows = list(range(int(t_min), int(t_max) + 1))
    n = len(windows)
    if n <= 0:
        raise MetricsBuildError("empty metrics time horizon")

    airport_rows = ds["static"]["airports"]
    mission_rows = ds["static"]["missions"]
    airports = [str(x["airport_id"]) for x in airport_rows]
    missions = [str(x["mission_id"]) for x in mission_rows]
    aircraft_types = sorted((run_params.get("aircrafts") or {}).keys())

    cluster_cfg = None
    if solution.selected_cluster:
        cluster_cfg = {
            "enabled": True,
            "K": len(solution.selected_cluster),
            "S": list(solution.selected_cluster),
        }
    elif runtime.get("cluster_enabled"):
        raise MetricsBuildError("cluster-enabled frozen Run produced Solution without selected_cluster")

    base_maps = build_base_path_map(ds, run_params)
    maps = build_path_map_from_base(base_maps, cluster_cfg)
    path_by_key = {p.key: p for p in maps.path_records}
    chain_paths: List[Tuple[SortieChain, SortiePath]] = []
    for chain in solution.sortie_chains:
        chain_paths.append((chain, _path_for_chain(chain, path_by_key=path_by_key, offset=int(t_min))))

    dep_total = _int_series(n)
    ret_total = _int_series(n)
    by_airport_dep = {aid: _int_series(n) for aid in airports}
    by_airport_ret = {aid: _int_series(n) for aid in airports}
    by_mission_dep = {mid: _int_series(n) for mid in missions}
    by_mission_ret = {mid: _int_series(n) for mid in missions}
    by_aircraft_dep = {f: _int_series(n) for f in aircraft_types}
    by_aircraft_ret = {f: _int_series(n) for f in aircraft_types}

    airport_dep_total = {aid: 0 for aid in airports}
    airport_ret_total = {aid: 0 for aid in airports}
    mission_scheduled: Dict[str, Dict[str, int]] = {
        mid: {f: 0 for f in aircraft_types} for mid in missions
    }
    mission_by_origin: Dict[str, Dict[str, int]] = {mid: defaultdict(int) for mid in missions}
    aircraft_by_origin: Dict[str, Dict[str, int]] = {f: defaultdict(int) for f in aircraft_types}
    aircraft_total = {f: 0 for f in aircraft_types}

    cross_return = 0
    for chain, _path in chain_paths:
        dep_idx = chain.depart_window - int(t_min)
        ret_idx = chain.return_window - int(t_min)
        if not (0 <= dep_idx < n and 0 <= ret_idx < n):
            raise MetricsBuildError(f"Solution event lies outside metrics horizon: {chain.path_id}")
        q = int(chain.sorties)
        dep_total[dep_idx] += q
        ret_total[ret_idx] += q
        by_airport_dep[chain.origin_airport_id][dep_idx] += q
        by_airport_ret[chain.return_airport_id][ret_idx] += q
        by_mission_dep[chain.mission_id][dep_idx] += q
        by_mission_ret[chain.mission_id][ret_idx] += q
        by_aircraft_dep[chain.aircraft_type][dep_idx] += q
        by_aircraft_ret[chain.aircraft_type][ret_idx] += q
        airport_dep_total[chain.origin_airport_id] += q
        airport_ret_total[chain.return_airport_id] += q
        mission_scheduled[chain.mission_id][chain.aircraft_type] += q
        mission_by_origin[chain.mission_id][chain.origin_airport_id] += q
        aircraft_by_origin[chain.aircraft_type][chain.origin_airport_id] += q
        aircraft_total[chain.aircraft_type] += q
        if chain.origin_airport_id != chain.return_airport_id:
            cross_return += q

    scheduled_total = sum(dep_total)
    if scheduled_total <= 0:
        raise MetricsBuildError("canonical successful Solution has zero scheduled sorties")
    if sum(ret_total) != scheduled_total:
        raise MetricsBuildError("complete sortie chains must have equal total departures and returns")

    required_by_mission: Dict[str, Dict[str, int]] = {}
    total_required = 0
    for row in mission_rows:
        mid = str(row["mission_id"])
        req = {str(f): int(v) for f, v in (row.get("required_sorties") or {}).items() if int(v) > 0}
        required_by_mission[mid] = req
        total_required += sum(req.values())

    participating = sorted({
        aid for aid in airports
        if airport_dep_total[aid] > 0 or airport_ret_total[aid] > 0
    })
    origin_airports = sorted(aid for aid in airports if airport_dep_total[aid] > 0)
    return_airports = sorted(aid for aid in airports if airport_ret_total[aid] > 0)

    max_airport_id = min(
        airports,
        key=lambda aid: (-airport_dep_total[aid], aid),
    )
    max_airport_sorties = airport_dep_total[max_airport_id]
    max_airport_share = max_airport_sorties / scheduled_total

    airport_metrics: Dict[str, Any] = {}
    selected_set = set(solution.selected_cluster)
    core_set = set(runtime.get("core_airports") or [])
    for aid in airports:
        airport_metrics[aid] = {
            "departures_total": airport_dep_total[aid],
            "returns_total": airport_ret_total[aid],
            "departure_share": airport_dep_total[aid] / scheduled_total,
            "is_selected_cluster": aid in selected_set,
            "is_core": aid in core_set,
            "is_participating": aid in participating,
            "departures_timeline": by_airport_dep[aid],
            "returns_timeline": by_airport_ret[aid],
        }

    tasks: Dict[str, Any] = {}
    for mid in missions:
        scheduled_by_type = {f: q for f, q in mission_scheduled[mid].items() if q > 0}
        demand = _build_demand_breakdown(required_by_mission[mid], scheduled_by_type)
        tasks[mid] = {
            **demand,
            "by_origin_airport": dict(sorted(mission_by_origin[mid].items())),
            "departures_timeline": by_mission_dep[mid],
            "returns_timeline": by_mission_ret[mid],
        }

    total_fulfilled = sum(row["fulfilled_total"] for row in tasks.values())
    total_unmet = sum(row["unmet_total"] for row in tasks.values())
    total_additional = sum(row["additional_total"] for row in tasks.values())
    if scheduled_total != total_fulfilled + total_additional:
        raise MetricsBuildError("scheduled demand invariant failed for global total")
    if total_required != total_fulfilled + total_unmet:
        raise MetricsBuildError("required demand invariant failed for global total")

    aircraft: Dict[str, Any] = {}
    for f in aircraft_types:
        q = aircraft_total[f]
        aircraft[f] = {
            "scheduled_total": q,
            "scheduled_share": q / scheduled_total,
            "by_origin_airport": dict(sorted(aircraft_by_origin[f].items())),
            "departures_timeline": by_aircraft_dep[f],
            "returns_timeline": by_aircraft_ret[f],
        }

    capacity = _build_capacity_metrics(
        ds=ds,
        run_params=run_params,
        chains_with_paths=chain_paths,
        airports=airports,
        windows=windows,
    )
    for aid in airports:
        airport_metrics[aid]["capacity"] = capacity["by_airport"][aid]

    resources = _build_resource_metrics(
        payload=payload,
        ds=ds,
        run_params=run_params,
        chains_with_paths=chain_paths,
        windows=windows,
        participating_airports=participating,
    )
    aircraft_inventory = _build_aircraft_inventory_metrics(
        payload=payload,
        ds=ds,
        chains_with_paths=chain_paths,
        airports=airports,
        aircraft_types=aircraft_types,
        windows=windows,
    )

    technical_block: Dict[str, Any] = {
        "snapshot_hash": snapshot.content_hash,
        "state_models": {
            "capacity": "per_window_capacity",
            "consumable_resources": "stock_replenishment_consumption",
            "aircraft": "retained_recyclable",
            "service_assets": "not_yet_in_optimizer_fact_chain",
        },
    }
    if technical is not None:
        # Only copy solver/execution facts supplied by the Run executor. Metrics does not
        # infer them from strings or files.
        for key in ("solver_status", "objective", "gap", "solve_time_s", "algorithm_version"):
            if key in technical:
                technical_block[key] = technical[key]

    peak_idx = max(range(n), key=lambda i: (dep_total[i], -i))
    departure_hhi = sum((airport_dep_total[aid] / scheduled_total) ** 2 for aid in airports)

    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "run_id": snapshot.run_id,
        "time_axis": {
            "slot_minutes": DELTA_MIN,
            "windows": windows,
        },
        "summary": {
            "selected_cluster_count": len(solution.selected_cluster),
            "participating_airport_count": len(participating),
            "core_airport_count": len(core_set),
            "mission_count": len(missions),
            "required_sorties_total": total_required,
            "fulfilled_sorties_total": total_fulfilled,
            "unmet_sorties_total": total_unmet,
            "additional_sorties_total": total_additional,
            "scheduled_sorties_total": scheduled_total,
            "completion_ratio": (
                total_fulfilled / total_required if total_required > 0 else None
            ),
            "returned_sorties_total": sum(ret_total),
            "max_airport_departure": {
                "airport_id": max_airport_id,
                "sorties": max_airport_sorties,
                "share": max_airport_share,
            },
            "peak_departure_slot": {
                "window": windows[peak_idx],
                "sorties": dep_total[peak_idx],
                "slot_minutes": DELTA_MIN,
            },
        },
        "timeline": {
            "departures_total": dep_total,
            "returns_total": ret_total,
            "by_airport": {
                aid: {"departures": by_airport_dep[aid], "returns": by_airport_ret[aid]}
                for aid in airports
            },
            "by_mission": {
                mid: {"departures": by_mission_dep[mid], "returns": by_mission_ret[mid]}
                for mid in missions
            },
            "by_aircraft": {
                f: {"departures": by_aircraft_dep[f], "returns": by_aircraft_ret[f]}
                for f in aircraft_types
            },
        },
        "airports": airport_metrics,
        "tasks": tasks,
        "aircraft": aircraft,
        "aircraft_inventory": aircraft_inventory,
        "resources": resources,
        "collaboration": {
            "selected_cluster": list(solution.selected_cluster),
            "core_airports": sorted(core_set),
            "participating_airports": participating,
            "origin_airports": origin_airports,
            "return_airports": return_airports,
            "cross_return_sorties": cross_return,
            "cross_return_ratio": cross_return / scheduled_total,
            "departure_hhi": departure_hhi,
        },
        "technical": technical_block,
    }


__all__ = ["METRICS_SCHEMA_VERSION", "MetricsBuildError", "build_metrics_core"]
