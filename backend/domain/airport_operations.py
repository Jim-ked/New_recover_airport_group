from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, NoReturn, Optional, Sequence, Tuple, Union

JsonNumber = Union[int, float]
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class AirportOperationsValidationError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise AirportOperationsValidationError(message, field=field)


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _ID_RE.fullmatch(value):
        _fail(field, f"{field} must be a nonblank stable identifier")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(field, f"{field} must be a nonnegative integer")
    return value


def _optional_nonnegative_int(value: Any, field: str) -> Optional[int]:
    if value is None:
        return None
    return _nonnegative_int(value, field)


def _nonnegative_number(value: Any, field: str) -> JsonNumber:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(field, f"{field} must be a JSON number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        _fail(field, f"{field} must be a finite nonnegative number")
    return value


def _optional_string(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(field, f"{field} must be a nonblank string or null")
    return value


@dataclass(frozen=True)
class AirportAircraftSupport:
    """
    A row means the airport supports this aircraft type.

    `initial_quantity=0` is different from no row: the airport can support/receive the
    type but has no initially based aircraft. `tau_reset_windows` belongs to this
    airport-aircraft relation, not to AircraftType globally.
    """

    aircraft_type_id: str
    initial_quantity: Optional[int] = None
    tau_reset_windows: Optional[int] = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, index: int) -> "AirportAircraftSupport":
        field = f"aircraft_support[{index}]"
        allowed = {"aircraft_type_id", "initial_quantity", "tau_reset_windows"}
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(f"{field}.{unknown[0]}", f"unknown field: {field}.{unknown[0]}")
        return cls(
            aircraft_type_id=_id(value.get("aircraft_type_id"), f"{field}.aircraft_type_id"),
            initial_quantity=_optional_nonnegative_int(value.get("initial_quantity"), f"{field}.initial_quantity"),
            tau_reset_windows=_optional_nonnegative_int(value.get("tau_reset_windows"), f"{field}.tau_reset_windows"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aircraft_type_id": self.aircraft_type_id,
            "initial_quantity": self.initial_quantity,
            "tau_reset_windows": self.tau_reset_windows,
        }


@dataclass(frozen=True)
class AirportResourceStock:
    """Baseline stock plus maximum replenishment throughput for one resource.

    ``initial_quantity`` is the retained stock at the beginning of a Situation before
    damage/mission execution. ``replenishment_capacity_per_window`` is only a ceiling:
    it never creates stock by itself. Actual replenishment is a Situation fact and is
    stored separately on ``SituationAirport``.
    """

    resource_type_id: str
    initial_quantity: Optional[JsonNumber] = None
    replenishment_capacity_per_window: Optional[JsonNumber] = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, index: int) -> "AirportResourceStock":
        field = f"resource_stocks[{index}]"
        allowed = {"resource_type_id", "initial_quantity", "replenishment_capacity_per_window"}
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(f"{field}.{unknown[0]}", f"unknown field: {field}.{unknown[0]}")
        initial = value.get("initial_quantity")
        capacity = value.get("replenishment_capacity_per_window")
        return cls(
            resource_type_id=_id(value.get("resource_type_id"), f"{field}.resource_type_id"),
            initial_quantity=None if initial is None else _nonnegative_number(
                initial, f"{field}.initial_quantity"
            ),
            replenishment_capacity_per_window=None if capacity is None else _nonnegative_number(
                capacity, f"{field}.replenishment_capacity_per_window"
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_type_id": self.resource_type_id,
            "initial_quantity": self.initial_quantity,
            "replenishment_capacity_per_window": self.replenishment_capacity_per_window,
        }


@dataclass(frozen=True)
class AirportOperationalProfile:
    """
    Reusable baseline operational configuration for an airport.

    This is deliberately separate from AirportBase static facts. When copied into a
    Situation it becomes independent mutable situation data; later base/profile edits
    must not propagate into existing situations.

    Missing-row semantics are only definitive when `configuration_complete=True`:
    - no aircraft-support row => unsupported aircraft type;
    - no resource-stock row => zero stock of that resource;
    - when incomplete, absence means unknown/not entered yet, never silently zero.
    """

    airport_id: str
    configuration_complete: bool = False
    capacity_per_window: Optional[int] = None
    support_level: Optional[str] = None
    aircraft_support: Tuple[AirportAircraftSupport, ...] = ()
    resource_stocks: Tuple[AirportResourceStock, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AirportOperationalProfile":
        if not isinstance(value, Mapping):
            _fail("body", "AirportOperationalProfile must be a JSON object")
        allowed = {
            "airport_id",
            "configuration_complete",
            "capacity_per_window",
            "support_level",
            "aircraft_support",
            "resource_stocks",
        }
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(str(unknown[0]), f"unknown field: {unknown[0]}")

        complete = value.get("configuration_complete", False)
        if not isinstance(complete, bool):
            _fail("configuration_complete", "configuration_complete must be boolean")

        capacity = _optional_nonnegative_int(value.get("capacity_per_window"), "capacity_per_window")

        raw_support = value.get("aircraft_support", [])
        if not isinstance(raw_support, list):
            _fail("aircraft_support", "aircraft_support must be an array")
        support = tuple(AirportAircraftSupport.from_mapping(item, index=i) for i, item in enumerate(raw_support))
        support_ids = [row.aircraft_type_id for row in support]
        if len(support_ids) != len(set(support_ids)):
            _fail("aircraft_support", "aircraft_type_id values must be unique per airport")

        raw_stocks = value.get("resource_stocks", [])
        if not isinstance(raw_stocks, list):
            _fail("resource_stocks", "resource_stocks must be an array")
        stocks = tuple(AirportResourceStock.from_mapping(item, index=i) for i, item in enumerate(raw_stocks))
        stock_ids = [row.resource_type_id for row in stocks]
        if len(stock_ids) != len(set(stock_ids)):
            _fail("resource_stocks", "resource_type_id values must be unique per airport")

        if complete:
            if capacity is None:
                _fail("capacity_per_window", "capacity_per_window is required for a complete profile")
            for i, row in enumerate(support):
                if row.initial_quantity is None:
                    _fail(f"aircraft_support[{i}].initial_quantity", "initial_quantity is required for a complete profile")
                if row.tau_reset_windows is None:
                    _fail(f"aircraft_support[{i}].tau_reset_windows", "tau_reset_windows is required for a complete profile")
            for i, row in enumerate(stocks):
                if row.initial_quantity is None:
                    _fail(
                        f"resource_stocks[{i}].initial_quantity",
                        "initial_quantity is required for a complete profile",
                    )
                if row.replenishment_capacity_per_window is None:
                    _fail(
                        f"resource_stocks[{i}].replenishment_capacity_per_window",
                        "replenishment_capacity_per_window is required for a complete profile",
                    )

        return cls(
            airport_id=_id(value.get("airport_id"), "airport_id"),
            configuration_complete=complete,
            capacity_per_window=capacity,
            support_level=_optional_string(value.get("support_level"), "support_level"),
            aircraft_support=support,
            resource_stocks=stocks,
        )

    def supports_aircraft(self, aircraft_type_id: str) -> Optional[bool]:
        target = _id(aircraft_type_id, "aircraft_type_id")
        if any(row.aircraft_type_id == target for row in self.aircraft_support):
            return True
        return False if self.configuration_complete else None

    def resource_initial_quantity(self, resource_type_id: str) -> Optional[JsonNumber]:
        target = _id(resource_type_id, "resource_type_id")
        for row in self.resource_stocks:
            if row.resource_type_id == target:
                return row.initial_quantity
        return 0 if self.configuration_complete else None

    def replenishment_capacity(self, resource_type_id: str) -> Optional[JsonNumber]:
        target = _id(resource_type_id, "resource_type_id")
        for row in self.resource_stocks:
            if row.resource_type_id == target:
                return row.replenishment_capacity_per_window
        return 0 if self.configuration_complete else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "airport_id": self.airport_id,
            "configuration_complete": self.configuration_complete,
            "capacity_per_window": self.capacity_per_window,
            "support_level": self.support_level,
            "aircraft_support": [row.to_dict() for row in self.aircraft_support],
            "resource_stocks": [row.to_dict() for row in self.resource_stocks],
        }
