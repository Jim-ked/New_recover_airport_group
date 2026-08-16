from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, NoReturn, Optional, Union

JsonNumber = Union[int, float]
RESOURCE_CATEGORIES = frozenset({"fuel", "material", "munition"})
CONSUMPTION_BASES = frozenset({"per_sortie", "per_hour"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class CatalogValidationError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise CatalogValidationError(message, field=field)


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _ID_RE.fullmatch(value):
        _fail(field, f"{field} must be a nonblank stable identifier")
    return value


def _string(value: Any, field: str, *, optional: bool = False) -> Optional[str]:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(field, f"{field} must be a nonblank string" + (" or null" if optional else ""))
    return value


def _num(value: Any, field: str, *, minimum: float = 0.0, strict_positive: bool = False) -> JsonNumber:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(field, f"{field} must be a JSON number")
    numeric = float(value)
    if not math.isfinite(numeric):
        _fail(field, f"{field} must be finite")
    if strict_positive and numeric <= minimum:
        _fail(field, f"{field} must be greater than {minimum:g}")
    if not strict_positive and numeric < minimum:
        _fail(field, f"{field} must be greater than or equal to {minimum:g}")
    return value


@dataclass(frozen=True)
class AircraftType:
    """Reusable aircraft-type catalog data. No airport inventory belongs here."""

    aircraft_type_id: str
    name: str
    speed_kmh: Optional[JsonNumber] = None
    max_range_km: Optional[JsonNumber] = None
    reserve_ratio: Optional[JsonNumber] = None
    departure_capacity_occupancy_factor: Optional[JsonNumber] = None
    arrival_capacity_occupancy_factor: Optional[JsonNumber] = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AircraftType":
        allowed = {
            "aircraft_type_id",
            "name",
            "speed_kmh",
            "max_range_km",
            "reserve_ratio",
            "departure_capacity_occupancy_factor",
            "arrival_capacity_occupancy_factor",
        }
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(str(unknown[0]), f"unknown field: {unknown[0]}")
        speed = value.get("speed_kmh")
        max_range = value.get("max_range_km")
        reserve = value.get("reserve_ratio")
        dep_cap = value.get("departure_capacity_occupancy_factor")
        arr_cap = value.get("arrival_capacity_occupancy_factor")
        reserve_value = None
        if reserve is not None:
            reserve_value = _num(reserve, "reserve_ratio", minimum=0)
            if float(reserve_value) >= 1.0:
                _fail("reserve_ratio", "reserve_ratio must be less than 1")
        return cls(
            aircraft_type_id=_id(value.get("aircraft_type_id"), "aircraft_type_id"),
            name=_string(value.get("name"), "name") or "",
            speed_kmh=None if speed is None else _num(speed, "speed_kmh", strict_positive=True),
            max_range_km=None if max_range is None else _num(max_range, "max_range_km", strict_positive=True),
            reserve_ratio=reserve_value,
            departure_capacity_occupancy_factor=(
                None if dep_cap is None else _num(dep_cap, "departure_capacity_occupancy_factor", strict_positive=True)
            ),
            arrival_capacity_occupancy_factor=(
                None if arr_cap is None else _num(arr_cap, "arrival_capacity_occupancy_factor", strict_positive=True)
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aircraft_type_id": self.aircraft_type_id,
            "name": self.name,
            "speed_kmh": self.speed_kmh,
            "max_range_km": self.max_range_km,
            "reserve_ratio": self.reserve_ratio,
            "departure_capacity_occupancy_factor": self.departure_capacity_occupancy_factor,
            "arrival_capacity_occupancy_factor": self.arrival_capacity_occupancy_factor,
        }


@dataclass(frozen=True)
class ResourceType:
    resource_type_id: str
    name: str
    category: str
    unit: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ResourceType":
        allowed = {"resource_type_id", "name", "category", "unit"}
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(str(unknown[0]), f"unknown field: {unknown[0]}")
        category = _string(value.get("category"), "category") or ""
        if category not in RESOURCE_CATEGORIES:
            _fail("category", f"category must be one of: {', '.join(sorted(RESOURCE_CATEGORIES))}")
        return cls(
            resource_type_id=_id(value.get("resource_type_id"), "resource_type_id"),
            name=_string(value.get("name"), "name") or "",
            category=category,
            unit=_string(value.get("unit"), "unit") or "",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_type_id": self.resource_type_id,
            "name": self.name,
            "category": self.category,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class AircraftResourceRequirement:
    """How one aircraft type consumes one resource. Airport stock is stored elsewhere."""

    aircraft_type_id: str
    resource_type_id: str
    basis: str
    quantity: JsonNumber

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AircraftResourceRequirement":
        allowed = {"aircraft_type_id", "resource_type_id", "basis", "quantity"}
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(str(unknown[0]), f"unknown field: {unknown[0]}")
        basis = _string(value.get("basis"), "basis") or ""
        if basis not in CONSUMPTION_BASES:
            _fail("basis", f"basis must be one of: {', '.join(sorted(CONSUMPTION_BASES))}")
        return cls(
            aircraft_type_id=_id(value.get("aircraft_type_id"), "aircraft_type_id"),
            resource_type_id=_id(value.get("resource_type_id"), "resource_type_id"),
            basis=basis,
            quantity=_num(value.get("quantity"), "quantity", minimum=0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aircraft_type_id": self.aircraft_type_id,
            "resource_type_id": self.resource_type_id,
            "basis": self.basis,
            "quantity": self.quantity,
        }
