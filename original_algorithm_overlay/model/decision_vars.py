# -*- coding: utf-8 -*-
"""Feasible sortie/path index construction.

Drop-in refactor of the original GitHub ``model/decision_vars.py``.

Kept deliberately stable:
- public builders: ``build_base_path_map``, ``build_path_map_from_base``,
  ``build_path_map``, ``build_var_index``, ``export_maps``;
- 15 minute slots;
- mission service window affects the time score, not hard feasibility;
- cluster semantics: S airports may cross-return within S; airports outside S return home;
- ``tau_cycle`` remains outbound + departure delay + mission work (no return leg),
  because the original F3 objective is defined on that quantity.

Targeted corrections:
- return airport must support the aircraft type;
- ``max_range`` is enforced per leg;
- navigation delay is sampled at the relevant operation time, not globally maximised;
- complete sortie identity is retained in ``PathMaps.path_records`` / ``XPATH`` so the
  model layer can stop reconstructing an ambiguous out/return pairing;
- half-open mission windows are handled natively, while old closed ``_duty_se`` data can
  still be read at this one migration boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from math import ceil
from typing import Any, Dict, List, Optional, Sequence, Tuple

DELTA_MIN = 15


class PathDataError(ValueError):
    pass


PathKey = Tuple[str, str, str, str, int, int, int]


@dataclass(frozen=True)
class SortiePath:
    origin_airport_id: str
    mission_id: str
    return_airport_id: str
    aircraft_type_id: str
    depart_slot: int
    mission_arrival_slot: int
    landing_slot: int
    ready_slot: int
    outbound_distance_km: float
    return_distance_km: float
    outbound_flight_slots: int
    return_flight_slots: int
    departure_delay_slots: int
    return_delay_slots: int
    tau_work_windows: int
    tau_reset_windows: int
    ontime_score: float
    tau_cycle: int

    @property
    def key(self) -> PathKey:
        return (
            self.origin_airport_id,
            self.mission_id,
            self.return_airport_id,
            self.aircraft_type_id,
            self.depart_slot,
            self.landing_slot,
            self.ready_slot,
        )

    @property
    def legacy_tuple(self) -> PathKey:
        return self.key


@dataclass
class BaseMaps:
    static: Dict[str, Any] = field(default_factory=dict)
    timeview: Dict[str, Any] = field(default_factory=dict)
    distance: Dict[str, Any] = field(default_factory=dict)
    A: List[str] = field(default_factory=list)
    M: List[str] = field(default_factory=list)
    K: List[str] = field(default_factory=list)
    T: int = 0

    # ``paths`` remains for existing callers; ``path_records`` is the canonical fact.
    paths: List[PathKey] = field(default_factory=list)
    path_records: List[SortiePath] = field(default_factory=list)
    ontime_score: Dict[str, Dict[str, Dict[str, Dict[int, float]]]] = field(default_factory=dict)
    tau_cycle: Dict[str, Dict[str, Dict[str, Dict[int, int]]]] = field(default_factory=dict)
    LAND_AT_base: Dict[str, Dict[int, List[Tuple[str, str, str, int]]]] = field(default_factory=dict)
    AVAIL_FROM_base: Dict[str, Dict[str, Dict[int, List[Tuple[str, str, str, int]]]]] = field(default_factory=dict)


@dataclass
class PathMaps:
    allowed_out: Dict[str, Dict[str, Dict[str, List[int]]]] = field(default_factory=dict)
    allowed_ret: Dict[str, Dict[str, Dict[str, List[int]]]] = field(default_factory=dict)
    LAND_READY: Dict[str, Dict[str, Dict[str, Dict[int, List[Tuple[str, int]]]]]] = field(default_factory=dict)
    AVAIL_FROM: Dict[str, Dict[str, Dict[int, List[Tuple[str, int]]]]] = field(default_factory=dict)
    LAND_AT: Dict[str, Dict[int, List[Tuple[str, str, int]]]] = field(default_factory=dict)
    ontime_score: Dict[str, Dict[str, Dict[str, Dict[int, float]]]] = field(default_factory=dict)
    tau_cycle: Dict[str, Dict[str, Dict[str, Dict[int, int]]]] = field(default_factory=dict)
    deviation_penalty: Dict[str, Dict[str, Dict[str, Dict[int, float]]]] = field(default_factory=dict)
    land_allow: Dict[str, List[str]] = field(default_factory=dict)
    path_records: List[SortiePath] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stable data access helpers
# ---------------------------------------------------------------------------

def _get_sets(ds: Dict[str, Any]):
    static = ds["static"]
    tv = ds["timeview"]
    dist = ds["distance"]
    A = [a["airport_id"] for a in static["airports"]]
    M = [m["mission_id"] for m in static["missions"]]
    K = set()
    for m in static["missions"]:
        K.update((m.get("tau_work") or {}).keys())
        K.update((m.get("required_sorties") or {}).keys())
    for a in static["airports"]:
        K.update((a.get("supported_aircraft") or {}).keys())
    return static, tv, dist, A, M, sorted(K)


def _distance_accessor(dist: Dict[str, Any]):
    ai = {aid: i for i, aid in enumerate(dist["airports"])}
    mi = {mid: i for i, mid in enumerate(dist["missions"])}
    mat = dist["matrix"]

    def dkm(aid: str, mid: str) -> float:
        try:
            return float(mat[ai[aid]][mi[mid]])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PathDataError(f"missing/invalid distance for {aid}->{mid}") from exc

    return dkm


def _flight_windows(distance_km: float, speed_kmh: float) -> int:
    if speed_kmh <= 0:
        raise PathDataError("speed_kmh must be positive")
    return int(ceil((distance_km / speed_kmh) * 60.0 / DELTA_MIN))


def _delay_at(seq: Optional[Sequence[int]], slot: int) -> int:
    if not seq:
        return 0
    if slot < 0:
        return 0
    if slot >= len(seq):
        return int(seq[-1])
    return max(0, int(seq[slot]))


def _mission_window_rel(mission: Dict[str, Any], ds_range: Tuple[int, int]) -> Optional[Tuple[int, int]]:
    """Return relative half-open [start,end) window.

    New snapshot adapter provides ``_duty_window`` directly.  Old ``_duty_se`` is a
    closed absolute interval and is converted only here, so legacy input does not leak
    closed-window semantics into the rest of the model.
    """
    if "_duty_window" in mission:
        raw = mission.get("_duty_window")
        if not isinstance(raw, (tuple, list)) or len(raw) != 2:
            raise PathDataError(f"invalid _duty_window for mission {mission.get('mission_id')}")
        s, e = int(raw[0]), int(raw[1])
        return (s, e) if e > s else None
    raw = mission.get("_duty_se")
    if raw is None:
        return None
    if not isinstance(raw, (tuple, list)) or len(raw) != 2:
        raise PathDataError(f"invalid _duty_se for mission {mission.get('mission_id')}")
    t_min = int(ds_range[0])
    s_abs, e_abs = int(raw[0]), int(raw[1])
    if e_abs < s_abs:
        return None
    return s_abs - t_min, e_abs - t_min + 1


def _score_work_window(arrival: int, tau_work: int, duty: Optional[Tuple[int, int]]) -> float:
    if duty is None:
        return 1.0
    s, e = duty
    if tau_work <= 0:
        return 1.0 if s <= arrival < e else 0.0
    work_end = arrival + tau_work
    overlap = max(0, min(e, work_end) - max(s, arrival))
    outside = max(0, tau_work - overlap)
    return max(0.0, 1.0 - outside / tau_work)


def _build_land_allow(static: Dict[str, Any], cluster_cfg: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    aids = [a["airport_id"] for a in static["airports"]]
    if not (cluster_cfg and cluster_cfg.get("enabled")):
        return {aid: [aid] for aid in aids}
    selected = sorted({aid for aid in (cluster_cfg.get("S") or []) if aid in aids})
    if not selected:
        return {aid: [aid] for aid in aids}
    S = set(selected)
    return {aid: (selected[:] if aid in S else [aid]) for aid in aids}


def _required_aircraft_params(acfg: Dict[str, Any], types: Sequence[str]) -> Tuple[Dict[str, float], Dict[str, float]]:
    speed: Dict[str, float] = {}
    max_range: Dict[str, float] = {}
    for f in types:
        cfg = acfg.get(f)
        if not isinstance(cfg, dict):
            raise PathDataError(f"missing aircraft configuration: {f}")
        try:
            speed[f] = float(cfg["speed"])
            max_range[f] = float(cfg["max_range"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PathDataError(f"aircraft {f} requires speed and max_range") from exc
        if speed[f] <= 0 or max_range[f] <= 0:
            raise PathDataError(f"aircraft {f} speed/max_range must be positive")
    return speed, max_range


def _support_and_reset(static: Dict[str, Any]):
    support: Dict[str, set[str]] = {}
    reset: Dict[str, Dict[str, int]] = {}
    for airport in static["airports"]:
        aid = airport["airport_id"]
        supported = airport.get("supported_aircraft") or {}
        tau_reset = airport.get("tau_reset") or {}
        support[aid] = set(supported.keys())
        reset[aid] = {}
        for f in support[aid]:
            if f not in tau_reset:
                raise PathDataError(f"airport {aid} supports {f} but tau_reset is missing")
            reset[aid][f] = int(tau_reset[f])
            if reset[aid][f] < 0:
                raise PathDataError(f"negative tau_reset: airport={aid}, aircraft={f}")
    return support, reset


# ---------------------------------------------------------------------------
# Base path construction
# ---------------------------------------------------------------------------

def build_base_path_map(ds: Dict[str, Any], run_params: Dict[str, Any]) -> BaseMaps:
    static, tv, dist, A, M, K = _get_sets(ds)
    T = int(tv["T"])
    if T <= 0:
        raise PathDataError("timeview.T must be positive")

    acfg = run_params.get("aircrafts") or {}
    speed, max_range = _required_aircraft_params(acfg, K)
    support, tau_reset = _support_and_reset(static)
    dkm = _distance_accessor(dist)
    ds_range = tuple(ds.get("range", (0, T - 1)))

    missions = {m["mission_id"]: m for m in static["missions"]}
    duty = {mid: _mission_window_rel(m, ds_range) for mid, m in missions.items()}
    tau_work = {mid: {f: int(v) for f, v in (m.get("tau_work") or {}).items()} for mid, m in missions.items()}

    bm = BaseMaps(static=static, timeview=tv, distance=dist, A=A, M=M, K=K, T=T)

    for j in A:
        for h in M:
            d_out = dkm(j, h)
            for f in K:
                if f not in support.get(j, set()):
                    continue
                if f not in tau_work.get(h, {}):
                    # Sparse mission relation: this aircraft type is not valid for h.
                    continue
                if d_out > max_range[f] + 1e-9:
                    continue

                of = _flight_windows(d_out, speed[f])
                tw = tau_work[h][f]
                if tw < 0:
                    raise PathDataError(f"negative tau_work: mission={h}, aircraft={f}")

                for k in A:
                    # A return is a real operational action: the destination must service f.
                    if f not in support.get(k, set()):
                        continue
                    d_ret = dkm(k, h)
                    if d_ret > max_range[f] + 1e-9:
                        continue
                    rf = _flight_windows(d_ret, speed[f])
                    tr = tau_reset[k][f]

                    for t_dep in range(T):
                        r_out = _delay_at((tv.get("radar_out_delay") or {}).get(j), t_dep)
                        t_arr = t_dep + of + r_out
                        after_return_flight = t_arr + tw + rf
                        if after_return_flight >= T:
                            continue
                        r_ret = _delay_at((tv.get("radar_ret_delay") or {}).get(k), after_return_flight)
                        t_ld = after_return_flight + r_ret
                        t_ready = t_ld + tr
                        if t_ld < 0 or t_ld >= T or t_ready < 0 or t_ready > T:
                            continue

                        score = _score_work_window(t_arr, tw, duty[h])
                        tau_cyc = of + r_out + tw
                        rec = SortiePath(
                            origin_airport_id=j,
                            mission_id=h,
                            return_airport_id=k,
                            aircraft_type_id=f,
                            depart_slot=t_dep,
                            mission_arrival_slot=t_arr,
                            landing_slot=t_ld,
                            ready_slot=t_ready,
                            outbound_distance_km=d_out,
                            return_distance_km=d_ret,
                            outbound_flight_slots=of,
                            return_flight_slots=rf,
                            departure_delay_slots=r_out,
                            return_delay_slots=r_ret,
                            tau_work_windows=tw,
                            tau_reset_windows=tr,
                            ontime_score=float(score),
                            tau_cycle=int(tau_cyc),
                        )
                        bm.path_records.append(rec)
                        bm.paths.append(rec.legacy_tuple)
                        bm.LAND_AT_base.setdefault(k, {}).setdefault(t_ld, []).append((j, h, f, t_dep))
                        bm.AVAIL_FROM_base.setdefault(k, {}).setdefault(f, {}).setdefault(t_ready, []).append(
                            (j, h, f, t_dep)
                        )
                        bm.ontime_score.setdefault(f, {}).setdefault(j, {}).setdefault(h, {})[t_dep] = float(score)
                        bm.tau_cycle.setdefault(f, {}).setdefault(j, {}).setdefault(h, {})[t_dep] = int(tau_cyc)

    bm.path_records.sort(key=lambda p: p.key)
    bm.paths = [p.legacy_tuple for p in bm.path_records]
    for k in bm.LAND_AT_base:
        for t in bm.LAND_AT_base[k]:
            bm.LAND_AT_base[k][t] = sorted(set(bm.LAND_AT_base[k][t]))
    for k in bm.AVAIL_FROM_base:
        for f in bm.AVAIL_FROM_base[k]:
            for t in bm.AVAIL_FROM_base[k][f]:
                bm.AVAIL_FROM_base[k][f][t] = sorted(set(bm.AVAIL_FROM_base[k][f][t]))
    return bm


# ---------------------------------------------------------------------------
# Cluster filtering.  Existing aggregate maps are retained for the current model,
# while full path records become the canonical input for the next model_builder patch.
# ---------------------------------------------------------------------------

def build_path_map_from_base(base_maps: BaseMaps, cluster_cfg: Optional[Dict[str, Any]] = None) -> PathMaps:
    land_allow = _build_land_allow(base_maps.static, cluster_cfg)
    pm = PathMaps(
        ontime_score=base_maps.ontime_score,
        tau_cycle=base_maps.tau_cycle,
        land_allow=land_allow,
    )

    enabled = bool(cluster_cfg and cluster_cfg.get("enabled"))
    S = set(cluster_cfg.get("S") or []) if enabled else set()

    for rec in base_maps.path_records:
        j, k = rec.origin_airport_id, rec.return_airport_id
        allow = (j == k) if not enabled else ((j == k) if j not in S else (k in S))
        if not allow:
            continue
        pm.path_records.append(rec)
        h, f, t_dep, t_ld, t_ready = (
            rec.mission_id, rec.aircraft_type_id, rec.depart_slot, rec.landing_slot, rec.ready_slot
        )
        pm.allowed_out.setdefault(f, {}).setdefault(j, {}).setdefault(h, []).append(t_dep)
        pm.allowed_ret.setdefault(h, {}).setdefault(k, {}).setdefault(f, []).append(t_ld)
        pm.LAND_READY.setdefault(h, {}).setdefault(k, {}).setdefault(f, {}).setdefault(t_ld, []).append((j, t_dep))
        pm.AVAIL_FROM.setdefault(k, {}).setdefault(f, {}).setdefault(t_ready, []).append((h, t_ld))
        pm.LAND_AT.setdefault(k, {}).setdefault(t_ld, []).append((h, f, t_ld))

    pm.path_records.sort(key=lambda p: p.key)
    pm.deviation_penalty = {
        f: {
            j: {
                h: {t: max(0.0, 1.0 - float(score)) for t, score in by_t.items()}
                for h, by_t in by_h.items()
            }
            for j, by_h in by_j.items()
        }
        for f, by_j in pm.ontime_score.items()
    }

    # Stable unique aggregate indexes used by old model_builder until it is replaced.
    for f, by_j in pm.allowed_out.items():
        for j, by_h in by_j.items():
            for h in by_h:
                by_h[h] = sorted(set(by_h[h]))
    for h, by_k in pm.allowed_ret.items():
        for k, by_f in by_k.items():
            for f in by_f:
                by_f[f] = sorted(set(by_f[f]))
    for h, by_k in pm.LAND_READY.items():
        for k, by_f in by_k.items():
            for f, by_t in by_f.items():
                for t in by_t:
                    by_t[t] = sorted(set(by_t[t]))
    for k, by_f in pm.AVAIL_FROM.items():
        for f, by_t in by_f.items():
            for t in by_t:
                by_t[t] = sorted(set(by_t[t]))
    for k, by_t in pm.LAND_AT.items():
        for t in by_t:
            by_t[t] = sorted(set(by_t[t]))
    return pm


def build_path_map(ds: Dict[str, Any], run_params: Dict[str, Any], cluster_cfg: Optional[Dict[str, Any]] = None) -> PathMaps:
    return build_path_map_from_base(build_base_path_map(ds, run_params), cluster_cfg=cluster_cfg)


def build_var_index(maps: PathMaps, ds: Dict[str, Any]) -> Dict[str, List[Tuple]]:
    """Return both the old aggregate indexes and the canonical full-path index."""
    T = int(ds["timeview"]["T"])
    A = [a["airport_id"] for a in ds["static"]["airports"]]
    K = set()
    for m in ds["static"]["missions"]:
        K.update((m.get("tau_work") or {}).keys())
        K.update((m.get("required_sorties") or {}).keys())
    for a in ds["static"]["airports"]:
        K.update((a.get("supported_aircraft") or {}).keys())

    xout = sorted({(p.origin_airport_id, p.mission_id, p.aircraft_type_id, p.depart_slot) for p in maps.path_records})
    xret = sorted({(p.mission_id, p.return_airport_id, p.aircraft_type_id, p.landing_slot) for p in maps.path_records})
    xpath = [p.key for p in maps.path_records]
    zidx = [(a, f, t) for a in A for f in sorted(K) for t in range(T + 1)]
    return {"XPATH": xpath, "XOUT": xout, "XRET": xret, "ZIDX": zidx}


def export_maps(maps: PathMaps) -> Dict[str, Any]:
    return {
        "paths": [asdict(p) for p in maps.path_records],
        "allowed_out": maps.allowed_out,
        "allowed_ret": maps.allowed_ret,
        "LAND_READY": maps.LAND_READY,
        "AVAIL_FROM": maps.AVAIL_FROM,
        "LAND_AT": maps.LAND_AT,
        "ontime_score": maps.ontime_score,
        "tau_cycle": maps.tau_cycle,
        "deviation_penalty": maps.deviation_penalty,
        "land_allow": maps.land_allow,
    }


__all__ = [
    "DELTA_MIN",
    "PathDataError",
    "SortiePath",
    "BaseMaps",
    "PathMaps",
    "build_base_path_map",
    "build_path_map_from_base",
    "build_path_map",
    "build_var_index",
    "export_maps",
]
