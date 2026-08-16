from __future__ import annotations

import unittest

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.situation import Situation
from backend.services.situation_service import copy_airport_into_situation


class SituationServiceTests(unittest.TestCase):
    def test_explicit_copy_freezes_values_into_working_copy(self) -> None:
        ap = AirportBase.from_mapping({
            "airport_id": "A1", "airport_name": "Original", "facility_type": "small_airport", "role": "civil",
            "longitude": 120, "latitude": 30, "scheduled_service": False,
            "icao_code": None, "iata_code": None, "region": None, "municipality": None,
            "elevation_m": None, "runway_count": None, "max_runway_length_m": None, "runways": None,
        })
        profile = AirportOperationalProfile.from_mapping({
            "airport_id": "A1", "configuration_complete": True, "capacity_per_window": 8,
            "aircraft_support": [], "resource_stocks": [],
        })
        s = copy_airport_into_situation(Situation.create(situation_id="S1", name="S"), ap, profile)

        newer_ap = AirportBase.from_mapping({**ap.to_dict(), "airport_name": "Changed Base"})
        newer_profile = AirportOperationalProfile.from_mapping({**profile.to_dict(), "capacity_per_window": 12})
        self.assertEqual("Original", s.airports[0].airport.airport_name)
        self.assertEqual(8, s.airports[0].operational_profile.capacity_per_window)

        s2 = copy_airport_into_situation(s, newer_ap, newer_profile)
        self.assertEqual("Changed Base", s2.airports[0].airport.airport_name)
        self.assertEqual(12, s2.airports[0].operational_profile.capacity_per_window)


if __name__ == "__main__":
    unittest.main()
