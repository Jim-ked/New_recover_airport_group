from __future__ import annotations

import unittest

from backend.domain.objective_profile import (
    RESEARCH_OBJECTIVE_PROFILE_ID,
    resolve_objective_profile,
    validate_research_profile_semantics,
)
from backend.domain.catalog import AircraftResourceRequirement, ResourceType
from backend.domain.run_config import RunConfigValidationError


class ObjectiveProfileTests(unittest.TestCase):
    def test_research_profile_resolves_the_frozen_experiment_calibration(self):
        resolved = resolve_objective_profile({
            "objective_profile_id": RESEARCH_OBJECTIVE_PROFILE_ID,
            "damage_scenario_id": None,
            "preference_mode": "resource_min",
            "cluster_enabled": False,
            "cluster_size": None,
            "core_airports": [],
            "aircraft_type_weight": {},
            "mip_time_limit_s": 120,
        })

        self.assertEqual(
            {"fighter": 1.0, "bomber": 1.0, "transport": 0.9},
            resolved["aircraft_type_weight"],
        )
        self.assertEqual(1.5625, resolved["f2_resource_reference_quantities"]["fuel"])
        self.assertEqual(2.0, resolved["f2_resource_reference_quantities"]["MUN-1"])
        self.assertAlmostEqual(1 / 3, resolved["f2_resource_weights"]["fuel"])
        self.assertAlmostEqual(1 / 9, resolved["f2_resource_weights"]["MAT-1"])
        self.assertEqual(16.0, resolved["f3_time_reference_slots"])
        self.assertEqual(1.0, resolved["f3_tardiness_coefficient"])
        self.assertEqual(3.0, resolved["unmet_demand_penalty"])
        self.assertEqual(0.0, resolved["core_airport_reward_weight"])

    def test_profile_rejects_conflicting_known_weight_but_allows_explicit_new_type(self):
        base = {
            "objective_profile_id": RESEARCH_OBJECTIVE_PROFILE_ID,
            "damage_scenario_id": None,
            "preference_mode": "sortie_max",
            "cluster_enabled": False,
            "cluster_size": None,
            "core_airports": [],
            "mip_time_limit_s": 120,
        }
        with self.assertRaisesRegex(RunConfigValidationError, "fighter"):
            resolve_objective_profile({**base, "aircraft_type_weight": {"fighter": 1.2}})

        resolved = resolve_objective_profile({
            **base,
            "aircraft_type_weight": {"new-type": 0.75},
        })
        self.assertEqual(0.75, resolved["aircraft_type_weight"]["new-type"])

    def test_profile_rejects_unknown_identifier(self):
        with self.assertRaisesRegex(RunConfigValidationError, "objective_profile_id"):
            resolve_objective_profile({"objective_profile_id": "not-a-profile"})

    def test_profile_rejects_changed_known_resource_semantics(self):
        with self.assertRaisesRegex(RunConfigValidationError, "fuel"):
            validate_research_profile_semantics(
                profile_id=RESEARCH_OBJECTIVE_PROFILE_ID,
                resource_types=[ResourceType("fuel", "Fuel", "fuel", "kg")],
                requirements=[
                    AircraftResourceRequirement("fighter", "fuel", "per_hour", 1)
                ],
                used_aircraft_type_ids=["fighter"],
                slot_minutes=15,
            )
        with self.assertRaisesRegex(RunConfigValidationError, "per_hour"):
            validate_research_profile_semantics(
                profile_id=RESEARCH_OBJECTIVE_PROFILE_ID,
                resource_types=[ResourceType("fuel", "Fuel", "fuel", "t")],
                requirements=[
                    AircraftResourceRequirement("fighter", "fuel", "per_sortie", 1)
                ],
                used_aircraft_type_ids=["fighter"],
                slot_minutes=15,
            )


if __name__ == "__main__":
    unittest.main()
