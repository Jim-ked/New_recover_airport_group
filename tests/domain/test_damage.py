from __future__ import annotations

import unittest

from backend.domain.damage import DamageEvent, DamageScenario, DamageTarget, DamageValidationError


class DamageTests(unittest.TestCase):
    def test_aircraft_damage_is_one_time_and_nonrecovering(self):
        event = DamageEvent.from_mapping({
            "event_id": "D1", "sequence": 0,
            "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
            "damage_type": "aircraft_damage", "start_slot": 10, "end_slot": 12,
            "effect": {"aircraft_loss": {"fighter": 2}},
            "recovery_mode": "none", "recovery_duration_slots": None,
        })
        self.assertEqual((10, 12), (event.start_slot, event.end_slot))
        self.assertEqual({"aircraft_loss": {"fighter": 2}}, event.effect.to_dict())

    def test_average_and_instant_recovery_are_explicit(self):
        avg = DamageEvent.from_mapping({
            "event_id": "D1", "sequence": 0,
            "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
            "damage_type": "capacity_damage", "start_slot": 2, "end_slot": 4,
            "effect": {"closed": False, "remaining_capacity_per_window": 3},
            "recovery_mode": "average", "recovery_duration_slots": 4,
        })
        self.assertEqual(4, avg.recovery_duration_slots)
        instant = DamageEvent.from_mapping({
            "event_id": "D2", "sequence": 1,
            "target": {"airport_id": "A1", "target_type": "support_element", "target_id": "RADAR"},
            "damage_type": "navigation_delay", "start_slot": 3, "end_slot": 5,
            "effect": {"departure_delay_slots": 2, "return_delay_slots": 1},
            "recovery_mode": "instant", "recovery_duration_slots": None,
        })
        self.assertEqual("instant", instant.recovery_mode)

    def test_invalid_interval_and_recovery_combination_fail_fast(self):
        with self.assertRaises(DamageValidationError):
            DamageEvent.from_mapping({
                "event_id": "D1", "sequence": 0,
                "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                "damage_type": "capacity_damage", "start_slot": 10, "end_slot": 10,
                "effect": {"closed": True}, "recovery_mode": "instant",
            })
        with self.assertRaises(DamageValidationError):
            DamageEvent.from_mapping({
                "event_id": "D1", "sequence": 0,
                "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                "damage_type": "aircraft_damage", "start_slot": 1, "end_slot": 2,
                "effect": {"aircraft_loss": {"fighter": 1}}, "recovery_mode": "average",
                "recovery_duration_slots": 3,
            })

    def test_target_identity_rules_are_strict(self):
        with self.assertRaises(DamageValidationError):
            DamageTarget("A1", "airport", "RWY-1")
        with self.assertRaises(DamageValidationError):
            DamageTarget("A1", "runway", None)
        self.assertEqual("ENG", DamageTarget("A1", "support_element", "ENG").target_id)

    def test_damage_scenario_owns_order_and_category_only_metadata(self):
        e1 = DamageEvent.from_mapping({
            "event_id": "D1", "sequence": 0,
            "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
            "damage_type": "resource_damage", "start_slot": 1, "end_slot": 3,
            "effect": {"remaining_quantity": {"FUEL-1": 50}},
            "recovery_mode": "average", "recovery_duration_slots": 2,
        })
        scenario = DamageScenario("DS1", "Medium", "medium", (e1,))
        self.assertEqual("medium", scenario.category)
        with self.assertRaises(DamageValidationError):
            DamageScenario("DS2", "Bad", "medium", (e1, DamageEvent.from_mapping({
                **e1.to_dict(), "event_id": "D2"
            })))

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(DamageValidationError):
            DamageEvent.from_mapping({
                "event_id": "D1", "sequence": 0,
                "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                "damage_type": "capacity_damage", "start_slot": 1, "end_slot": 2,
                "effect": {"closed": True}, "recovery_mode": "instant",
                "damage_degree": 0.5,
            })


if __name__ == "__main__":
    unittest.main()
