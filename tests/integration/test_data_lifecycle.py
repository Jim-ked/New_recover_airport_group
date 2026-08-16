from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import (
    AirportAircraftSupport,
    AirportOperationalProfile,
    AirportResourceStock,
)
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.domain.damage import DamageScenario
from backend.domain.run_snapshot import ODDistance
from backend.domain.situation import Situation
from backend.services.run_snapshot_service import RunSnapshotService
from backend.services.situation_service import copy_airport_into_situation
from backend.storage.airport_repository import AirportRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository


class DataLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "application.db"
        self.airports = AirportRepository(self.db_path)
        self.situations = SituationRepository(self.db_path)
        self.snapshots = RunSnapshotRepository(self.db_path)
        # Any repository may initialize the one shared schema authority.
        self.airports.init_schema()

        self.airports.save_aircraft_type(AircraftType.from_mapping({
            "aircraft_type_id": "fighter",
            "name": "Fighter",
            "speed_kmh": 800,
            "max_range_km": 1200,
            "reserve_ratio": 0.2,
            "departure_capacity_occupancy_factor": 1.0,
            "arrival_capacity_occupancy_factor": 1.0,
        }))
        self.airports.save_resource_type(ResourceType.from_mapping({
            "resource_type_id": "FUEL-1",
            "name": "Fuel",
            "category": "fuel",
            "unit": "t",
        }))
        self.airports.save_aircraft_resource_requirement(AircraftResourceRequirement.from_mapping({
            "aircraft_type_id": "fighter",
            "resource_type_id": "FUEL-1",
            "basis": "per_hour",
            "quantity": 1.5,
        }))

        self.base_airport = AirportBase.from_mapping({
            "airport_id": "A1",
            "airport_name": "Base Airport",
            "facility_type": "medium_airport",
            "role": "joint",
            "longitude": 118.8,
            "latitude": 31.7,
            "scheduled_service": True,
            "runway_count": 0,
            "max_runway_length_m": None,
            "runways": [],
        })
        self.base_profile = AirportOperationalProfile(
            airport_id="A1",
            configuration_complete=True,
            capacity_per_window=8,
            support_level="L1",
            aircraft_support=(AirportAircraftSupport("fighter", 3, 2),),
            resource_stocks=(AirportResourceStock("FUEL-1", 50, 0),),
        )
        self.airports.save_airport(self.base_airport)
        self.airports.save_operational_profile(self.base_profile)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _saved_situation(self) -> Situation:
        working = Situation.create(situation_id="S1", name="Situation 1")
        working = copy_airport_into_situation(working, self.base_airport, self.base_profile)
        working = working.with_mission(Mission(
            mission_id="M1",
            name="Mission 1",
            longitude=120.0,
            latitude=32.0,
            window_start_slot=4,
            window_end_slot=10,
            aircraft_requirements=(MissionAircraftRequirement("fighter", 2, 1),),
        ))
        self.situations.save_situation(working, owner_user_id="u1")
        return working

    def test_static_to_situation_to_run_snapshot_isolation(self) -> None:
        original = self._saved_situation()
        service = RunSnapshotService(
            airport_repository=self.airports,
            situation_repository=self.situations,
            snapshot_repository=self.snapshots,
        )
        snapshot = service.create_snapshot(
            run_id="R1",
            situation_id="S1",
            run_config={
                "damage_scenario_id": None,
                "preference_mode": "sortie_max",
                "cluster_enabled": False,
                "cluster_size": None,
                "core_airports": [],
                "aircraft_type_weight": {"fighter": 1.0},
                "mip_time_limit_s": 120,
            },
            od_distances=[ODDistance("A1", "M1", 321.5)],
        )
        original_snapshot_json = snapshot.payload_json

        # Later base changes must not alter the already-saved Situation copy.
        changed_base = AirportBase.from_mapping({
            **self.base_airport.to_dict(),
            "airport_name": "Base Airport Changed Later",
        })
        changed_profile = AirportOperationalProfile(
            airport_id="A1",
            configuration_complete=True,
            capacity_per_window=99,
            support_level="L9",
            aircraft_support=(AirportAircraftSupport("fighter", 20, 5),),
            resource_stocks=(AirportResourceStock("FUEL-1", 999, 0),),
        )
        self.airports.save_airport(changed_base)
        self.airports.save_operational_profile(changed_profile)
        saved_after_base_change = self.situations.get_situation("S1")
        self.assertEqual(original.to_dict(), saved_after_base_change.to_dict())

        # Later Situation save must not alter the already-created RunSnapshot.
        edited = saved_after_base_change.with_mission(Mission(
            mission_id="M1",
            name="Mission Changed Later",
            longitude=120.0,
            latitude=32.0,
            window_start_slot=5,
            window_end_slot=11,
            aircraft_requirements=(MissionAircraftRequirement("fighter", 1, 1),),
        ))
        self.situations.save_situation(edited, owner_user_id="u1")

        frozen = self.snapshots.get("R1")
        self.assertIsNotNone(frozen)
        self.assertEqual(original_snapshot_json, frozen.payload_json)
        payload = frozen.to_dict()
        self.assertEqual("Base Airport", payload["situation"]["airports"][0]["airport"]["airport_name"])
        self.assertEqual(8, payload["situation"]["airports"][0]["operational_profile"]["capacity_per_window"])
        self.assertEqual("Mission 1", payload["situation"]["missions"][0]["name"])
        self.assertEqual(1200, payload["catalogs"]["aircraft_types"][0]["max_range_km"])
        self.assertEqual(0.2, payload["catalogs"]["aircraft_types"][0]["reserve_ratio"])
        self.assertEqual("sortie_max", payload["run_config"]["preference_mode"])
        self.assertEqual([0.8, 0.1, 0.1], payload["run_config"]["alpha"])
        self.assertEqual({"fighter": 1.0}, payload["run_config"]["aircraft_type_weight"])
        self.assertEqual(original.content_hash(), payload["situation_content_hash"])

    def test_damage_scenario_storage_and_selected_run_snapshot_are_isolated(self) -> None:
        working = self._saved_situation()
        damage = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "Selected Damage", "category": "custom",
            "events": [
                {
                    "event_id": "E1", "sequence": 0,
                    "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                    "damage_type": "capacity_damage",
                    "start_slot": 5, "end_slot": 8,
                    "effect": {"remaining_capacity_per_window": 4},
                    "recovery_mode": "average", "recovery_duration_slots": 2,
                }
            ],
        })
        working = working.with_damage_scenario(damage)
        self.situations.save_situation(working, owner_user_id="u1")

        reloaded = self.situations.get_situation("S1")
        self.assertEqual("DS1", reloaded.damage_scenarios[0].damage_scenario_id)
        self.assertEqual(4, reloaded.damage_scenarios[0].events[0].effect.remaining_capacity_per_window)

        service = RunSnapshotService(
            airport_repository=self.airports,
            situation_repository=self.situations,
            snapshot_repository=self.snapshots,
        )
        snapshot = service.create_snapshot(
            run_id="R-DS", situation_id="S1",
            run_config={
                "damage_scenario_id": "DS1", "preference_mode": "time_min",
                "cluster_enabled": False, "cluster_size": None, "core_airports": [],
                "aircraft_type_weight": {}, "mip_time_limit_s": 60,
            },
            od_distances=[ODDistance("A1", "M1", 321.5)],
        )
        frozen_json = snapshot.payload_json

        # Editing/removing the scenario later only changes the current Situation.
        self.situations.save_situation(reloaded.without_damage_scenario("DS1"), owner_user_id="u1")
        self.assertEqual(0, len(self.situations.get_situation("S1").damage_scenarios))
        frozen = self.snapshots.get("R-DS")
        self.assertEqual(frozen_json, frozen.payload_json)
        self.assertEqual("DS1", frozen.to_dict()["run_config"]["damage_scenario_id"])
        self.assertEqual("DS1", frozen.to_dict()["situation"]["damage_scenarios"][0]["damage_scenario_id"])


if __name__ == "__main__":
    unittest.main()
