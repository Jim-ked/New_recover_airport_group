from __future__ import annotations

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.mission import Mission
from backend.domain.situation import Situation, SituationAirport


class SituationServiceError(ValueError):
    pass


def copy_airport_into_situation(
    situation: Situation,
    airport: AirportBase,
    operational_profile: AirportOperationalProfile,
) -> Situation:
    """Explicitly copy/restore current base values into the Situation working copy."""
    if airport.airport_id != operational_profile.airport_id:
        raise SituationServiceError("airport and operational profile airport_id must match")
    return situation.with_airport(
        SituationAirport(airport=airport, operational_profile=operational_profile)
    )


def copy_mission_into_situation(situation: Situation, mission_record: Mission) -> Situation:
    """Copy the current reusable MissionRecord value into the Situation Working Copy."""
    if not isinstance(mission_record, Mission):
        raise SituationServiceError("mission_record must be Mission")
    # Value-copy through the canonical mapping so the Situation owns an independent
    # Mission snapshot rather than a reference to a reusable library object.
    return situation.with_mission(Mission.from_mapping(mission_record.to_dict()))
