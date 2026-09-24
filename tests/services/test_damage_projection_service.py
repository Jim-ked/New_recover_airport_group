from __future__ import annotations

import json
import pathlib
import subprocess
import unittest

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportAircraftSupport, AirportOperationalProfile, AirportResourceStock
from backend.domain.damage import DamageScenario
from backend.domain.situation import Situation, SituationAirport
from backend.services.damage_projection_service import project_damage


class DamageProjectionServiceTests(unittest.TestCase):
    def test_actual_frontend_generator_output_projects_to_preview_capacity(self):
        module = pathlib.Path(__file__).resolve().parents[2] / "frontend/static/js/modules/damage-prefill.js"
        seed_scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "Fixture", "category": "custom",
            "events": [self._capacity_event("fixture", 0, 1, 2, 5)],
        })
        situation = self._situation(seed_scenario)
        script = f'''
            import {{DAMAGE_PRESETS, generateDamageEvents}} from {json.dumps(module.as_uri())};
            const airports = {json.dumps([item.to_dict() for item in situation.airports])};
            const outputs = [];
            for (const [kind, preset] of Object.entries(DAMAGE_PRESETS)) {{
                for (const seed of ['projection-1', 'projection-2', 'projection-3']) {{
                    const result = generateDamageEvents({{
                        kind, scope: 'all', airportIds: [], count: 1,
                        start: 3, end: 30, seed, offset: 2,
                        stages: structuredClone(preset.stages),
                    }}, airports);
                    outputs.push({{kind, seed, ...result}});
                }}
            }}
            console.log(JSON.stringify(outputs));
        '''
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        outputs = json.loads(result.stdout)
        self.assertEqual(15, len(outputs))
        for output in outputs:
            with self.subTest(kind=output["kind"], seed=output["seed"]):
                self.assertTrue(output["events"])
                events = []
                expected = [10] * 32
                previous_end = None
                for index, raw in enumerate(output["events"]):
                    self.assertNotIn("event_id", raw)
                    self.assertNotIn("sequence", raw)
                    self.assertEqual("instant", raw["recovery_mode"])
                    if previous_end is not None:
                        self.assertEqual(previous_end, raw["start_slot"])
                    previous_end = raw["end_slot"]
                    remaining = raw["effect"]["remaining_capacity_per_window"]
                    expected[raw["start_slot"]:raw["end_slot"]] = [remaining] * (raw["end_slot"] - raw["start_slot"])
                    events.append({**raw, "event_id": f"generated-{index}", "sequence": index})
                scenario = DamageScenario.from_mapping({
                    "damage_scenario_id": "DS1", "name": "Generated", "category": "custom", "events": events,
                })
                actual = project_damage(situation, scenario, horizon_slots=32)
                self.assertEqual(tuple(expected), actual.capacity_per_window["A1"])

    def _capacity_event(self, event_id, sequence, start, end, remaining, *, recovery="instant", duration=None):
        return {
            "event_id": event_id, "sequence": sequence,
            "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
            "damage_type": "capacity_damage", "start_slot": start, "end_slot": end,
            "effect": {"remaining_capacity_per_window": remaining},
            "recovery_mode": recovery, "recovery_duration_slots": duration,
        }

    def test_continuous_prefill_has_no_gaps_and_recovers_at_exclusive_end(self):
        for duration in (8, 16):
            with self.subTest(duration=duration):
                scenario = DamageScenario.from_mapping({
                    "damage_scenario_id": "DS1", "name": "Continuous", "category": "custom",
                    "events": [self._capacity_event("C1", 0, 3, 3 + duration, 5)],
                })
                out = project_damage(self._situation(scenario), scenario, horizon_slots=duration + 5)
                self.assertEqual((10,) * 3 + (5,) * duration + (10,) * 2, out.capacity_per_window["A1"])

    def test_extreme_adjacent_instant_stages_match_preview_without_interference(self):
        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "Extreme", "category": "custom",
            "events": [
                self._capacity_event("severe", 0, 2, 5, 2),
                self._capacity_event("closed", 1, 5, 7, 0),
                self._capacity_event("partial", 2, 7, 11, 6),
            ],
        })
        out = project_damage(self._situation(scenario), scenario, horizon_slots=13)
        self.assertEqual((10, 10, 2, 2, 2, 0, 0, 6, 6, 6, 6, 10, 10), out.capacity_per_window["A1"])

    def test_extreme_prefill_respects_existing_average_recovery_and_sequence(self):
        events = [
            self._capacity_event("existing", 0, 1, 3, 2, recovery="average", duration=10),
            self._capacity_event("severe", 1, 2, 4, 3),
            self._capacity_event("closed", 2, 4, 5, 0),
            self._capacity_event("partial", 3, 5, 7, 6),
        ]
        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "Overlap", "category": "custom",
            # Storage order must not supersede the explicit sequence.
            "events": list(reversed(events)),
        })
        out = project_damage(self._situation(scenario), scenario, horizon_slots=14)
        # Partial recovery is capped at existing capacity 4 at its start; after
        # its exclusive end the existing average recovery trajectory resumes.
        self.assertEqual((10, 2, 2, 2, 0, 4, 4, 6, 6, 7, 8, 9, 10, 10), out.capacity_per_window["A1"])

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
