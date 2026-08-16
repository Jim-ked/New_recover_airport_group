from __future__ import annotations

import unittest

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.mission import Mission
from backend.domain.situation import Situation, SituationAirport
from backend.services.od_distance_service import (
    ODDistanceService,
    ODDistanceServiceError,
    vincenty_distance_km,
)


class ODDistanceServiceTests(unittest.TestCase):
    def test_vincenty_preserves_old_wgs84_distance_semantics(self):
        distance = vincenty_distance_km(31.7, 118.8, 32.0, 120.0)
        self.assertAlmostEqual(118.2362089185, distance, places=6)
        self.assertEqual(0.0, vincenty_distance_km(31.7, 118.8, 31.7, 118.8))

    def test_nonconvergent_antipodal_case_fails_instead_of_using_last_iterate(self):
        with self.assertRaises(ODDistanceServiceError):
            vincenty_distance_km(0.0, 0.0, 0.0, 180.0)

    def test_build_for_situation_returns_complete_sorted_cross_product(self):
        def airport(aid, lon):
            return SituationAirport(
                airport=AirportBase.from_mapping({
                    "airport_id": aid,
                    "airport_name": aid,
                    "facility_type": "medium_airport",
                    "role": "joint",
                    "longitude": lon,
                    "latitude": 31.7,
                    "scheduled_service": True,
                    "runway_count": 0, "max_runway_length_m": None,
                    "runways": [],
                }),
                operational_profile=AirportOperationalProfile(
                    airport_id=aid,
                    configuration_complete=True,
                    capacity_per_window=1,
                    support_level="L1",
                ),
            )

        situation = Situation(
            situation_id="S1",
            name="S",
            airports=(airport("A2", 119.0), airport("A1", 118.8)),
            missions=(
                Mission("M2", "M2", 120.5, 32.0, 1, 2),
                Mission("M1", "M1", 120.0, 32.0, 1, 2),
            ),
        )
        rows = ODDistanceService().build_for_situation(situation)
        self.assertEqual(
            [("A1", "M1"), ("A1", "M2"), ("A2", "M1"), ("A2", "M2")],
            [(r.airport_id, r.mission_id) for r in rows],
        )
        self.assertTrue(all(r.distance_km > 0 for r in rows))


if __name__ == "__main__":
    unittest.main()
