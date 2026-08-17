from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.domain.airport import AirportBase, AirportValidationError


ROOT = Path(__file__).resolve().parents[2]
MASTER_PATH = ROOT / "resources" / "seed" / "airports_master_v1.json"

VALID_AIRPORT = {
    "airport_id": "oa:27188",
    "airport_name": "Beijing Capital International Airport",
    "facility_type": "large_airport",
    "role": "civil",
    "icao_code": "ZBAA",
    "iata_code": "PEK",
    "region": "CN-11",
    "municipality": "Beijing",
    "longitude": 116.596702,
    "latitude": 40.077349,
    "elevation_m": 35.4,
    "scheduled_service": True,
    "runway_count": 1,
    "max_runway_length_m": 3799.9,
    "runways": [
        {
            "runway_id": "oa-runway:269343",
            "length_m": 3799.9,
            "width_m": 60.0,
            "surface": "CON",
            "lighted": True,
            "low_end": {
                "ident": "01",
                "latitude": 40.058914,
                "longitude": 116.617599,
                "elevation_m": 28.7,
                "heading_deg_true": 353.0,
                "displaced_threshold_m": None,
            },
            "high_end": None,
        }
    ],
}


class AirportBaseDomainTests(unittest.TestCase):
    def test_static_airport_round_trips(self) -> None:
        airport = AirportBase.from_mapping(VALID_AIRPORT)
        self.assertEqual(VALID_AIRPORT, airport.to_dict())

    def test_final_master_all_566_airports_validate(self) -> None:
        payload = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
        self.assertEqual(566, payload["count"])
        parsed = [AirportBase.from_mapping(item) for item in payload["airports"]]
        self.assertEqual(566, len(parsed))
        self.assertEqual(566, len({item.airport_id for item in parsed}))

    def test_required_fields_and_enums(self) -> None:
        for field in (
            "airport_id",
            "airport_name",
            "facility_type",
            "role",
            "longitude",
            "latitude",
            "scheduled_service",
        ):
            with self.subTest(field=field):
                payload = dict(VALID_AIRPORT)
                payload.pop(field)
                with self.assertRaises(AirportValidationError) as caught:
                    AirportBase.from_mapping(payload)
                self.assertEqual(field, caught.exception.field)

        for field, bad in (
            ("facility_type", "military"),
            ("role", "large_airport"),
            ("scheduled_service", 1),
        ):
            payload = dict(VALID_AIRPORT)
            payload[field] = bad
            with self.assertRaises(AirportValidationError) as caught:
                AirportBase.from_mapping(payload)
            self.assertEqual(field, caught.exception.field)

    def test_coordinates_are_strict_numbers(self) -> None:
        for field, value in (
            ("longitude", "116.58"),
            ("latitude", "40.08"),
            ("longitude", 180.0001),
            ("latitude", 90.0001),
            ("longitude", True),
        ):
            with self.subTest(field=field, value=value):
                payload = dict(VALID_AIRPORT)
                payload[field] = value
                with self.assertRaises(AirportValidationError) as caught:
                    AirportBase.from_mapping(payload)
                self.assertEqual(field, caught.exception.field)

    def test_runway_unknown_and_known_zero_are_distinct(self) -> None:
        unknown = dict(VALID_AIRPORT)
        unknown.update(runways=None, runway_count=None, max_runway_length_m=None)
        airport = AirportBase.from_mapping(unknown)
        self.assertIsNone(airport.runways)
        self.assertIsNone(airport.runway_count)

        known_zero = dict(VALID_AIRPORT)
        known_zero.update(runways=[], runway_count=0, max_runway_length_m=None)
        airport = AirportBase.from_mapping(known_zero)
        self.assertEqual((), airport.runways)
        self.assertEqual(0, airport.runway_count)

    def test_runway_summary_is_validated_not_trusted(self) -> None:
        payload = dict(VALID_AIRPORT)
        payload["runway_count"] = 2
        with self.assertRaises(AirportValidationError) as caught:
            AirportBase.from_mapping(payload)
        self.assertEqual("runway_count", caught.exception.field)

        payload = dict(VALID_AIRPORT)
        payload["max_runway_length_m"] = 4000
        with self.assertRaises(AirportValidationError) as caught:
            AirportBase.from_mapping(payload)
        self.assertEqual("max_runway_length_m", caught.exception.field)

    def test_operational_fields_do_not_belong_to_airport_base(self) -> None:
        for field in ("support_level", "default_config", "capacity", "supported_aircraft", "fuel"):
            with self.subTest(field=field):
                payload = dict(VALID_AIRPORT)
                payload[field] = None
                with self.assertRaises(AirportValidationError) as caught:
                    AirportBase.from_mapping(payload)
                self.assertEqual(field, caught.exception.field)


if __name__ == "__main__":
    unittest.main()
