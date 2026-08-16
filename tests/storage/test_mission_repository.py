from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.domain.catalog import AircraftType
from backend.domain.mission import Mission
from backend.domain.situation import Situation
from backend.services.situation_service import copy_mission_into_situation
from backend.storage.airport_repository import AirportRepository
from backend.storage.mission_repository import MissionRepository


class MissionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "app.db"
        self.airports = AirportRepository(self.db)
        self.airports.init_schema()
        self.airports.save_aircraft_type(AircraftType.from_mapping({
            "aircraft_type_id": "fighter", "name": "Fighter",
            "speed_kmh": 800, "max_range_km": 1200, "reserve_ratio": 0.2,
            "departure_capacity_occupancy_factor": 1.0,
            "arrival_capacity_occupancy_factor": 1.0,
        }))
        self.repo = MissionRepository(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _mission(self, name="M"):
        return Mission.from_mapping({
            "mission_id": "M1", "name": name, "longitude": 120, "latitude": 31,
            "window_start_slot": 4, "window_end_slot": 10,
            "aircraft_requirements": [
                {"aircraft_type_id": "fighter", "required_sorties": 3, "tau_work_windows": 2}
            ],
        })

    def test_reusable_mission_record_roundtrip_and_update(self):
        self.repo.save(self._mission("Old"))
        self.assertEqual("Old", self.repo.get("M1").name)
        self.repo.save(self._mission("New"))
        self.assertEqual("New", self.repo.get("M1").name)
        self.assertEqual(["M1"], [m.mission_id for m in self.repo.list()])

    def test_unknown_aircraft_reference_fails_fast(self):
        bad = Mission.from_mapping({
            "mission_id": "M2", "name": "Bad", "longitude": 120, "latitude": 31,
            "window_start_slot": 4, "window_end_slot": 10,
            "aircraft_requirements": [
                {"aircraft_type_id": "unknown", "required_sorties": 1, "tau_work_windows": 1}
            ],
        })
        with self.assertRaises(Exception):
            self.repo.save(bad)

    def test_library_update_and_delete_do_not_change_situation_copy(self):
        self.repo.save(self._mission("Original"))
        situation = copy_mission_into_situation(
            Situation.create(situation_id="S1", name="S"),
            self.repo.get("M1"),
        )

        self.repo.save(self._mission("Changed Library"))
        self.assertEqual("Original", situation.missions[0].name)
        self.assertEqual("Changed Library", self.repo.get("M1").name)

        self.repo.delete("M1")
        with self.assertRaises(KeyError):
            self.repo.get("M1")
        self.assertEqual("Original", situation.missions[0].name)


if __name__ == "__main__":
    unittest.main()
