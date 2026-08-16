from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, NoReturn, Tuple, Union

JsonNumber = Union[int, float]
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class MissionValidationError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise MissionValidationError(message, field=field)


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _ID_RE.fullmatch(value):
        _fail(field, f"{field} must be a nonblank stable identifier")
    return value


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(field, f"{field} must be a nonblank string")
    return value


def _number(value: Any, field: str, minimum: float, maximum: float) -> JsonNumber:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(field, f"{field} must be a JSON number")
    value_f = float(value)
    if not math.isfinite(value_f) or value_f < minimum or value_f > maximum:
        _fail(field, f"{field} must be in [{minimum:g}, {maximum:g}]")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(field, f"{field} must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class MissionAircraftRequirement:
    aircraft_type_id: str
    required_sorties: int
    tau_work_windows: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, index: int) -> "MissionAircraftRequirement":
        field = f"aircraft_requirements[{index}]"
        if not isinstance(value, Mapping):
            _fail(field, f"{field} must be an object")
        allowed = {"aircraft_type_id", "required_sorties", "tau_work_windows"}
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(f"{field}.{unknown[0]}", f"unknown field: {field}.{unknown[0]}")
        sorties = _nonnegative_int(value.get("required_sorties"), f"{field}.required_sorties")
        if sorties <= 0:
            _fail(f"{field}.required_sorties", "a requirement row must have required_sorties > 0")
        return cls(
            aircraft_type_id=_id(value.get("aircraft_type_id"), f"{field}.aircraft_type_id"),
            required_sorties=sorties,
            tau_work_windows=_nonnegative_int(value.get("tau_work_windows"), f"{field}.tau_work_windows"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aircraft_type_id": self.aircraft_type_id,
            "required_sorties": self.required_sorties,
            "tau_work_windows": self.tau_work_windows,
        }


@dataclass(frozen=True)
class Mission:
    """Canonical mission value used by the reusable library and Situation copies.

    When placed in a Situation it is an independent value snapshot. Its service window
    is always half-open [start, end).
    """

    mission_id: str
    name: str
    longitude: JsonNumber
    latitude: JsonNumber
    window_start_slot: int
    window_end_slot: int
    aircraft_requirements: Tuple[MissionAircraftRequirement, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Mission":
        if not isinstance(value, Mapping):
            _fail("body", "Mission must be a JSON object")
        allowed = {
            "mission_id", "name", "longitude", "latitude",
            "window_start_slot", "window_end_slot", "aircraft_requirements",
        }
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(str(unknown[0]), f"unknown field: {unknown[0]}")

        start = _nonnegative_int(value.get("window_start_slot"), "window_start_slot")
        end = _nonnegative_int(value.get("window_end_slot"), "window_end_slot")
        if end <= start:
            _fail("window_end_slot", "half-open mission window requires end > start")

        raw = value.get("aircraft_requirements", [])
        if not isinstance(raw, list):
            _fail("aircraft_requirements", "aircraft_requirements must be an array")
        reqs = tuple(MissionAircraftRequirement.from_mapping(item, index=i) for i, item in enumerate(raw))
        ids = [r.aircraft_type_id for r in reqs]
        if len(ids) != len(set(ids)):
            _fail("aircraft_requirements", "aircraft_type_id values must be unique per mission")

        return cls(
            mission_id=_id(value.get("mission_id"), "mission_id"),
            name=_required_string(value.get("name"), "name"),
            longitude=_number(value.get("longitude"), "longitude", -180, 180),
            latitude=_number(value.get("latitude"), "latitude", -90, 90),
            window_start_slot=start,
            window_end_slot=end,
            aircraft_requirements=reqs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "name": self.name,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "window_start_slot": self.window_start_slot,
            "window_end_slot": self.window_end_slot,
            "aircraft_requirements": [r.to_dict() for r in self.aircraft_requirements],
        }
