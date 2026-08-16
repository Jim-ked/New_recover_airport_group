from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, Mapping, Optional, Tuple, Union

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.damage import AircraftDamageEffect, DamageScenario, ResourceDamageEffect
from backend.domain.mission import Mission

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


JsonNumber = Union[int, float]


def _nonnegative_number(value: Any, field: str) -> JsonNumber:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(field, f"{field} must be a JSON number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        _fail(field, f"{field} must be a finite nonnegative number")
    return value


def _positive_number(value: Any, field: str) -> JsonNumber:
    out = _nonnegative_number(value, field)
    if float(out) <= 0:
        _fail(field, f"{field} must be greater than 0")
    return out


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(field, f"{field} must be a nonnegative integer")
    return value


class SituationValidationError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> None:
    raise SituationValidationError(message, field=field)


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _ID_RE.fullmatch(value):
        _fail(field, f"{field} must be a nonblank stable identifier")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(field, f"{field} must be a nonblank string")
    return value


def _optional_string(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(field, f"{field} must be a string or null")
    return value


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_json_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class ResourceReplenishment:
    """Actual exogenous replenishment arriving in one Situation time window."""

    resource_type_id: str
    slot: int
    quantity: JsonNumber

    def __post_init__(self) -> None:
        _id(self.resource_type_id, "resource_type_id")
        _nonnegative_int(self.slot, "slot")
        _positive_number(self.quantity, "quantity")

    @classmethod
    def from_mapping(cls, value: Dict[str, Any], *, index: int) -> "ResourceReplenishment":
        field = f"resource_replenishments[{index}]"
        if not isinstance(value, dict):
            _fail(field, f"{field} must be an object")
        allowed = {"resource_type_id", "slot", "quantity"}
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(f"{field}.{unknown[0]}", f"unknown field: {field}.{unknown[0]}")
        return cls(
            resource_type_id=_id(value.get("resource_type_id"), f"{field}.resource_type_id"),
            slot=_nonnegative_int(value.get("slot"), f"{field}.slot"),
            quantity=_positive_number(value.get("quantity"), f"{field}.quantity"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_type_id": self.resource_type_id,
            "slot": self.slot,
            "quantity": self.quantity,
        }


@dataclass(frozen=True)
class SituationAirport:
    airport: AirportBase
    operational_profile: AirportOperationalProfile
    resource_replenishments: Tuple[ResourceReplenishment, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, index: int) -> "SituationAirport":
        field = f"airports[{index}]"
        if not isinstance(value, Mapping):
            _fail(field, f"{field} must be an object")
        allowed = {"airport", "operational_profile", "resource_replenishments"}
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(f"{field}.{unknown[0]}", f"unknown field: {field}.{unknown[0]}")
        airport_raw = value.get("airport")
        profile_raw = value.get("operational_profile")
        if not isinstance(airport_raw, Mapping):
            _fail(f"{field}.airport", "airport must be an object")
        if not isinstance(profile_raw, Mapping):
            _fail(f"{field}.operational_profile", "operational_profile must be an object")
        raw_replenishments = value.get("resource_replenishments", [])
        if not isinstance(raw_replenishments, list):
            _fail(f"{field}.resource_replenishments", "resource_replenishments must be an array")
        # Child validators already reject aliases/unknown fields. Re-wrap their field paths
        # only at the Situation boundary by preserving the underlying precise field name.
        airport = AirportBase.from_mapping(airport_raw)
        profile = AirportOperationalProfile.from_mapping(profile_raw)
        replenishments = tuple(
            ResourceReplenishment.from_mapping(dict(item), index=i)
            for i, item in enumerate(raw_replenishments)
        )
        return cls(airport=airport, operational_profile=profile, resource_replenishments=replenishments)

    def __post_init__(self) -> None:
        if self.airport.airport_id != self.operational_profile.airport_id:
            _fail("operational_profile.airport_id", "airport snapshot and operational profile must use the same airport_id")

        stock_by_id = {row.resource_type_id: row for row in self.operational_profile.resource_stocks}
        seen = set()
        for i, item in enumerate(self.resource_replenishments):
            key = (item.resource_type_id, item.slot)
            if key in seen:
                _fail(
                    f"resource_replenishments[{i}]",
                    "resource_type_id + slot must be unique per Situation airport",
                )
            seen.add(key)
            stock = stock_by_id.get(item.resource_type_id)
            if stock is None:
                _fail(
                    f"resource_replenishments[{i}].resource_type_id",
                    "replenishment resource must be configured in the airport operational profile",
                )
            cap = stock.replenishment_capacity_per_window
            if cap is None:
                _fail(
                    f"resource_replenishments[{i}].quantity",
                    "cannot schedule replenishment while replenishment capacity is unknown",
                )
            if float(item.quantity) > float(cap) + 1e-9:
                _fail(
                    f"resource_replenishments[{i}].quantity",
                    "actual replenishment cannot exceed replenishment_capacity_per_window",
                )

    @property
    def airport_id(self) -> str:
        return self.airport.airport_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "airport": self.airport.to_dict(),
            "operational_profile": self.operational_profile.to_dict(),
            "resource_replenishments": [
                row.to_dict()
                for row in sorted(
                    self.resource_replenishments,
                    key=lambda x: (x.slot, x.resource_type_id),
                )
            ],
        }


@dataclass(frozen=True)
class Situation:
    situation_id: str
    name: str
    description: Optional[str] = None
    airports: Tuple[SituationAirport, ...] = ()
    missions: Tuple[Mission, ...] = ()
    damage_scenarios: Tuple[DamageScenario, ...] = ()

    @classmethod
    def create(cls, *, situation_id: str, name: str, description: Optional[str] = None) -> "Situation":
        return cls(
            situation_id=_id(situation_id, "situation_id"),
            name=_string(name, "name"),
            description=_optional_string(description, "description"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Situation":
        if not isinstance(value, Mapping):
            _fail("situation", "Situation must be an object")
        allowed = {"situation_id", "name", "description", "airports", "missions", "damage_scenarios"}
        unknown = [k for k in value if k not in allowed]
        if unknown:
            _fail(str(unknown[0]), f"unknown field: {unknown[0]}")
        raw_airports = value.get("airports", [])
        raw_missions = value.get("missions", [])
        raw_damage = value.get("damage_scenarios", [])
        if not isinstance(raw_airports, list):
            _fail("airports", "airports must be an array")
        if not isinstance(raw_missions, list):
            _fail("missions", "missions must be an array")
        if not isinstance(raw_damage, list):
            _fail("damage_scenarios", "damage_scenarios must be an array")
        return cls(
            situation_id=_id(value.get("situation_id"), "situation_id"),
            name=_string(value.get("name"), "name"),
            description=_optional_string(value.get("description"), "description"),
            airports=tuple(SituationAirport.from_mapping(item, index=i) for i, item in enumerate(raw_airports)),
            missions=tuple(Mission.from_mapping(item) for item in raw_missions),
            damage_scenarios=tuple(DamageScenario.from_mapping(item) for item in raw_damage),
        )

    def __post_init__(self) -> None:
        _id(self.situation_id, "situation_id")
        _string(self.name, "name")
        _optional_string(self.description, "description")
        airport_ids = [a.airport_id for a in self.airports]
        if len(airport_ids) != len(set(airport_ids)):
            _fail("airports", "airport_id values must be unique per situation")
        mission_ids = [m.mission_id for m in self.missions]
        if len(mission_ids) != len(set(mission_ids)):
            _fail("missions", "mission_id values must be unique per situation")
        scenario_ids = [s.damage_scenario_id for s in self.damage_scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            _fail("damage_scenarios", "damage_scenario_id values must be unique per situation")

        airport_by_id = {a.airport_id: a for a in self.airports}
        for scenario in self.damage_scenarios:
            for event in scenario.events:
                item = airport_by_id.get(event.target.airport_id)
                if item is None:
                    _fail("damage_scenarios.events.target.airport_id", "damage target airport must belong to the situation")
                if event.target.target_type == "runway":
                    runways = item.airport.runways
                    if runways is None:
                        _fail("damage_scenarios.events.target.target_id", "runway target cannot be validated when runway data is unknown")
                    if event.target.target_id not in {r.runway_id for r in runways}:
                        _fail("damage_scenarios.events.target.target_id", "runway target must exist in the situation airport snapshot")
                if isinstance(event.effect, AircraftDamageEffect):
                    supported = {r.aircraft_type_id for r in item.operational_profile.aircraft_support}
                    unknown = sorted({k for k, _ in event.effect.aircraft_loss} - supported)
                    if unknown:
                        _fail("damage_scenarios.events.effect.aircraft_loss", f"airport does not support damaged aircraft types: {unknown}")
                if isinstance(event.effect, ResourceDamageEffect):
                    configured = {r.resource_type_id for r in item.operational_profile.resource_stocks}
                    unknown = sorted({k for k, _ in event.effect.remaining_quantity} - configured)
                    if unknown:
                        _fail("damage_scenarios.events.effect.remaining_quantity", f"airport does not configure damaged resources: {unknown}")

    def with_airport(self, item: SituationAirport) -> "Situation":
        updated = [a for a in self.airports if a.airport_id != item.airport_id]
        updated.append(item)
        return replace(self, airports=tuple(updated))

    def without_airport(self, airport_id: str) -> "Situation":
        target = _id(airport_id, "airport_id")
        if any(e.target.airport_id == target for s in self.damage_scenarios for e in s.events):
            _fail("airport_id", "cannot remove airport while damage events still reference it")
        return replace(self, airports=tuple(a for a in self.airports if a.airport_id != target))

    def with_mission(self, mission: Mission) -> "Situation":
        updated = [m for m in self.missions if m.mission_id != mission.mission_id]
        updated.append(mission)
        return replace(self, missions=tuple(updated))

    def without_mission(self, mission_id: str) -> "Situation":
        target = _id(mission_id, "mission_id")
        return replace(self, missions=tuple(m for m in self.missions if m.mission_id != target))

    def with_damage_scenario(self, scenario: DamageScenario) -> "Situation":
        updated = [s for s in self.damage_scenarios if s.damage_scenario_id != scenario.damage_scenario_id]
        updated.append(scenario)
        return replace(self, damage_scenarios=tuple(updated))

    def without_damage_scenario(self, damage_scenario_id: str) -> "Situation":
        target = _id(damage_scenario_id, "damage_scenario_id")
        return replace(self, damage_scenarios=tuple(s for s in self.damage_scenarios if s.damage_scenario_id != target))

    def get_damage_scenario(self, damage_scenario_id: str) -> DamageScenario:
        target = _id(damage_scenario_id, "damage_scenario_id")
        for scenario in self.damage_scenarios:
            if scenario.damage_scenario_id == target:
                return scenario
        raise KeyError(target)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "situation_id": self.situation_id,
            "name": self.name,
            "description": self.description,
            "airports": [a.to_dict() for a in self.airports],
            "missions": [m.to_dict() for m in self.missions],
            "damage_scenarios": [s.to_dict() for s in self.damage_scenarios],
        }

    def canonical_dict(self) -> Dict[str, Any]:
        raw = self.to_dict()
        raw["airports"] = sorted(raw["airports"], key=lambda x: x["airport"]["airport_id"])
        raw["missions"] = sorted(raw["missions"], key=lambda x: x["mission_id"])
        raw["damage_scenarios"] = sorted(raw["damage_scenarios"], key=lambda x: x["damage_scenario_id"])
        return _canonical_json_value(raw)

    def content_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
