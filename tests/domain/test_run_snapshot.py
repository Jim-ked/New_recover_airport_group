from __future__ import annotations

import unittest

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportAircraftSupport, AirportOperationalProfile, AirportResourceStock
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.damage import DamageScenario
from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.domain.run_config import RunConfig
from backend.domain.run_snapshot import ODDistance, RunSnapshot, RunSnapshotValidationError
from backend.domain.situation import Situation, SituationAirport


class RunSnapshotTests(unittest.TestCase):
    def _situation(self, *, complete: bool = True) -> Situation:
        ap = AirportBase.from_mapping({
            "airport_id": "A1", "airport_name": "Airport", "facility_type": "medium_airport",
            "role": "joint", "longitude": 118.8, "latitude": 31.7,
            "scheduled_service": True, "runway_count": 0, "max_runway_length_m": None, "runways": [],
        })
        op = AirportOperationalProfile(
            airport_id="A1", configuration_complete=complete,
            capacity_per_window=8 if complete else None, support_level="L1" if complete else None,
            aircraft_support=(AirportAircraftSupport("fighter", 3 if complete else None, 2 if complete else None),),
            resource_stocks=(AirportResourceStock("FUEL-1", 50 if complete else None, 0 if complete else None),),
        )
        mission = Mission(
            mission_id="M1", name="Mission", longitude=120.0, latitude=32.0,
            window_start_slot=4, window_end_slot=10,
            aircraft_requirements=(MissionAircraftRequirement("fighter", 2, 1),),
        )
        return Situation.create(situation_id="S1", name="S").with_airport(
            SituationAirport(ap, op)
        ).with_mission(mission)

    def _catalogs(self):
        aircraft = [AircraftType.from_mapping({
            "aircraft_type_id": "fighter", "name": "Fighter", "speed_kmh": 800,
            "max_range_km": 1000, "reserve_ratio": 0.2,
            "departure_capacity_occupancy_factor": 1.0,
            "arrival_capacity_occupancy_factor": 0.9,
        })]
        resources = [ResourceType("FUEL-1", "Fuel", "fuel", "t")]
        reqs = [AircraftResourceRequirement("fighter", "FUEL-1", "per_hour", 1.5)]
        return aircraft, resources, reqs

    def _config(self):
        return RunConfig.from_mapping({
            "damage_scenario_id": None,
            "preference_mode": "sortie_max",
            "cluster_enabled": False,
            "cluster_size": None,
            "core_airports": [],
            "aircraft_type_weight": {"fighter": 1.1},
            "mip_time_limit_s": 120,
        })

    def test_snapshot_is_complete_json_closure_and_deeply_immutable(self) -> None:
        aircraft, resources, reqs = self._catalogs()
        snap = RunSnapshot.build(
            run_id="R1", situation=self._situation(), aircraft_types=aircraft,
            resource_types=resources, aircraft_resource_requirements=reqs,
            od_distances=[ODDistance("A1", "M1", 321.5)], run_config=self._config(),
        )
        payload = snap.to_dict()
        self.assertEqual("sortie_max", payload["run_config"]["preference_mode"])
        self.assertEqual([0.8, 0.1, 0.1], payload["run_config"]["alpha"])
        self.assertEqual(1000, payload["catalogs"]["aircraft_types"][0]["max_range_km"])
        self.assertEqual(0.2, payload["catalogs"]["aircraft_types"][0]["reserve_ratio"])
        self.assertEqual(1.0, payload["catalogs"]["aircraft_types"][0]["departure_capacity_occupancy_factor"])
        self.assertEqual(0.9, payload["catalogs"]["aircraft_types"][0]["arrival_capacity_occupancy_factor"])
        payload["situation"]["name"] = "changed"
        self.assertEqual("S", snap.to_dict()["situation"]["name"])

    def test_snapshot_requires_complete_airport_configuration(self) -> None:
        aircraft, resources, reqs = self._catalogs()
        with self.assertRaises(RunSnapshotValidationError) as ctx:
            RunSnapshot.build(
                run_id="R1", situation=self._situation(complete=False), aircraft_types=aircraft,
                resource_types=resources, aircraft_resource_requirements=reqs,
                od_distances=[ODDistance("A1", "M1", 1)], run_config=self._config(),
            )
        self.assertIn("operational_profile", ctx.exception.field)

    def test_snapshot_requires_complete_od_cross_product(self) -> None:
        aircraft, resources, reqs = self._catalogs()
        with self.assertRaises(RunSnapshotValidationError) as ctx:
            RunSnapshot.build(
                run_id="R1", situation=self._situation(), aircraft_types=aircraft,
                resource_types=resources, aircraft_resource_requirements=reqs,
                od_distances=[], run_config=self._config(),
            )
        self.assertEqual("od_distances", ctx.exception.field)

    def test_snapshot_requires_all_referenced_catalog_rows(self) -> None:
        _, resources, reqs = self._catalogs()
        with self.assertRaises(RunSnapshotValidationError) as ctx:
            RunSnapshot.build(
                run_id="R1", situation=self._situation(), aircraft_types=[],
                resource_types=resources, aircraft_resource_requirements=reqs,
                od_distances=[ODDistance("A1", "M1", 1)], run_config=self._config(),
            )
        self.assertEqual("aircraft_types", ctx.exception.field)

    def test_snapshot_rejects_used_aircraft_with_missing_operational_parameter(self) -> None:
        _, resources, reqs = self._catalogs()
        incomplete = [AircraftType.from_mapping({
            "aircraft_type_id": "fighter", "name": "Fighter", "speed_kmh": 800,
            "max_range_km": 1000, "reserve_ratio": 0.2,
            "departure_capacity_occupancy_factor": 1.0,
        })]
        with self.assertRaises(RunSnapshotValidationError) as ctx:
            RunSnapshot.build(
                run_id="R1", situation=self._situation(), aircraft_types=incomplete,
                resource_types=resources, aircraft_resource_requirements=reqs,
                od_distances=[ODDistance("A1", "M1", 1)], run_config=self._config(),
            )
        self.assertIn("arrival_capacity_occupancy_factor", ctx.exception.field)

    def test_snapshot_freezes_selected_damage_scenario_and_later_situation_changes_do_not_propagate(self) -> None:
        aircraft, resources, reqs = self._catalogs()
        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1",
            "name": "Damage 1",
            "category": "custom",
            "events": [
                {
                    "event_id": "E1", "sequence": 0,
                    "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                    "damage_type": "aircraft_damage",
                    "start_slot": 5, "end_slot": 6,
                    "effect": {"aircraft_loss": {"fighter": 1}},
                    "recovery_mode": "none", "recovery_duration_slots": None,
                }
            ],
        })
        situation = self._situation().with_damage_scenario(scenario)
        config = RunConfig.from_mapping({
            "damage_scenario_id": "DS1", "preference_mode": "resource_min",
            "cluster_enabled": False, "cluster_size": None, "core_airports": [],
            "aircraft_type_weight": {"fighter": 1.0}, "mip_time_limit_s": 90,
        })
        snap = RunSnapshot.build(
            run_id="R-DS", situation=situation, aircraft_types=aircraft,
            resource_types=resources, aircraft_resource_requirements=reqs,
            od_distances=[ODDistance("A1", "M1", 100)], run_config=config,
        )

        changed = situation.without_damage_scenario("DS1")
        self.assertEqual(0, len(changed.damage_scenarios))
        payload = snap.to_dict()
        self.assertEqual("DS1", payload["run_config"]["damage_scenario_id"])
        self.assertEqual("DS1", payload["situation"]["damage_scenarios"][0]["damage_scenario_id"])
        self.assertEqual("aircraft_damage", payload["situation"]["damage_scenarios"][0]["events"][0]["damage_type"])

    def test_snapshot_rejects_run_config_selecting_unknown_damage_scenario(self) -> None:
        aircraft, resources, reqs = self._catalogs()
        config = RunConfig.from_mapping({
            "damage_scenario_id": "MISSING", "preference_mode": "sortie_max",
            "cluster_enabled": False, "cluster_size": None, "core_airports": [],
            "aircraft_type_weight": {}, "mip_time_limit_s": 120,
        })
        with self.assertRaisesRegex(ValueError, "selected damage_scenario_id is not in Situation"):
            RunSnapshot.build(
                run_id="R1", situation=self._situation(), aircraft_types=aircraft,
                resource_types=resources, aircraft_resource_requirements=reqs,
                od_distances=[ODDistance("A1", "M1", 1)], run_config=config,
            )


if __name__ == "__main__":
    unittest.main()
