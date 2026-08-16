from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

from backend.domain.damage import (
    AircraftDamageEffect,
    CapacityDamageEffect,
    DamageScenario,
    NavigationDelayEffect,
    ResourceDamageEffect,
)
from backend.domain.situation import Situation


class DamageProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class AircraftLossShock:
    event_id: str
    airport_id: str
    start_slot: int
    aircraft_loss: Tuple[Tuple[str, int], ...]


@dataclass(frozen=True)
class DamageProjection:
    capacity_per_window: Dict[str, Tuple[int, ...]]
    resource_available: Dict[str, Dict[str, Tuple[float, ...]]]
    departure_delay_slots: Dict[str, Tuple[int, ...]]
    return_delay_slots: Dict[str, Tuple[int, ...]]
    aircraft_loss_shocks: Tuple[AircraftLossShock, ...]


def _recover_float(before: List[float], series: List[float], start: int, end: int, damaged: float, duration: int) -> None:
    for t in range(end, min(len(series), end + duration)):
        progress = min(1.0, (t - end + 1) / duration)
        target = before[t]
        series[t] = min(target, damaged + (target - damaged) * progress)


def _recover_int(before: List[int], series: List[int], start: int, end: int, damaged: int, duration: int) -> None:
    for t in range(end, min(len(series), end + duration)):
        progress = min(1.0, (t - end + 1) / duration)
        target = before[t]
        # Discrete step recovery driven by cumulative average progress. This allows
        # average rates below one unit/slot without inventing fractional capacity.
        series[t] = min(target, int(math.floor(damaged + (target - damaged) * progress + 1e-12)))


def project_damage(situation: Situation, scenario: DamageScenario, *, horizon_slots: int) -> DamageProjection:
    if horizon_slots <= 0:
        raise DamageProjectionError("horizon_slots must be positive")
    airport_by_id = {a.airport_id: a for a in situation.airports}

    capacity: Dict[str, List[int]] = {}
    resources: Dict[str, Dict[str, List[float]]] = {}
    dep_delay: Dict[str, List[int]] = {}
    ret_delay: Dict[str, List[int]] = {}
    for aid, item in airport_by_id.items():
        op = item.operational_profile
        if not op.configuration_complete or op.capacity_per_window is None:
            raise DamageProjectionError(f"airport operational profile incomplete: {aid}")
        capacity[aid] = [op.capacity_per_window] * horizon_slots
        resources[aid] = {
            row.resource_type_id: [float(row.initial_quantity)] * horizon_slots
            for row in op.resource_stocks
            if row.initial_quantity is not None
        }
        dep_delay[aid] = [0] * horizon_slots
        ret_delay[aid] = [0] * horizon_slots

    shocks: List[AircraftLossShock] = []
    for event in sorted(scenario.events, key=lambda x: (x.sequence, x.event_id)):
        aid = event.target.airport_id
        if aid not in airport_by_id:
            raise DamageProjectionError(f"damage event targets airport outside Situation: {aid}")
        if event.start_slot >= horizon_slots:
            continue
        start = event.start_slot
        end = min(event.end_slot, horizon_slots)

        if isinstance(event.effect, AircraftDamageEffect):
            shocks.append(AircraftLossShock(event.event_id, aid, start, event.effect.aircraft_loss))
            continue

        if isinstance(event.effect, ResourceDamageEffect):
            for rid, ceiling in event.effect.remaining_quantity:
                if rid not in resources[aid]:
                    raise DamageProjectionError(f"resource {rid} is not configured at airport {aid}")
                series = resources[aid][rid]
                before = series.copy()
                damaged = min(before[start], float(ceiling))
                for t in range(start, end):
                    series[t] = min(series[t], damaged)
                if event.recovery_mode == "average" and end < horizon_slots:
                    _recover_float(before, series, start, end, series[end - 1], event.recovery_duration_slots or 1)
            continue

        if isinstance(event.effect, CapacityDamageEffect):
            series = capacity[aid]
            before = series.copy()
            damaged = min(before[start], event.effect.remaining_capacity_per_window)
            for t in range(start, end):
                series[t] = min(series[t], damaged)
            if event.recovery_mode == "average" and end < horizon_slots:
                _recover_int(before, series, start, end, series[end - 1], event.recovery_duration_slots or 1)
            continue

        if isinstance(event.effect, NavigationDelayEffect):
            for series, requested in ((dep_delay[aid], event.effect.departure_delay_slots), (ret_delay[aid], event.effect.return_delay_slots)):
                if requested <= 0:
                    continue
                before = series.copy()
                damaged = max(before[start], requested)
                for t in range(start, end):
                    series[t] = max(series[t], damaged)
                if event.recovery_mode == "average" and end < horizon_slots:
                    # Delay recovers downward. Use integer cumulative average progress.
                    duration = event.recovery_duration_slots or 1
                    for t in range(end, min(horizon_slots, end + duration)):
                        progress = min(1.0, (t - end + 1) / duration)
                        target = before[t]
                        series[t] = max(target, int(math.ceil(damaged + (target - damaged) * progress - 1e-12)))
            continue

        raise DamageProjectionError(f"unsupported damage effect for event {event.event_id}")

    return DamageProjection(
        capacity_per_window={k: tuple(v) for k, v in capacity.items()},
        resource_available={a: {r: tuple(v) for r, v in rows.items()} for a, rows in resources.items()},
        departure_delay_slots={k: tuple(v) for k, v in dep_delay.items()},
        return_delay_slots={k: tuple(v) for k, v in ret_delay.items()},
        aircraft_loss_shocks=tuple(shocks),
    )
