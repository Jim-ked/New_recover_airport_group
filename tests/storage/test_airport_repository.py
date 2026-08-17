from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.storage.airport_repository import AirportRepository


ROOT = Path(__file__).resolve().parents[2]
MASTER_PATH = ROOT / "resources" / "seed" / "airports_master_v1.json"


class AirportRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "app.db"
        self.repo = AirportRepository(self.db_path)
        self.repo.init_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _master_airports(self):
        payload = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
        return [AirportBase.from_mapping(item) for item in payload["airports"]]

    def test_all_566_master_airports_store_and_reload(self) -> None:
        airports = self._master_airports()
        self.repo.save_airports(airports)
        self.assertEqual(566, self.repo.count_airports())
        reloaded = self.repo.list_airports()
        self.assertEqual(566, len(reloaded))
        self.assertEqual(
            {a.airport_id for a in airports},
            {a.airport_id for a in reloaded},
        )

    def test_runway_unknown_and_known_zero_survive_sqlite(self) -> None:
        unknown = AirportBase.from_mapping(
            {
                "airport_id": "A-UNKNOWN",
                "airport_name": "Unknown Runway Airport",
                "facility_type": "small_airport",
                "role": "civil",
                "icao_code": None,
                "iata_code": None,
                "region": None,
                "municipality": None,
                "longitude": 120.0,
                "latitude": 30.0,
                "elevation_m": None,
                "scheduled_service": False,
                "runway_count": None,
                "max_runway_length_m": None,
                "runways": None,
            }
        )
        zero = AirportBase.from_mapping(
            {
                "airport_id": "A-ZERO",
                "airport_name": "Known Zero Airport",
                "facility_type": "small_airport",
                "role": "military",
                "icao_code": None,
                "iata_code": None,
                "region": None,
                "municipality": None,
                "longitude": 121.0,
                "latitude": 31.0,
                "elevation_m": None,
                "scheduled_service": False,
                "runway_count": 0,
                "max_runway_length_m": None,
                "runways": [],
            }
        )
        self.repo.save_airport(unknown)
        self.repo.save_airport(zero)
        self.assertIsNone(self.repo.get_airport("A-UNKNOWN").runways)
        self.assertEqual((), self.repo.get_airport("A-ZERO").runways)

    def test_new_aircraft_and_resource_types_add_rows_not_columns(self) -> None:
        airport = self._master_airports()[0]
        self.repo.save_airport(airport)

        self.repo.save_aircraft_type(
            AircraftType.from_mapping(
                {
                    "aircraft_type_id": "fighter-X",
                    "name": "fighter-X",
                    "speed_kmh": 850,
                    "departure_capacity_occupancy_factor": 1.0,
                    "arrival_capacity_occupancy_factor": 1.0,
                }
            )
        )
        self.repo.save_aircraft_type(
            AircraftType.from_mapping(
                {
                    "aircraft_type_id": "transport-Y",
                    "name": "transport-Y",
                    "speed_kmh": 700,
                    "departure_capacity_occupancy_factor": 1.2,
                    "arrival_capacity_occupancy_factor": 1.2,
                }
            )
        )
        for item in (
            {"resource_type_id": "FUEL-A", "name": "Fuel A", "category": "fuel", "unit": "t"},
            {"resource_type_id": "MAT-99", "name": "Material 99", "category": "material", "unit": "t"},
            {"resource_type_id": "MUN-8", "name": "Munition 8", "category": "munition", "unit": "t"},
        ):
            self.repo.save_resource_type(ResourceType.from_mapping(item))

        profile = AirportOperationalProfile.from_mapping(
            {
                "airport_id": airport.airport_id,
                "configuration_complete": True,
                "capacity_per_window": 8,
                "support_level": None,
                "aircraft_support": [
                    {"aircraft_type_id": "fighter-X", "initial_quantity": 12, "tau_reset_windows": 2},
                    {"aircraft_type_id": "transport-Y", "initial_quantity": 0, "tau_reset_windows": 4},
                ],
                "resource_stocks": [
                    {"resource_type_id": "FUEL-A", "initial_quantity": 200, "replenishment_capacity_per_window": 0},
                    {"resource_type_id": "MAT-99", "initial_quantity": 30, "replenishment_capacity_per_window": 0},
                ],
            }
        )
        self.repo.save_operational_profile(profile)
        saved = self.repo.get_operational_profile(airport.airport_id)
        self.assertTrue(saved.supports_aircraft("transport-Y"))
        self.assertEqual(0, saved.resource_initial_quantity("MUN-8"))

        with self.repo.connect() as conn:
            airport_columns = {r["name"] for r in conn.execute("PRAGMA table_info(airports)")}
            support_columns = {r["name"] for r in conn.execute("PRAGMA table_info(airport_aircraft_support)")}
            stock_columns = {r["name"] for r in conn.execute("PRAGMA table_info(airport_resource_stocks)")}
        for dynamic_name in ("fighter-X", "transport-Y", "MAT-99", "MUN-8"):
            self.assertNotIn(dynamic_name, airport_columns | support_columns | stock_columns)

    def test_aircraft_type_range_and_fuel_reserve_roundtrip(self) -> None:
        item = AircraftType.from_mapping(
            {
                "aircraft_type_id": "fighter-R",
                "name": "fighter-R",
                "speed_kmh": 880,
                "max_range_km": 1600,
                "reserve_ratio": 0.22,
                "departure_capacity_occupancy_factor": 1.05,
                "arrival_capacity_occupancy_factor": 1.05,
            }
        )
        self.repo.save_aircraft_type(item)
        self.assertEqual(item, self.repo.get_aircraft_type("fighter-R"))
        self.assertEqual([item], self.repo.list_aircraft_types())

    def test_aircraft_resource_requirements_reference_catalog_rows(self) -> None:
        self.repo.save_aircraft_type(
            AircraftType.from_mapping(
                {
                    "aircraft_type_id": "fighter",
                    "name": "fighter",
                    "speed_kmh": 800,
                    "departure_capacity_occupancy_factor": 1,
                    "arrival_capacity_occupancy_factor": 1,
                }
            )
        )
        self.repo.save_resource_type(
            ResourceType.from_mapping(
                {"resource_type_id": "MAT-1", "name": "MAT-1", "category": "material", "unit": "t"}
            )
        )
        self.repo.save_aircraft_resource_requirement(
            AircraftResourceRequirement.from_mapping(
                {
                    "aircraft_type_id": "fighter",
                    "resource_type_id": "MAT-1",
                    "basis": "per_sortie",
                    "quantity": 1,
                }
            )
        )
        with self.repo.connect() as conn:
            row = conn.execute("SELECT quantity FROM aircraft_resource_requirements").fetchone()
        self.assertEqual(1.0, row["quantity"])

    def test_unknown_catalog_reference_fails_fast(self) -> None:
        airport = self._master_airports()[0]
        self.repo.save_airport(airport)
        profile = AirportOperationalProfile.from_mapping(
            {
                "airport_id": airport.airport_id,
                "configuration_complete": True,
                "capacity_per_window": 8,
                "aircraft_support": [
                    {"aircraft_type_id": "NOT-IN-CATALOG", "initial_quantity": 1, "tau_reset_windows": 1}
                ],
                "resource_stocks": [],
            }
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.save_operational_profile(profile)

    def test_static_airport_edit_does_not_change_operational_profile(self) -> None:
        airport = self._master_airports()[0]
        self.repo.save_airport(airport)
        self.repo.save_aircraft_type(
            AircraftType.from_mapping(
                {
                    "aircraft_type_id": "fighter",
                    "name": "fighter",
                    "speed_kmh": 800,
                    "departure_capacity_occupancy_factor": 1,
                    "arrival_capacity_occupancy_factor": 1,
                }
            )
        )
        profile = AirportOperationalProfile.from_mapping(
            {
                "airport_id": airport.airport_id,
                "configuration_complete": True,
                "capacity_per_window": 8,
                "aircraft_support": [
                    {"aircraft_type_id": "fighter", "initial_quantity": 16, "tau_reset_windows": 1}
                ],
                "resource_stocks": [],
            }
        )
        self.repo.save_operational_profile(profile)

        changed = AirportBase(
            airport_id=airport.airport_id,
            airport_name=airport.airport_name + " renamed",
            facility_type=airport.facility_type,
            role=airport.role,
            longitude=airport.longitude,
            latitude=airport.latitude,
            scheduled_service=airport.scheduled_service,
            icao_code=airport.icao_code,
            iata_code=airport.iata_code,
            region=airport.region,
            municipality=airport.municipality,
            elevation_m=airport.elevation_m,
            runways=airport.runways,
        )
        self.repo.save_airport(changed)
        after = self.repo.get_operational_profile(airport.airport_id)
        self.assertEqual(16, after.aircraft_support[0].initial_quantity)
        self.assertEqual(8, after.capacity_per_window)


if __name__ == "__main__":
    unittest.main()
