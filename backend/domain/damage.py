from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, NoReturn, Optional, Tuple, Union

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
DAMAGE_TARGET_TYPES = frozenset({"airport", "runway", "support_element"})
DAMAGE_TYPES = frozenset({"aircraft_damage", "resource_damage", "capacity_damage", "navigation_delay"})
DAMAGE_SCENARIO_CATEGORIES = frozenset({"low", "medium", "high", "custom"})
RECOVERY_MODES = frozenset({"none", "instant", "average"})


class DamageValidationError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise DamageValidationError(message, field=field)


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _ID_RE.fullmatch(value):
        _fail(field, f"{field} must be a nonblank stable identifier")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(field, f"{field} must be a nonblank string")
    return value


def _optional_id(value: Any, field: str) -> Optional[str]:
    return None if value is None else _id(value, field)


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(field, f"{field} must be a nonnegative integer")
    return value


def _positive_int(value: Any, field: str) -> int:
    out = _nonnegative_int(value, field)
    if out <= 0:
        _fail(field, f"{field} must be a positive integer")
    return out


def _nonnegative_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(field, f"{field} must be a finite nonnegative number")
    out = float(value)
    if not math.isfinite(out) or out < 0:
        _fail(field, f"{field} must be a finite nonnegative number")
    return out


@dataclass(frozen=True)
class DamageTarget:
    airport_id: str
    target_type: str
    target_id: Optional[str] = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DamageTarget":
        if not isinstance(raw, Mapping):
            _fail("target", "target must be an object")
        allowed = {"airport_id", "target_type", "target_id"}
        unknown = set(raw) - allowed
        if unknown:
            _fail("target", f"unknown target fields: {sorted(unknown)}")
        return cls(
            airport_id=_id(raw.get("airport_id"), "target.airport_id"),
            target_type=str(raw.get("target_type", "")),
            target_id=_optional_id(raw.get("target_id"), "target.target_id"),
        )

    def __post_init__(self) -> None:
        _id(self.airport_id, "target.airport_id")
        if self.target_type not in DAMAGE_TARGET_TYPES:
            _fail("target.target_type", f"target_type must be one of {sorted(DAMAGE_TARGET_TYPES)}")
        if self.target_type == "airport":
            if self.target_id is not None:
                _fail("target.target_id", "airport target must not have target_id")
        elif self.target_id is None:
            _fail("target.target_id", f"{self.target_type} target requires target_id")

    def to_dict(self) -> Dict[str, Any]:
        return {"airport_id": self.airport_id, "target_type": self.target_type, "target_id": self.target_id}


@dataclass(frozen=True)
class AircraftDamageEffect:
    aircraft_loss: Tuple[Tuple[str, int], ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AircraftDamageEffect":
        if set(raw) != {"aircraft_loss"} or not isinstance(raw.get("aircraft_loss"), Mapping):
            _fail("effect", "aircraft_damage effect must contain only aircraft_loss object")
        rows = []
        for key, value in raw["aircraft_loss"].items():
            rows.append((_id(key, f"effect.aircraft_loss.{key}"), _positive_int(value, f"effect.aircraft_loss.{key}")))
        if not rows:
            _fail("effect.aircraft_loss", "aircraft_loss must not be empty")
        return cls(tuple(sorted(rows)))

    def to_dict(self) -> Dict[str, Any]:
        return {"aircraft_loss": {k: v for k, v in self.aircraft_loss}}


@dataclass(frozen=True)
class ResourceDamageEffect:
    """Absolute scenario-available resource ceilings for affected resources."""

    remaining_quantity: Tuple[Tuple[str, float], ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ResourceDamageEffect":
        if set(raw) != {"remaining_quantity"} or not isinstance(raw.get("remaining_quantity"), Mapping):
            _fail("effect", "resource_damage effect must contain only remaining_quantity object")
        rows = []
        for key, value in raw["remaining_quantity"].items():
            rows.append((_id(key, f"effect.remaining_quantity.{key}"), _nonnegative_number(value, f"effect.remaining_quantity.{key}")))
        if not rows:
            _fail("effect.remaining_quantity", "remaining_quantity must not be empty")
        return cls(tuple(sorted(rows)))

    def to_dict(self) -> Dict[str, Any]:
        return {"remaining_quantity": {k: v for k, v in self.remaining_quantity}}


@dataclass(frozen=True)
class CapacityDamageEffect:
    remaining_capacity_per_window: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CapacityDamageEffect":
        allowed = {"closed", "remaining_capacity_per_window"}
        unknown = set(raw) - allowed
        if unknown:
            _fail("effect", f"unknown capacity effect fields: {sorted(unknown)}")
        closed = raw.get("closed", False)
        if not isinstance(closed, bool):
            _fail("effect.closed", "closed must be boolean")
        remaining = raw.get("remaining_capacity_per_window")
        if closed:
            if remaining not in (None, 0):
                _fail("effect.remaining_capacity_per_window", "closed capacity damage must have remaining capacity 0/null")
            return cls(0)
        if remaining is None:
            _fail("effect.remaining_capacity_per_window", "remaining capacity is required when closed=false")
        return cls(_nonnegative_int(remaining, "effect.remaining_capacity_per_window"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "closed": self.remaining_capacity_per_window == 0,
            "remaining_capacity_per_window": self.remaining_capacity_per_window,
        }


@dataclass(frozen=True)
class NavigationDelayEffect:
    departure_delay_slots: int
    return_delay_slots: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "NavigationDelayEffect":
        allowed = {"departure_delay_slots", "return_delay_slots"}
        unknown = set(raw) - allowed
        if unknown:
            _fail("effect", f"unknown navigation effect fields: {sorted(unknown)}")
        out = _nonnegative_int(raw.get("departure_delay_slots", 0), "effect.departure_delay_slots")
        ret = _nonnegative_int(raw.get("return_delay_slots", 0), "effect.return_delay_slots")
        if out == 0 and ret == 0:
            _fail("effect", "navigation_delay must add departure and/or return delay")
        return cls(out, ret)

    def to_dict(self) -> Dict[str, Any]:
        return {"departure_delay_slots": self.departure_delay_slots, "return_delay_slots": self.return_delay_slots}


DamageEffect = Union[AircraftDamageEffect, ResourceDamageEffect, CapacityDamageEffect, NavigationDelayEffect]


def _effect_from_mapping(damage_type: str, raw: Any) -> DamageEffect:
    if not isinstance(raw, Mapping):
        _fail("effect", "effect must be an object")
    if damage_type == "aircraft_damage":
        return AircraftDamageEffect.from_mapping(raw)
    if damage_type == "resource_damage":
        return ResourceDamageEffect.from_mapping(raw)
    if damage_type == "capacity_damage":
        return CapacityDamageEffect.from_mapping(raw)
    if damage_type == "navigation_delay":
        return NavigationDelayEffect.from_mapping(raw)
    _fail("damage_type", f"unsupported damage_type: {damage_type}")


@dataclass(frozen=True)
class DamageEvent:
    """One ordered Situation damage event using half-open [start_slot,end_slot)."""

    event_id: str
    sequence: int
    target: DamageTarget
    damage_type: str
    start_slot: int
    end_slot: int
    effect: DamageEffect
    recovery_mode: str
    recovery_duration_slots: Optional[int] = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DamageEvent":
        if not isinstance(raw, Mapping):
            _fail("damage_event", "damage event must be an object")
        allowed = {
            "event_id", "sequence", "target", "damage_type", "start_slot", "end_slot",
            "effect", "recovery_mode", "recovery_duration_slots",
        }
        unknown = set(raw) - allowed
        if unknown:
            _fail("damage_event", f"unknown damage event fields: {sorted(unknown)}")
        damage_type = raw.get("damage_type")
        if damage_type not in DAMAGE_TYPES:
            _fail("damage_type", f"damage_type must be one of {sorted(DAMAGE_TYPES)}")
        recovery_mode = raw.get("recovery_mode")
        if recovery_mode not in RECOVERY_MODES:
            _fail("recovery_mode", f"recovery_mode must be one of {sorted(RECOVERY_MODES)}")
        duration_raw = raw.get("recovery_duration_slots")
        duration = None if duration_raw is None else _positive_int(duration_raw, "recovery_duration_slots")
        return cls(
            event_id=_id(raw.get("event_id"), "event_id"),
            sequence=_nonnegative_int(raw.get("sequence"), "sequence"),
            target=DamageTarget.from_mapping(raw.get("target")),
            damage_type=str(damage_type),
            start_slot=_nonnegative_int(raw.get("start_slot"), "start_slot"),
            end_slot=_positive_int(raw.get("end_slot"), "end_slot"),
            effect=_effect_from_mapping(str(damage_type), raw.get("effect")),
            recovery_mode=str(recovery_mode),
            recovery_duration_slots=duration,
        )

    def __post_init__(self) -> None:
        _id(self.event_id, "event_id")
        _nonnegative_int(self.sequence, "sequence")
        if self.damage_type not in DAMAGE_TYPES:
            _fail("damage_type", f"damage_type must be one of {sorted(DAMAGE_TYPES)}")
        if self.end_slot <= self.start_slot:
            _fail("end_slot", "end_slot must be greater than start_slot for [start,end)")
        if self.damage_type == "aircraft_damage":
            if not isinstance(self.effect, AircraftDamageEffect):
                _fail("effect", "aircraft_damage requires AircraftDamageEffect")
            if self.target.target_type != "airport":
                _fail("target.target_type", "aircraft_damage targets an airport")
            if self.recovery_mode != "none" or self.recovery_duration_slots is not None:
                _fail("recovery_mode", "aircraft damage is a one-time non-recovering loss")
        else:
            if self.recovery_mode == "none":
                _fail("recovery_mode", "non-aircraft damage must use instant or average recovery")
            if self.recovery_mode == "average" and self.recovery_duration_slots is None:
                _fail("recovery_duration_slots", "average recovery requires recovery_duration_slots")
            if self.recovery_mode == "instant" and self.recovery_duration_slots is not None:
                _fail("recovery_duration_slots", "instant recovery must not specify recovery_duration_slots")
        if self.damage_type == "resource_damage" and self.target.target_type != "airport":
            _fail("target.target_type", "resource_damage targets an airport")
        if self.damage_type == "capacity_damage" and self.target.target_type not in {"airport", "runway"}:
            _fail("target.target_type", "capacity_damage targets an airport or runway")
        if self.damage_type == "navigation_delay" and self.target.target_type not in {"airport", "support_element"}:
            _fail("target.target_type", "navigation_delay targets an airport or support element")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "target": self.target.to_dict(),
            "damage_type": self.damage_type,
            "start_slot": self.start_slot,
            "end_slot": self.end_slot,
            "effect": self.effect.to_dict(),
            "recovery_mode": self.recovery_mode,
            "recovery_duration_slots": self.recovery_duration_slots,
        }


@dataclass(frozen=True)
class DamageScenario:
    damage_scenario_id: str
    name: str
    category: str
    events: Tuple[DamageEvent, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DamageScenario":
        if not isinstance(raw, Mapping):
            _fail("damage_scenario", "damage scenario must be an object")
        allowed = {"damage_scenario_id", "name", "category", "events"}
        unknown = set(raw) - allowed
        if unknown:
            _fail("damage_scenario", f"unknown damage scenario fields: {sorted(unknown)}")
        category = raw.get("category")
        if category not in DAMAGE_SCENARIO_CATEGORIES:
            _fail("category", f"category must be one of {sorted(DAMAGE_SCENARIO_CATEGORIES)}")
        events_raw = raw.get("events", [])
        if not isinstance(events_raw, list):
            _fail("events", "events must be an array")
        return cls(
            damage_scenario_id=_id(raw.get("damage_scenario_id"), "damage_scenario_id"),
            name=_string(raw.get("name"), "name"),
            category=str(category),
            events=tuple(DamageEvent.from_mapping(v) for v in events_raw),
        )

    def __post_init__(self) -> None:
        ids = [e.event_id for e in self.events]
        seq = [e.sequence for e in self.events]
        if len(ids) != len(set(ids)):
            _fail("events", "event_id values must be unique within a damage scenario")
        if len(seq) != len(set(seq)):
            _fail("events", "event sequence values must be unique within a damage scenario")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "damage_scenario_id": self.damage_scenario_id,
            "name": self.name,
            "category": self.category,
            "events": [e.to_dict() for e in sorted(self.events, key=lambda x: (x.sequence, x.event_id))],
        }
