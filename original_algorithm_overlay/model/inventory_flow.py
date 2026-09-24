"""Pure inventory timeline facts for the pending stock-constraint migration.

This module intentionally is not wired into ``model_builder`` yet. The canonical
Situation has no warehouse-capacity field, so replacing the live constraint before a
data migration would silently invent business data.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence, Tuple


class InventoryFlowError(ValueError):
    pass


@dataclass(frozen=True)
class InventoryWindowResult:
    window: int
    opening_quantity: float
    storage_capacity: float
    damage_loss: float
    sortie_consumption: float
    planned_replenishment: float
    replenishment_throughput: float
    received: float
    throughput_rejected: float
    storage_overflow: float
    ending_quantity: float


def _nonnegative(value: float, field: str, window: int | None = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise InventoryFlowError(f"{field} must be numeric") from exc
    suffix = "" if window is None else f" at window={window}"
    if not isfinite(out) or out < 0:
        raise InventoryFlowError(f"{field} must be nonnegative and finite{suffix}")
    return out


def calculate_inventory_timeline(
    *,
    initial_quantity: float,
    storage_capacity: Sequence[float],
    replenishment_throughput: Sequence[float],
    planned_replenishment: Sequence[float],
    sortie_consumption: Sequence[float],
    tolerance: float = 1e-9,
) -> Tuple[InventoryWindowResult, ...]:
    """Apply the confirmed within-window inventory event order.

    At each window start, damage/repair sets storage capacity and excess stock is lost.
    Sorties then extract resources. At window end, planned supply is admitted subject to
    both the independent throughput limit and remaining warehouse space. Capacity repair
    never recreates stock lost in an earlier window.
    """
    horizon = len(storage_capacity)
    lengths = {
        len(replenishment_throughput),
        len(planned_replenishment),
        len(sortie_consumption),
        horizon,
    }
    if len(lengths) != 1:
        raise InventoryFlowError("inventory timeline inputs must have equal lengths")

    stock = _nonnegative(initial_quantity, "initial_quantity")
    rows = []
    for window in range(horizon):
        capacity = _nonnegative(storage_capacity[window], "storage_capacity", window)
        throughput = _nonnegative(
            replenishment_throughput[window], "replenishment_throughput", window
        )
        planned = _nonnegative(planned_replenishment[window], "planned_replenishment", window)
        consumption = _nonnegative(sortie_consumption[window], "sortie_consumption", window)

        damage_loss = max(0.0, stock - capacity)
        stock = min(stock, capacity)
        opening = stock
        if consumption > stock + tolerance:
            raise InventoryFlowError(
                f"sortie consumption exceeds available inventory at window={window}: "
                f"consumption={consumption}, available={stock}"
            )
        stock = max(0.0, stock - consumption)

        throughput_accepted = min(planned, throughput)
        throughput_rejected = planned - throughput_accepted
        room = max(0.0, capacity - stock)
        received = min(throughput_accepted, room)
        storage_overflow = throughput_accepted - received
        stock += received

        rows.append(InventoryWindowResult(
            window=window,
            opening_quantity=opening,
            storage_capacity=capacity,
            damage_loss=damage_loss,
            sortie_consumption=consumption,
            planned_replenishment=planned,
            replenishment_throughput=throughput,
            received=received,
            throughput_rejected=throughput_rejected,
            storage_overflow=storage_overflow,
            ending_quantity=stock,
        ))
    return tuple(rows)


__all__ = [
    "InventoryFlowError",
    "InventoryWindowResult",
    "calculate_inventory_timeline",
]
