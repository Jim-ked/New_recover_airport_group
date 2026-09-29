from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence

from backend.domain.damage import DamageScenario, DynamicInventoryResourceDamageEffect
from backend.domain.situation import Situation


DYNAMIC_INVENTORY_SEMANTICS = "physical_inventory_v1"
_EPS = 1e-9


class DynamicInventoryError(ValueError):
    pass


def _event_end(event: Any) -> int:
    if event.recovery_mode == "average":
        return int(event.end_slot + (event.recovery_duration_slots or 0))
    return int(event.end_slot)


def build_dynamic_inventory_facts(
    situation: Situation,
    scenario: Optional[DamageScenario],
    contract: Mapping[str, Any],
    *,
    replenishment_capacity_abs: Mapping[str, Mapping[str, Sequence[float]]],
    replenishment_actual_abs: Mapping[str, Mapping[str, Sequence[float]]],
    t_min: int,
    t_max: int,
) -> Dict[str, Any]:
    """Build archived-v5 stock-conservation facts on the visible time axis."""

    if contract.get("semantics") != DYNAMIC_INVENTORY_SEMANTICS:
        raise DynamicInventoryError("unsupported dynamic inventory semantics")
    raw_storage = contract.get("storage_capacity")
    if not isinstance(raw_storage, Mapping):
        raise DynamicInventoryError("dynamic inventory storage_capacity is required")
    horizon = t_max + 1
    if horizon <= 0 or t_min < 0 or t_min > t_max:
        raise DynamicInventoryError("invalid dynamic inventory horizon")

    initial: Dict[str, Dict[str, float]] = {}
    storage_abs: Dict[str, Dict[str, list[float]]] = {}
    loss_abs: Dict[str, Dict[str, list[Optional[float]]]] = {}
    event_abs: Dict[str, Dict[str, list[Optional[str]]]] = {}
    for airport in situation.airports:
        aid = airport.airport_id
        cap_rows = raw_storage.get(aid)
        if not isinstance(cap_rows, Mapping):
            raise DynamicInventoryError(f"storage capacity missing: {aid}")
        initial[aid] = {}
        storage_abs[aid] = {}
        loss_abs[aid] = {}
        event_abs[aid] = {}
        for stock in airport.operational_profile.resource_stocks:
            rid = stock.resource_type_id
            if rid not in cap_rows:
                raise DynamicInventoryError(f"storage capacity missing: {aid}/{rid}")
            base_cap = float(cap_rows[rid])
            init = float(stock.initial_quantity or 0.0)
            if not math.isfinite(base_cap) or base_cap < 0 or init > base_cap + _EPS:
                raise DynamicInventoryError(f"invalid storage capacity: {aid}/{rid}")
            initial[aid][rid] = init
            storage_abs[aid][rid] = [base_cap] * horizon
            loss_abs[aid][rid] = [None] * horizon
            event_abs[aid][rid] = [None] * horizon

    occupied: Dict[tuple[str, str], list[tuple[int, int, str]]] = {}
    if scenario is not None:
        for event in sorted(scenario.events, key=lambda row: (row.sequence, row.event_id)):
            if not isinstance(event.effect, DynamicInventoryResourceDamageEffect):
                continue
            aid = event.target.airport_id
            physical = dict(event.effect.physical_remaining_quantity)
            damaged_storage = dict(event.effect.damaged_storage_capacity)
            affected = sorted(set(physical) | set(damaged_storage))
            for rid in affected:
                if rid not in initial.get(aid, {}):
                    raise DynamicInventoryError(
                        f"damage references unconfigured resource: {aid}/{rid}"
                    )
                active_end = _event_end(event)
                rows = occupied.setdefault((aid, rid), [])
                for prior_start, prior_end, prior_id in rows:
                    if max(event.start_slot, prior_start) < min(active_end, prior_end):
                        raise DynamicInventoryError(
                            "overlapping dynamic inventory damage: "
                            f"{aid}/{rid}/{prior_id}/{event.event_id}"
                        )
                rows.append((event.start_slot, active_end, event.event_id))

                if event.start_slot < horizon:
                    if loss_abs[aid][rid][event.start_slot] is not None:
                        raise DynamicInventoryError(
                            "multiple physical loss events in one slot: "
                            f"{aid}/{rid}/slot={event.start_slot}"
                        )
                    loss_cap = physical.get(rid)
                    if loss_cap is not None:
                        loss_abs[aid][rid][event.start_slot] = float(loss_cap)

                if rid in damaged_storage:
                    base = float(raw_storage[aid][rid])
                    damaged = float(damaged_storage[rid])
                    if damaged > base + _EPS:
                        raise DynamicInventoryError(
                            "damaged storage capacity exceeds baseline: "
                            f"{aid}/{rid}/{event.event_id}"
                        )
                    start = max(0, event.start_slot)
                    end = min(horizon, event.end_slot)
                    for slot in range(start, end):
                        storage_abs[aid][rid][slot] = damaged
                        event_abs[aid][rid][slot] = event.event_id
                    if event.recovery_mode == "average":
                        duration = int(event.recovery_duration_slots or 0)
                        for slot in range(
                            event.end_slot,
                            min(horizon, event.end_slot + duration),
                        ):
                            progress = (slot - event.end_slot + 1) / duration
                            storage_abs[aid][rid][slot] = damaged + (base - damaged) * progress
                            event_abs[aid][rid][slot] = event.event_id

    opening: Dict[str, Dict[str, float]] = {}
    for aid, rows in initial.items():
        opening[aid] = {}
        for rid, init in rows.items():
            cap_seq = (replenishment_capacity_abs.get(aid) or {}).get(rid)
            arrival_seq = (replenishment_actual_abs.get(aid) or {}).get(rid)
            if (
                cap_seq is None
                or arrival_seq is None
                or len(cap_seq) < horizon
                or len(arrival_seq) < horizon
            ):
                raise DynamicInventoryError(
                    f"replenishment arrays missing/short: {aid}/{rid}"
                )
            stock = init
            for slot in range(t_min):
                loss_cap = loss_abs[aid][rid][slot]
                if loss_cap is not None:
                    stock = min(stock, float(loss_cap))
                arrival = float(arrival_seq[slot])
                capacity = float(cap_seq[slot])
                if arrival < -_EPS or arrival > capacity + _EPS:
                    raise DynamicInventoryError(
                        f"actual replenishment exceeds capacity: {aid}/{rid}/slot={slot}"
                    )
                stock += arrival
                if stock > storage_abs[aid][rid][slot] + _EPS:
                    raise DynamicInventoryError(
                        "inventory exceeds storage capacity before run horizon: "
                        f"{aid}/{rid}/slot={slot}"
                    )
            opening[aid][rid] = stock

    def crop(
        block: Mapping[str, Mapping[str, Sequence[Any]]],
    ) -> Dict[str, Dict[str, list[Any]]]:
        return {
            aid: {rid: list(seq[t_min : t_max + 1]) for rid, seq in rows.items()}
            for aid, rows in block.items()
        }

    return {
        "semantics": DYNAMIC_INVENTORY_SEMANTICS,
        "opening_inventory": opening,
        "storage_capacity": crop(storage_abs),
        "physical_loss_cap": crop(loss_abs),
        "damage_event_id": crop(event_abs),
    }


def replay_dynamic_inventory(
    timeview: Mapping[str, Any],
    consumption: Mapping[str, Mapping[str, Sequence[float]]],
    *,
    tolerance: float = 1e-7,
) -> Dict[str, Dict[str, Dict[str, list[float]]]]:
    """Independently replay exact v5 inventory conservation for a fixed schedule."""

    facts = timeview.get("dynamic_inventory")
    if not isinstance(facts, Mapping) or facts.get("semantics") != DYNAMIC_INVENTORY_SEMANTICS:
        raise DynamicInventoryError("dynamic inventory facts missing")
    T = int(timeview.get("T") or 0)
    arrivals = timeview.get("resource_replenishment_actual") or {}
    arrival_caps = timeview.get("resource_replenishment_capacity") or {}
    opening = facts.get("opening_inventory") or {}
    storage = facts.get("storage_capacity") or {}
    loss_caps = facts.get("physical_loss_cap") or {}
    out: Dict[str, Dict[str, Dict[str, list[float]]]] = {}

    for aid, rows in opening.items():
        out[aid] = {}
        for rid, raw_opening in rows.items():
            cap_seq = (storage.get(aid) or {}).get(rid)
            loss_seq = (loss_caps.get(aid) or {}).get(rid)
            arr_seq = (arrivals.get(aid) or {}).get(rid)
            arr_cap_seq = (arrival_caps.get(aid) or {}).get(rid)
            use_seq = (consumption.get(aid) or {}).get(rid, [0.0] * T)
            if any(
                not isinstance(seq, Sequence) or len(seq) != T
                for seq in (cap_seq, loss_seq, arr_seq, arr_cap_seq, use_seq)
            ):
                raise DynamicInventoryError(
                    f"dynamic inventory series missing/length mismatch: {aid}/{rid}"
                )
            state = {
                "inherited": [],
                "after_physical_loss": [],
                "permanent_loss": [],
                "replenishment_actual": [],
                "available_before_consumption": [],
                "consumed_increment": [],
                "ending_inventory": [],
            }
            stock = float(raw_opening)
            for slot in range(T):
                inherited = stock
                loss_cap = loss_seq[slot]
                after_loss = (
                    min(inherited, float(loss_cap))
                    if loss_cap is not None
                    else inherited
                )
                lost = inherited - after_loss
                arrival = float(arr_seq[slot])
                arrival_cap = float(arr_cap_seq[slot])
                if arrival < -tolerance or arrival > arrival_cap + tolerance:
                    raise DynamicInventoryError(
                        f"actual replenishment exceeds capacity: {aid}/{rid}/slot={slot}"
                    )
                available = after_loss + arrival
                if available > float(cap_seq[slot]) + tolerance:
                    raise DynamicInventoryError(
                        f"inventory exceeds storage capacity: {aid}/{rid}/slot={slot}"
                    )
                used = float(use_seq[slot])
                if used < -tolerance or used > available + tolerance:
                    raise DynamicInventoryError(
                        "dynamic inventory violated: "
                        f"{aid}/{rid}/slot={slot}, used={used}, available={available}"
                    )
                stock = max(0.0, available - used)
                for key, value in (
                    ("inherited", inherited),
                    ("after_physical_loss", after_loss),
                    ("permanent_loss", lost),
                    ("replenishment_actual", arrival),
                    ("available_before_consumption", available),
                    ("consumed_increment", used),
                    ("ending_inventory", stock),
                ):
                    state[key].append(float(value))
            out[aid][rid] = state
    return out


__all__ = [
    "DYNAMIC_INVENTORY_SEMANTICS",
    "DynamicInventoryError",
    "build_dynamic_inventory_facts",
    "replay_dynamic_inventory",
]
