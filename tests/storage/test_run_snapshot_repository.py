from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportAircraftSupport, AirportOperationalProfile, AirportResourceStock
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.domain.run_config import RunConfig
from backend.domain.run_snapshot import ODDistance, RunSnapshot
from backend.domain.situation import Situation, SituationAirport
from backend.storage.run_snapshot_repository import RunSnapshotConflictError, RunSnapshotRepository


class RunSnapshotRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = RunSnapshotRepository(Path(self.tmp.name) / "test.sqlite")
        self.repo.init_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _snapshot(self, *, run_id="R1", mode="sortie_max") -> RunSnapshot:
        ap = AirportBase.from_mapping({
            "airport_id": "A1", "airport_name": "Airport", "facility_type": "small_airport",
            "role": "military", "longitude": 110, "latitude": 30,
            "scheduled_service": False, "runway_count": 0, "max_runway_length_m": None, "runways": [],
        })
        op = AirportOperationalProfile(
            airport_id="A1", configuration_complete=True, capacity_per_window=4, support_level="L1",
            aircraft_support=(AirportAircraftSupport("fighter", 2, 1),),
            resource_stocks=(AirportResourceStock("FUEL-1", 10, 0),),
        )
        mission = Mission("M1", "M", 111, 31, 1, 4, (MissionAircraftRequirement("fighter", 1, 1),))
        situation = Situation.create(situation_id="S1", name="S").with_airport(
            SituationAirport(ap, op)
        ).with_mission(mission)
        config = RunConfig.from_mapping({
            "damage_scenario_id": None, "preference_mode": mode,
            "cluster_enabled": False, "cluster_size": None, "core_airports": [],
            "aircraft_type_weight": {}, "mip_time_limit_s": 120,
        })
        return RunSnapshot.build(
            run_id=run_id, situation=situation,
            aircraft_types=[AircraftType("fighter", "F", 800, 1000, 0.2, 1.0, 1.0)],
            resource_types=[ResourceType("FUEL-1", "Fuel", "fuel", "t")],
            aircraft_resource_requirements=[AircraftResourceRequirement("fighter", "FUEL-1", "per_hour", 1)],
            od_distances=[ODDistance("A1", "M1", 100)], run_config=config,
        )

    def test_insert_and_round_trip_exact_snapshot(self) -> None:
        snap = self._snapshot()
        self.repo.save_new(snap)
        got = self.repo.get("R1")
        self.assertIsNotNone(got)
        self.assertEqual(snap.content_hash, got.content_hash)
        self.assertEqual(snap.payload_json, got.payload_json)

    def test_snapshot_is_insert_only_and_cannot_be_overwritten(self) -> None:
        self.repo.save_new(self._snapshot(mode="sortie_max"))
        with self.assertRaises(RunSnapshotConflictError):
            self.repo.save_new(self._snapshot(mode="time_min"))
        self.assertEqual("sortie_max", self.repo.get("R1").to_dict()["run_config"]["preference_mode"])


if __name__ == "__main__":
    unittest.main()
