from __future__ import annotations

from typing import Any, Mapping

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.damage import DamageScenario
from backend.domain.mission import Mission
from backend.domain.situation import ResourceReplenishment, Situation, SituationAirport


class SnapshotMaterializationError(ValueError):
    pass


def materialize_situation(raw: Mapping[str, Any]) -> Situation:
    """Rebuild one canonical Situation from a frozen RunSnapshot payload section.

    The function only uses canonical domain parsers. It performs no alias handling,
    no file reads and no repair of historical input.
    """
    if not isinstance(raw, Mapping):
        raise SnapshotMaterializationError("snapshot situation must be an object")
    try:
        airports = tuple(
            SituationAirport(
                airport=AirportBase.from_mapping(item["airport"]),
                operational_profile=AirportOperationalProfile.from_mapping(item["operational_profile"]),
                resource_replenishments=tuple(
                    ResourceReplenishment.from_mapping(row, index=i)
                    for i, row in enumerate(item.get("resource_replenishments", []))
                ),
            )
            for item in raw.get("airports", [])
        )
        missions = tuple(Mission.from_mapping(item) for item in raw.get("missions", []))
        scenarios = tuple(DamageScenario.from_mapping(item) for item in raw.get("damage_scenarios", []))
        return Situation(
            situation_id=raw["situation_id"],
            name=raw["name"],
            description=raw.get("description"),
            airports=airports,
            missions=missions,
            damage_scenarios=scenarios,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotMaterializationError(f"invalid frozen Situation: {exc}") from exc


__all__ = ["SnapshotMaterializationError", "materialize_situation"]
