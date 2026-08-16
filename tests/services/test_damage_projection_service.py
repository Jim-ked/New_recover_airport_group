from __future__ import annotations

import unittest

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportAircraftSupport, AirportOperationalProfile, AirportResourceStock
from backend.domain.damage import DamageScenario
from backend.domain.situation import Situation, SituationAirport
from backend.services.damage_projection_service import project_damage


class DamageProjectionServiceTests(unittest.TestCase):
    def _situation(self, scenario: DamageScenario) -> Situation:
        airport = AirportBase.from_mapping({
            "airport_id": "A1", "airport_name": "A", "facility_type": "small_airport", "role": "military",
            "longitude": 120, "latitude": 30, "scheduled_service": False,
            "runway_count": 0, "max_runway_length_m": None, "runways": [],
        })
        profile = AirportOperationalProfile(
            airport_id="A1", configuration_complete=True, capacity_per_window=10,
            aircraft_support=(AirportAircraftSupport("fighter", 5, 1),),
            resource_stocks=(AirportResourceStock("FUEL-1", 100, 0),),
        )
        return Situation(
            situation_id="S1", name="S",
            airports=(SituationAirport(airport, profile),),
            damage_scenarios=(scenario,),
        )

    def test_average_capacity_recovery_uses_integer_steps(self):
        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "X", "category": "custom",
            "events": [{
                "event_id": "C1", "sequence": 0,
                "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                "damage_type": "capacity_damage", "start_slot": 2, "end_slot": 4,
                "effect": {"remaining_capacity_per_window": 4},
                "recovery_mode": "average", "recovery_duration_slots": 3,
            }],
        })
        out = project_damage(self._situation(scenario), scenario, horizon_slots=8)
        self.assertEqual((10, 10, 4, 4, 6, 8, 10, 10), out.capacity_per_window["A1"])

    def test_instant_recovery_restores_pre_event_trajectory_not_base_reset(self):
        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "X", "category": "custom",
            "events": [
                {
                    "event_id": "C1", "sequence": 0,
                    "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                    "damage_type": "capacity_damage", "start_slot": 1, "end_slot": 3,
                    "effect": {"remaining_capacity_per_window": 4},
                    "recovery_mode": "average", "recovery_duration_slots": 4,
                },
                {
                    "event_id": "C2", "sequence": 1,
                    "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                    "damage_type": "capacity_damage", "start_slot": 4, "end_slot": 5,
                    "effect": {"remaining_capacity_per_window": 2},
                    "recovery_mode": "instant", "recovery_duration_slots": None,
                },
            ],
        })
        out = project_damage(self._situation(scenario), scenario, horizon_slots=8)
        # C1 trajectory is 4,4,5,7,8,10...; C2 only replaces slot 4 and then reveals
        # the pre-C2 trajectory again, rather than resetting all history to base.
        self.assertEqual((10, 4, 4, 5, 2, 8, 10, 10), out.capacity_per_window["A1"])

    def test_resource_recovery_is_external_boundary_and_aircraft_is_shock_only(self):
        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "X", "category": "custom",
            "events": [
                {
                    "event_id": "R1", "sequence": 0,
                    "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                    "damage_type": "resource_damage", "start_slot": 1, "end_slot": 3,
                    "effect": {"remaining_quantity": {"FUEL-1": 40}},
                    "recovery_mode": "average", "recovery_duration_slots": 3,
                },
                {
                    "event_id": "A1LOSS", "sequence": 1,
                    "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                    "damage_type": "aircraft_damage", "start_slot": 2, "end_slot": 4,
                    "effect": {"aircraft_loss": {"fighter": 2}},
                    "recovery_mode": "none", "recovery_duration_slots": None,
                },
            ],
        })
        out = project_damage(self._situation(scenario), scenario, horizon_slots=7)
        self.assertEqual((100.0, 40.0, 40.0, 60.0, 80.0, 100.0, 100.0), out.resource_available["A1"]["FUEL-1"])
        self.assertEqual(1, len(out.aircraft_loss_shocks))
        self.assertEqual((("fighter", 2),), out.aircraft_loss_shocks[0].aircraft_loss)

    def test_navigation_delay_can_recover_instantly(self):
        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "X", "category": "custom",
            "events": [{
                "event_id": "N1", "sequence": 0,
                "target": {"airport_id": "A1", "target_type": "support_element", "target_id": "RADAR"},
                "damage_type": "navigation_delay", "start_slot": 2, "end_slot": 4,
                "effect": {"departure_delay_slots": 2, "return_delay_slots": 1},
                "recovery_mode": "instant", "recovery_duration_slots": None,
            }],
        })
        out = project_damage(self._situation(scenario), scenario, horizon_slots=6)
        self.assertEqual((0, 0, 2, 2, 0, 0), out.departure_delay_slots["A1"])
        self.assertEqual((0, 0, 1, 1, 0, 0), out.return_delay_slots["A1"])


if __name__ == "__main__":
    unittest.main()
