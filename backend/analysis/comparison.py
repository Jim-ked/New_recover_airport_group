from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from backend.domain.run_snapshot import RunSnapshot

COMPARISON_SCHEMA_VERSION = "comparison.v1"


class ComparisonError(ValueError):
    pass


@dataclass(frozen=True)
class ComparabilityCheck:
    comparable: bool
    reasons: Tuple[str, ...] = ()

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


def _same_frozen_problem(a: Mapping[str, Any], b: Mapping[str, Any]) -> List[str]:
    reasons: List[str] = []
    if a.get("schema") != b.get("schema"):
        reasons.append("snapshot schema differs")
    if a.get("situation_content_hash") != b.get("situation_content_hash"):
        reasons.append("Situation content differs")
    if _canon(a.get("catalogs")) != _canon(b.get("catalogs")):
        reasons.append("catalog closure differs")
    if _canon(a.get("od_distances")) != _canon(b.get("od_distances")):
        reasons.append("OD distance closure differs")
    return reasons


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
    reasons = _same_frozen_problem(a, b)
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
    reasons = _same_frozen_problem(a, b)
    ca, cb = _config(a), _config(b)
    if ca.get("damage_scenario_id") != cb.get("damage_scenario_id"):
        reasons.append("damage_scenario_id differs")
    reasons.extend(_same_fields([ca, cb], ("mip_time_limit_s", "algorithm_seed")))
    return ComparabilityCheck(not reasons, tuple(reasons))


def check_r0_r1_r2(r0: RunSnapshot, r1: RunSnapshot, r2: RunSnapshot) -> ComparabilityCheck:
    """Validate the fixed Results roles used by the damage/optimization workspace.

    R0 = no damage / no cluster
    R1 = target damage / no cluster
    R2 = same target damage / cluster enabled
    """
    p0, p1, p2 = _payload(r0), _payload(r1), _payload(r2)
    reasons = _same_frozen_problem(p0, p1) + _same_frozen_problem(p0, p2)
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
            departure_totals.append(row.get("departures_total", 0))
            departure_shares.append(row.get("departure_share", 0.0))
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
        vals = [((m.get("tasks") or {}).get(mid) or {}).get("scheduled_total", 0) for m in (r0_metrics, r1_metrics, r2_metrics)]
        tasks[mid] = {"scheduled_total": _scalar_delta(*vals)}

    aircraft_ids = sorted(
        set((r0_metrics.get("aircraft") or {}).keys())
        | set((r1_metrics.get("aircraft") or {}).keys())
        | set((r2_metrics.get("aircraft") or {}).keys())
    )
    aircraft: Dict[str, Any] = {}
    for fid in aircraft_ids:
        vals = [((m.get("aircraft") or {}).get(fid) or {}).get("scheduled_total", 0) for m in (r0_metrics, r1_metrics, r2_metrics)]
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
            "scheduled_sorties_total": summary_metric("scheduled_sorties_total"),
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


def _comparison_summary(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    summary = metrics.get("summary") or {}
    peak = summary.get("peak_departure_slot") or {}
    max_airport = summary.get("max_airport_departure") or {}
    collaboration = metrics.get("collaboration") or {}
    return {
        "peak_window": peak.get("window"),
        "peak_sorties": peak.get("sorties"),
        "max_airport_departure": dict(max_airport) if isinstance(max_airport, Mapping) else None,
        "minimum_resource_remaining": _minimum_resource_summary(metrics),
        "participating_airport_count": summary.get("participating_airport_count"),
        "selected_cluster_count": summary.get("selected_cluster_count"),
        "departure_hhi": collaboration.get("departure_hhi"),
        "cross_return_ratio": collaboration.get("cross_return_ratio"),
    }


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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
            by_run[snapshot.run_id] = {field: source.get(field, 0) for field in value_fields}
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

    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "mode": "multi_scenario",
        "run_ids": run_ids,
        "configurations": configurations,
        "summary": summaries,
        "timeline": timeline,
        "difference_overview": {
            "peak_sorties": _extrema(peak_values, low_name="lowest", high_name="highest"),
            "peak_window": _extrema(peak_windows, low_name="earliest", high_name="latest"),
            "max_airport_departure_share": _extrema(max_shares, low_name="lowest", high_name="highest"),
            "minimum_resource_remaining_ratio": _extrema(min_resources, low_name="lowest", high_name="highest"),
            "participating_airport_count": _extrema(participant_counts, low_name="lowest", high_name="highest"),
        },
        "airports": _full_object_rows(rows, block_name="airports", value_fields=("departures_total", "departure_share")),
        "tasks": _full_object_rows(rows, block_name="tasks", value_fields=("scheduled_total",)),
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
    baseline_summary = summaries[baseline_run_id]
    summary_deltas: Dict[str, Any] = {}
    for rid, summary in summaries.items():
        min_resource = (summary.get("minimum_resource_remaining") or {}).get("ratio")
        base_resource = (baseline_summary.get("minimum_resource_remaining") or {}).get("ratio")
        share = (summary.get("max_airport_departure") or {}).get("share")
        base_share = (baseline_summary.get("max_airport_departure") or {}).get("share")
        slot_delta = _delta_from_baseline(summary.get("peak_window"), baseline_summary.get("peak_window"))
        summary_deltas[rid] = {
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
                "departures_total": row.get("departures_total", 0),
                "departure_share": row.get("departure_share", 0),
                "departures_total_delta": _delta_from_baseline(row.get("departures_total", 0), base_row.get("departures_total", 0)),
                "departure_share_delta": _delta_from_baseline(row.get("departure_share", 0), base_row.get("departure_share", 0)),
            }
        airports[aid] = out

    def object_with_delta(block_name: str, value_field: str) -> Dict[str, Any]:
        ids = sorted({oid for _s, m in rows for oid in (m.get(block_name) or {})})
        output: Dict[str, Any] = {}
        base_block = baseline_metrics.get(block_name) or {}
        for oid in ids:
            base_value = ((base_block.get(oid) or {}).get(value_field, 0))
            output[oid] = {
                snapshot.run_id: {
                    "value": ((metrics.get(block_name) or {}).get(oid) or {}).get(value_field, 0),
                    "delta_vs_baseline": _delta_from_baseline(
                        ((metrics.get(block_name) or {}).get(oid) or {}).get(value_field, 0),
                        base_value,
                    ),
                }
                for snapshot, metrics in rows
            }
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
        "summary": summaries,
        "summary_deltas_vs_baseline": summary_deltas,
        "timeline": timeline,
        "airports": airports,
        "tasks": object_with_delta("tasks", "scheduled_total"),
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


__all__ = [
    "COMPARISON_SCHEMA_VERSION",
    "ComparisonError",
    "ComparabilityCheck",
    "check_multi_scenario_comparable",
    "check_configuration_comparable",
    "check_r0_r1_r2",
    "build_r0_r1_r2_comparison",
    "build_multi_scenario_comparison",
    "build_configuration_comparison",
]
