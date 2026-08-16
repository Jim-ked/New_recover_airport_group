from __future__ import annotations

import unittest

from backend.domain.airport_operations import (
    AirportOperationalProfile,
    AirportOperationsValidationError,
)


class AirportOperationalProfileTests(unittest.TestCase):
    def test_sparse_aircraft_and_resource_relations(self) -> None:
        payload = {
            "airport_id": "oa:27188",
            "configuration_complete": True,
            "capacity_per_window": 8,
            "support_level": "L2",
            "aircraft_support": [
                {"aircraft_type_id": "fighter", "initial_quantity": 16, "tau_reset_windows": 1},
                {"aircraft_type_id": "transport", "initial_quantity": 0, "tau_reset_windows": 3},
            ],
            "resource_stocks": [
                {"resource_type_id": "FUEL-1", "initial_quantity": 220.2, "replenishment_capacity_per_window": 0},
                {"resource_type_id": "MAT-1", "initial_quantity": 130, "replenishment_capacity_per_window": 0},
                {"resource_type_id": "MUN-1", "initial_quantity": 150, "replenishment_capacity_per_window": 0},
            ],
        }
        profile = AirportOperationalProfile.from_mapping(payload)
        self.assertEqual(payload, profile.to_dict())
        self.assertTrue(profile.supports_aircraft("transport"))
        self.assertFalse(profile.supports_aircraft("bomber"))
        self.assertEqual(0, profile.resource_initial_quantity("MAT-99"))

    def test_zero_aircraft_quantity_does_not_mean_unsupported(self) -> None:
        profile = AirportOperationalProfile.from_mapping(
            {
                "airport_id": "A1",
                "configuration_complete": True,
                "capacity_per_window": 4,
                "aircraft_support": [
                    {"aircraft_type_id": "transport", "initial_quantity": 0, "tau_reset_windows": 3}
                ],
                "resource_stocks": [],
            }
        )
        self.assertTrue(profile.supports_aircraft("transport"))
        self.assertFalse(profile.supports_aircraft("fighter"))

    def test_incomplete_profile_absence_is_unknown_not_zero(self) -> None:
        profile = AirportOperationalProfile.from_mapping(
            {
                "airport_id": "A1",
                "configuration_complete": False,
                "capacity_per_window": None,
                "aircraft_support": [],
                "resource_stocks": [],
            }
        )
        self.assertIsNone(profile.supports_aircraft("fighter"))
        self.assertIsNone(profile.resource_initial_quantity("MAT-1"))

    def test_complete_profile_requires_values_for_present_relations(self) -> None:
        bad_profiles = (
            {
                "airport_id": "A1",
                "configuration_complete": True,
                "capacity_per_window": None,
                "aircraft_support": [],
                "resource_stocks": [],
            },
            {
                "airport_id": "A1",
                "configuration_complete": True,
                "capacity_per_window": 4,
                "aircraft_support": [{"aircraft_type_id": "fighter", "initial_quantity": None, "tau_reset_windows": 1}],
                "resource_stocks": [],
            },
            {
                "airport_id": "A1",
                "configuration_complete": True,
                "capacity_per_window": 4,
                "aircraft_support": [],
                "resource_stocks": [{"resource_type_id": "MAT-1", "initial_quantity": None, "replenishment_capacity_per_window": None}],
            },
        )
        for payload in bad_profiles:
            with self.subTest(payload=payload):
                with self.assertRaises(AirportOperationsValidationError):
                    AirportOperationalProfile.from_mapping(payload)

    def test_duplicate_relation_keys_are_rejected(self) -> None:
        payload = {
            "airport_id": "A1",
            "configuration_complete": False,
            "aircraft_support": [
                {"aircraft_type_id": "fighter", "initial_quantity": 1, "tau_reset_windows": 1},
                {"aircraft_type_id": "fighter", "initial_quantity": 2, "tau_reset_windows": 2},
            ],
            "resource_stocks": [],
        }
        with self.assertRaises(AirportOperationsValidationError) as caught:
            AirportOperationalProfile.from_mapping(payload)
        self.assertEqual("aircraft_support", caught.exception.field)


if __name__ == "__main__":
    unittest.main()
