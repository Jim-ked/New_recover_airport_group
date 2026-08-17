from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.domain.airport import AirportBase
from backend.storage.workspace_airport_repository import WorkspaceAirportRepository


class WorkspaceAirportRepositoryTests(unittest.TestCase):
    def test_seeded_static_airport_is_addable_with_explicit_incomplete_profile(self):
        with tempfile.TemporaryDirectory() as td:
            repo = WorkspaceAirportRepository(Path(td) / "app.db")
            repo.init_schema()
            airport = AirportBase.from_mapping({
                "airport_id": "A1",
                "airport_name": "Airport 1",
                "facility_type": "small_airport",
                "role": "civil",
                "icao_code": None,
                "iata_code": None,
                "region": "R1",
                "municipality": "M1",
                "longitude": 118.0,
                "latitude": 34.0,
                "elevation_m": None,
                "scheduled_service": False,
                "runway_count": None,
                "max_runway_length_m": None,
                "runways": None,
            })
            repo.save_airport(airport)

            profile = repo.get_operational_profile("A1")
            self.assertFalse(profile.configuration_complete)
            self.assertIsNone(profile.capacity_per_window)

            rows, total = repo.list_airport_bundles(limit=20, offset=0)
            self.assertEqual(1, total)
            self.assertFalse(rows[0]["configuration_complete"])

    def test_unknown_airport_still_fails(self):
        with tempfile.TemporaryDirectory() as td:
            repo = WorkspaceAirportRepository(Path(td) / "app.db")
            repo.init_schema()
            with self.assertRaises(KeyError):
                repo.get_operational_profile("MISSING")


if __name__ == "__main__":
    unittest.main()
