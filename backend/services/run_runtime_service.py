from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from backend.domain.damage import DamageEvent, DamageScenario
from backend.services.damage_projection_service import project_damage
from backend.services.run_result_service import RunResultService, RunResultServiceError
from backend.services.snapshot_materialization import materialize_situation


RUNTIME_SCHEMA_VERSION = "runtime.v1"


class RunRuntimeServiceError(RunResultServiceError):
    pass


def _event_phase(event: DamageEvent, window: int) -> Optional[str]:
    if window < event.start_slot:
        return None
    if event.damage_type == "aircraft_damage":
        return "applied"
    if event.start_slot <= window < event.end_slot:
        return "active"
    if event.recovery_mode == "average":
        end = event.end_slot + int(event.recovery_duration_slots or 0)
        if event.end_slot <= window < end:
            return "recovering"
    return None


class RunRuntimeService:
    """Read-only GIS/runtime projection built only from one successful frozen Run.

    This service is a presentation projection, not a new business authority. It combines
    the stored Snapshot, canonical Solution and canonical Metrics into deterministic
    per-window frames so the frontend never re-implements Damage or resource semantics.
    """

    def __init__(self, *, result_service: RunResultService) -> None:
        self.results = result_service

    def get_runtime(
        self,
        run_id: str,
        *,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        bundle = self.results.get_single_run(
            run_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        situation_raw = bundle.get("situation")
        run_config = bundle.get("run_config") or {}
        solution = bundle.get("solution") or {}
        metrics = bundle.get("metrics") or {}
        situation = materialize_situation(situation_raw)

        axis = metrics.get("time_axis") or {}
        windows = list(axis.get("windows") or [])
        if not windows or any(not isinstance(v, int) for v in windows):
            raise RunRuntimeServiceError("Metrics time axis is missing or invalid")
        slot_minutes = axis.get("slot_minutes")
        if not isinstance(slot_minutes, int) or slot_minutes <= 0:
            raise RunRuntimeServiceError("Metrics slot_minutes is missing or invalid")

        selected_damage_id = run_config.get("damage_scenario_id")
        scenario: Optional[DamageScenario] = None
        if selected_damage_id is not None:
            scenario = next(
                (x for x in situation.damage_scenarios if x.damage_scenario_id == selected_damage_id),
                None,
            )
            if scenario is None:
                raise RunRuntimeServiceError("frozen RunConfig damage scenario is missing from Situation")

        max_window = max(windows)
        projection = (
            project_damage(situation, scenario, horizon_slots=max_window + 1)
            if scenario is not None
            else None
        )

        airport_metrics: Mapping[str, Any] = metrics.get("airports") or {}
        resource_metrics: Mapping[str, Any] = (metrics.get("resources") or {}).get("by_airport") or {}
        aircraft_metrics: Mapping[str, Any] = (metrics.get("aircraft_inventory") or {}).get("by_airport") or {}
        selected_cluster = set((metrics.get("collaboration") or {}).get("selected_cluster") or [])
        participating = set((metrics.get("collaboration") or {}).get("participating_airports") or [])
        core = set((metrics.get("collaboration") or {}).get("core_airports") or [])

        airports: List[Dict[str, Any]] = []
        for item in situation.airports:
            a = item.airport
            airports.append({
                "airport_id": a.airport_id,
                "airport_name": a.airport_name,
                "longitude": a.longitude,
                "latitude": a.latitude,
                "is_selected_cluster": a.airport_id in selected_cluster,
                "is_participating": a.airport_id in participating,
                "is_core": a.airport_id in core,
            })

        missions = [
            {
                "mission_id": m.mission_id,
                "name": m.name,
                "longitude": m.longitude,
                "latitude": m.latitude,
                "window_start_slot": m.window_start_slot,
                "window_end_slot": m.window_end_slot,
            }
            for m in situation.missions
        ]

        chains = list(solution.get("sortie_chains") or [])
        snapshot_payload = bundle.get("snapshot") or {}
        od_distances = [
            {
                "airport_id": row.get("airport_id"),
                "mission_id": row.get("mission_id"),
                "distance_km": row.get("distance_km"),
            }
            for row in (snapshot_payload.get("od_distances") or [])
        ]

        routes = [
            {
                "path_id": row["path_id"],
                "origin_airport_id": row["origin_airport_id"],
                "mission_id": row["mission_id"],
                "return_airport_id": row["return_airport_id"],
                "aircraft_type": row["aircraft_type"],
                "depart_window": row["depart_window"],
                "return_window": row["return_window"],
                "ready_window": row["ready_window"],
                "sorties": row["sorties"],
            }
            for row in chains
        ]

        frames: List[Dict[str, Any]] = []
        for idx, window in enumerate(windows):
            current_events: List[Dict[str, Any]] = []
            if scenario is not None:
                for event in scenario.events:
                    phase = _event_phase(event, window)
                    if phase is None:
                        continue
                    current_events.append({
                        "event_id": event.event_id,
                        "airport_id": event.target.airport_id,
                        "target_type": event.target.target_type,
                        "target_id": event.target.target_id,
                        "damage_type": event.damage_type,
                        "phase": phase,
                    })

            airport_state: Dict[str, Any] = {}
            for item in situation.airports:
                aid = item.airport_id
                cap = (airport_metrics.get(aid) or {}).get("capacity") or {}
                available = cap.get("available") or []
                used_dep = cap.get("used_departure") or []
                used_arr = cap.get("used_arrival") or []
                utilization = cap.get("utilization") or []
                if not all(len(seq) == len(windows) for seq in (available, used_dep, used_arr, utilization)):
                    raise RunRuntimeServiceError(f"capacity Metrics length mismatch for {aid}")

                resources: Dict[str, Any] = {}
                for rid, row in (resource_metrics.get(aid) or {}).items():
                    remaining = row.get("remaining") or []
                    ratios = row.get("remaining_ratio_initial") or []
                    if len(remaining) != len(windows) or len(ratios) != len(windows):
                        raise RunRuntimeServiceError(f"resource Metrics length mismatch for {aid}/{rid}")
                    resources[rid] = {
                        "remaining": remaining[idx],
                        "remaining_ratio_initial": ratios[idx],
                    }

                aircraft: Dict[str, Any] = {}
                for aircraft_type, row in (aircraft_metrics.get(aid) or {}).items():
                    before = row.get("available_before_departure") or []
                    after = row.get("available_after_departure") or []
                    in_use = row.get("in_use") or []
                    if not all(len(seq) == len(windows) for seq in (before, after, in_use)):
                        raise RunRuntimeServiceError(f"aircraft Metrics length mismatch for {aid}/{aircraft_type}")
                    aircraft[aircraft_type] = {
                        "available_before_departure": before[idx],
                        "available_after_departure": after[idx],
                        "in_use": in_use[idx],
                    }

                airport_state[aid] = {
                    "capacity_available": available[idx],
                    "capacity_used_departure": used_dep[idx],
                    "capacity_used_arrival": used_arr[idx],
                    "capacity_utilization": utilization[idx],
                    "departure_delay_slots": (
                        projection.departure_delay_slots[aid][window] if projection is not None else 0
                    ),
                    "return_delay_slots": (
                        projection.return_delay_slots[aid][window] if projection is not None else 0
                    ),
                    "resources": resources,
                    "aircraft": aircraft,
                    "damage_event_ids": [
                        e["event_id"] for e in current_events if e["airport_id"] == aid
                    ],
                }

            departures = [
                {"path_id": r["path_id"], "sorties": r["sorties"]}
                for r in routes if r["depart_window"] == window
            ]
            returns = [
                {"path_id": r["path_id"], "sorties": r["sorties"]}
                for r in routes if r["return_window"] == window
            ]
            frames.append({
                "window": window,
                "departures_total": sum(x["sorties"] for x in departures),
                "returns_total": sum(x["sorties"] for x in returns),
                "departures": departures,
                "returns": returns,
                "damage_events": current_events,
                "airports": airport_state,
            })

        scenario_block = None
        if scenario is not None:
            scenario_block = {
                "damage_scenario_id": scenario.damage_scenario_id,
                "name": scenario.name,
                "category": scenario.category,
            }

        return {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "run_id": run_id,
            "time_axis": {"slot_minutes": slot_minutes, "windows": windows},
            "damage_scenario": scenario_block,
            "selected_cluster": sorted(selected_cluster),
            "participating_airports": sorted(participating),
            "core_airports": sorted(core),
            "airports": airports,
            "missions": missions,
            "od_distances": od_distances,
            "routes": routes,
            "frames": frames,
        }


__all__ = ["RUNTIME_SCHEMA_VERSION", "RunRuntimeService", "RunRuntimeServiceError"]
