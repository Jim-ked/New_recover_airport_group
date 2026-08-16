from __future__ import annotations

import unittest

from backend.domain.catalog import (
    AircraftResourceRequirement,
    AircraftType,
    CatalogValidationError,
    ResourceType,
)


class CatalogDomainTests(unittest.TestCase):
    def test_aircraft_type_is_a_catalog_not_inventory(self) -> None:
        payload = {
            "aircraft_type_id": "fighter",
            "name": "fighter",
            "speed_kmh": 800,
            "max_range_km": 1000,
            "reserve_ratio": 0.2,
            "departure_capacity_occupancy_factor": 1.0,
            "arrival_capacity_occupancy_factor": 1.0,
        }
        self.assertEqual(payload, AircraftType.from_mapping(payload).to_dict())
        bad = dict(payload)
        bad["quantity"] = 16
        with self.assertRaises(CatalogValidationError):
            AircraftType.from_mapping(bad)

    def test_resource_types_are_extensible_by_data(self) -> None:
        for category in ("fuel", "material", "munition"):
            payload = {
                "resource_type_id": f"X-{category}",
                "name": category,
                "category": category,
                "unit": "t",
            }
            self.assertEqual(category, ResourceType.from_mapping(payload).category)

    def test_aircraft_resource_requirement_unifies_resource_consumption(self) -> None:
        fuel = AircraftResourceRequirement.from_mapping({
            "aircraft_type_id": "fighter", "resource_type_id": "FUEL-1",
            "basis": "per_hour", "quantity": 1.2,
        })
        material = AircraftResourceRequirement.from_mapping({
            "aircraft_type_id": "fighter", "resource_type_id": "MAT-1",
            "basis": "per_sortie", "quantity": 1,
        })
        self.assertEqual("per_hour", fuel.basis)
        self.assertEqual("per_sortie", material.basis)

    def test_single_leg_range_and_aircraft_fuel_reserve_are_explicit(self) -> None:
        item = AircraftType.from_mapping({
            "aircraft_type_id": "fighter-X", "name": "Fighter X", "speed_kmh": 900,
            "max_range_km": 1450, "reserve_ratio": 0.18,
            "departure_capacity_occupancy_factor": 1.1,
            "arrival_capacity_occupancy_factor": 0.9,
        })
        self.assertEqual(1450, item.max_range_km)
        self.assertEqual(0.18, item.reserve_ratio)
        self.assertEqual(1.1, item.departure_capacity_occupancy_factor)
        self.assertEqual(0.9, item.arrival_capacity_occupancy_factor)

    def test_invalid_range_or_fuel_reserve_fails_fast(self) -> None:
        base = {"aircraft_type_id": "fighter-X", "name": "Fighter X"}
        with self.assertRaises(CatalogValidationError) as ctx:
            AircraftType.from_mapping({**base, "max_range_km": 0})
        self.assertEqual("max_range_km", ctx.exception.field)
        with self.assertRaises(CatalogValidationError) as ctx:
            AircraftType.from_mapping({**base, "reserve_ratio": 1.0})
        self.assertEqual("reserve_ratio", ctx.exception.field)

    def test_legacy_capacity_factor_names_are_rejected(self) -> None:
        with self.assertRaises(CatalogValidationError) as ctx:
            AircraftType.from_mapping({
                "aircraft_type_id": "fighter-X", "name": "Fighter X",
                "capacity_factor": 1.1,
            })
        self.assertEqual("capacity_factor", ctx.exception.field)
        with self.assertRaises(CatalogValidationError) as ctx:
            AircraftType.from_mapping({
                "aircraft_type_id": "fighter-X", "name": "Fighter X",
                "arrival_capacity_factor": 1.2,
            })
        self.assertEqual("arrival_capacity_factor", ctx.exception.field)


if __name__ == "__main__":
    unittest.main()
