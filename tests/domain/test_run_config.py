from __future__ import annotations

import unittest

from backend.domain.run_config import RunConfig, RunConfigValidationError


class RunConfigTests(unittest.TestCase):
    def test_presets_resolve_to_non_extreme_original_weights(self):
        cases = {
            "sortie_max": [0.8, 0.1, 0.1],
            "resource_min": [0.1, 0.8, 0.1],
            "time_min": [0.1, 0.1, 0.8],
        }
        for mode, alpha in cases.items():
            cfg = RunConfig.from_mapping({
                "damage_scenario_id": None,
                "preference_mode": mode,
                "cluster_enabled": False,
                "cluster_size": None,
                "core_airports": [],
                "aircraft_type_weight": {},
                "mip_time_limit_s": 120,
            })
            self.assertEqual(alpha, cfg.to_dict()["alpha"])

    def test_custom_zero_dimensions_use_existing_floor_then_normalize(self):
        cfg = RunConfig.from_mapping({
            "damage_scenario_id": "DS1",
            "preference_mode": "custom",
            "alpha": [1, 0, 0],
            "cluster_enabled": True,
            "cluster_size": 4,
            "core_airports": ["A2", "A1"],
            "aircraft_type_weight": {"fighter": 1.2},
            "mip_time_limit_s": 90,
        })
        self.assertAlmostEqual(1.0, sum(cfg.alpha))
        self.assertGreater(cfg.alpha[1], 0)
        self.assertGreater(cfg.alpha[2], 0)
        self.assertEqual(["A1", "A2"], cfg.to_dict()["core_airports"])

    def test_core_airports_are_ids_only_max_two(self):
        with self.assertRaises(RunConfigValidationError):
            RunConfig.from_mapping({
                "damage_scenario_id": None, "preference_mode": "sortie_max",
                "cluster_enabled": True, "cluster_size": 4,
                "core_airports": ["A1", "A2", "A3"],
                "aircraft_type_weight": {}, "mip_time_limit_s": 120,
            })
        with self.assertRaises(RunConfigValidationError):
            RunConfig.from_mapping({
                "damage_scenario_id": None, "preference_mode": "sortie_max",
                "cluster_enabled": True, "cluster_size": 4,
                "core_airports": {"A1": 2.0},
                "aircraft_type_weight": {}, "mip_time_limit_s": 120,
            })

    def test_cluster_disabled_has_no_latent_core_or_size(self):
        with self.assertRaises(RunConfigValidationError):
            RunConfig.from_mapping({
                "damage_scenario_id": None, "preference_mode": "sortie_max",
                "cluster_enabled": False, "cluster_size": 4,
                "core_airports": [], "aircraft_type_weight": {}, "mip_time_limit_s": 120,
            })

    def test_validate_against_situation_and_catalog(self):
        cfg = RunConfig.from_mapping({
            "damage_scenario_id": "DS1", "preference_mode": "time_min",
            "cluster_enabled": True, "cluster_size": 2,
            "core_airports": ["A1"], "aircraft_type_weight": {"fighter": 1.1},
            "mip_time_limit_s": 120,
        })
        cfg.validate_against(
            airport_ids=["A1", "A2"], damage_scenario_ids=["DS1"], aircraft_type_ids=["fighter"]
        )
        with self.assertRaises(RunConfigValidationError):
            cfg.validate_against(
                airport_ids=["A1"], damage_scenario_ids=["DS1"], aircraft_type_ids=["fighter"]
            )

    def test_algorithm_seed_defaults_to_42_and_is_frozen(self):
        cfg = RunConfig.from_mapping({
            "damage_scenario_id": None,
            "preference_mode": "sortie_max",
            "cluster_enabled": False,
            "cluster_size": None,
            "core_airports": [],
            "aircraft_type_weight": {},
            "mip_time_limit_s": 120,
        })
        self.assertEqual(42, cfg.algorithm_seed)
        self.assertEqual(42, cfg.to_dict()["algorithm_seed"])

    def test_algorithm_seed_must_be_nonnegative_integer(self):
        with self.assertRaises(RunConfigValidationError):
            RunConfig.from_mapping({
                "damage_scenario_id": None,
                "preference_mode": "sortie_max",
                "cluster_enabled": False,
                "cluster_size": None,
                "core_airports": [],
                "aircraft_type_weight": {},
                "mip_time_limit_s": 120,
                "algorithm_seed": -1,
            })


if __name__ == "__main__":
    unittest.main()
