from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from backend.domain.airport_operations import AirportOperationalProfile
from backend.storage.airport_repository import AirportRepository


class WorkspaceAirportRepository(AirportRepository):
    """Airport adapter for the interactive catalog/Situation boundary.

    Static airport facts may exist before reusable operational data has been configured.
    Such an airport is selectable into a Situation as an explicitly incomplete operational
    profile. No capacity, aircraft quantity, reset time, stock, or replenishment value is
    invented.

    Base Data detail must still distinguish "no operational profile exists" from an
    existing incomplete profile, so get_airport_bundle preserves that truth.
    """

    def __init__(self, db_path: str | Path):
        super().__init__(db_path)

    def get_operational_profile(self, airport_id: str) -> AirportOperationalProfile:
        try:
            return AirportRepository.get_operational_profile(self, airport_id)
        except KeyError:
            # Confirm the static airport itself exists.
            AirportRepository.get_airport(self, airport_id)
            return AirportOperationalProfile(
                airport_id=airport_id,
                configuration_complete=False,
                capacity_per_window=None,
                support_level=None,
                aircraft_support=(),
                resource_stocks=(),
            )

    @staticmethod
    def _airport_summary_row(row) -> Dict[str, Any]:
        result = AirportRepository._airport_summary_row(row)
        # List endpoints need static airports to be selectable in Situation. Detail
        # endpoints still preserve "profile missing" via get_airport_bundle below.
        if result.get("configuration_complete") is None:
            result["configuration_complete"] = False
        return result

    def get_airport_bundle(self, airport_id: str) -> Dict[str, Any]:
        airport = AirportRepository.get_airport(self, airport_id)
        try:
            profile = AirportRepository.get_operational_profile(self, airport_id)
        except KeyError:
            profile = None
        meta = AirportRepository.get_airport_metadata(self, airport_id)
        if meta is None:
            raise KeyError(f"airport not found: {airport_id}")
        return {
            "airport": airport.to_dict(),
            "operational_profile": None if profile is None else profile.to_dict(),
            "metadata": meta,
        }


__all__ = ["WorkspaceAirportRepository"]
