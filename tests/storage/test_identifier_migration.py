from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.damage import DamageScenario
from backend.domain.situation import Situation, SituationAirport
from backend.services.airport_seed_service import bootstrap_airport_master
from backend.storage.airport_repository import AirportRepository
from backend.storage.identifier_migration import (
    load_airport_id_map,
    migrate_project_identifiers,
)
from backend.storage.situation_repository import SituationRepository


ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = ROOT / "resources" / "migrations" / "airport_id_map_20260818.json"
SEED_PATH = ROOT / "resources" / "seed" / "airports_master_v1.json"


class IdentifierMigrationTests(unittest.TestCase):
    def test_airport_mapping_and_canonical_seed_are_exact_and_stable(self) -> None:
        mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))

        self.assertEqual(566, len(mapping))
        self.assertEqual(566, len(set(mapping)))
        self.assertEqual(
            [f"AP{index:03d}" for index in range(1, 567)],
            list(mapping.values()),
        )
        self.assertTrue(all(old_id.startswith("oa:") for old_id in mapping))
        self.assertEqual(list(mapping.values()), [row["airport_id"] for row in seed["airports"]])
        self.assertFalse(any(row["airport_id"].startswith("oa:") for row in seed["airports"]))

    def test_fresh_bootstrap_uses_only_project_airport_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "fresh.sqlite3"
            repository = AirportRepository(db_path)
            repository.init_schema()
            self.assertEqual(566, bootstrap_airport_master(repository, SEED_PATH))
            ids = [row.airport_id for row in repository.list_airports()]
            next_id = repository.allocate_airport_id()

        self.assertEqual("AP001", ids[0])
        self.assertEqual("AP566", ids[-1])
        self.assertTrue(all(value.startswith("AP") for value in ids))
        self.assertEqual("AP567", next_id)

    def test_situation_id_reservations_are_monotonic_and_never_reused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repository = SituationRepository(Path(td) / "ids.sqlite3")
            repository.init_schema()
            first = repository.allocate_situation_id()
            second = repository.allocate_situation_id()

        self.assertEqual("ST001", first)
        self.assertEqual("ST002", second)

    def test_transaction_migrates_base_and_situation_references_and_rehashes(self) -> None:
        mapping = load_airport_id_map(MAPPING_PATH)
        old_airport_id = next(iter(mapping))
        new_airport_id = mapping[old_airport_id]

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "legacy.sqlite3"
            airports = AirportRepository(db_path)
            airports.init_schema()
            airport = AirportBase.from_mapping({
                "airport_id": old_airport_id,
                "airport_name": "Legacy Airport",
                "facility_type": "small_airport",
                "role": "military",
                "longitude": 118.0,
                "latitude": 32.0,
                "scheduled_service": False,
                "runway_count": 0,
                "max_runway_length_m": None,
                "runways": [],
            })
            airports.save_airport(airport)
            profile = AirportOperationalProfile(
                airport_id=old_airport_id,
                configuration_complete=False,
                capacity_per_window=8,
                support_level=None,
                aircraft_support=(),
                resource_stocks=(),
            )
            airports.save_operational_profile(profile)
            scenario = DamageScenario.from_mapping({
                "damage_scenario_id": "DS1",
                "name": "Damage",
                "category": "custom",
                "events": [{
                    "event_id": "D1",
                    "sequence": 1,
                    "target": {
                        "airport_id": old_airport_id,
                        "target_type": "support_element",
                        "target_id": "NAV-1",
                    },
                    "damage_type": "navigation_delay",
                    "start_slot": 2,
                    "end_slot": 5,
                    "effect": {"departure_delay_slots": 1, "return_delay_slots": 2},
                    "recovery_mode": "instant",
                    "recovery_duration_slots": None,
                }],
            })
            legacy = Situation(
                situation_id="SITUATION-legacy",
                name="Legacy Situation",
                airports=(SituationAirport(airport=airport, operational_profile=profile),),
                damage_scenarios=(scenario,),
            )
            situations = SituationRepository(db_path)
            situations.save_situation(legacy, owner_user_id="user-1")

            report = migrate_project_identifiers(db_path, mapping_path=MAPPING_PATH)
            migrated = situations.get_situation("ST001")
            with closing(sqlite3.connect(db_path)) as conn:
                foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
                active_oa = sum(
                    conn.execute(f"SELECT COUNT(*) FROM {table} WHERE airport_id LIKE 'oa:%'").fetchone()[0]
                    for table in (
                        "airports", "runways", "airport_operational_profiles",
                        "airport_aircraft_support", "airport_resource_stocks",
                        "situation_airports", "situation_runways",
                        "situation_aircraft_support", "situation_resource_stocks",
                        "situation_resource_replenishments", "situation_damage_events",
                    )
                )

            self.assertEqual([], foreign_key_errors)
            self.assertEqual(0, active_oa)
            self.assertEqual(new_airport_id, migrated.airports[0].airport_id)
            self.assertEqual(new_airport_id, migrated.airports[0].operational_profile.airport_id)
            self.assertEqual(new_airport_id, migrated.damage_scenarios[0].events[0].target.airport_id)
            self.assertEqual(migrated.content_hash(), situations.get_content_hash("ST001"))
            self.assertEqual(1, report.situations_migrated)
            self.assertEqual(1, report.airports_migrated)

    def test_legacy_run_snapshot_payload_is_not_rewritten(self) -> None:
        mapping = load_airport_id_map(MAPPING_PATH)
        old_airport_id = next(iter(mapping))
        payload = json.dumps({"situation": {"situation_id": "S-OLD", "airports": [{"airport_id": old_airport_id}]}})

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "history.sqlite3"
            AirportRepository(db_path).init_schema()
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "INSERT INTO run_input_snapshots VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)",
                    ("RUN-old", "S-OLD", "c" * 64, "s" * 64, payload),
                )
                conn.commit()

            migrate_project_identifiers(db_path, mapping_path=MAPPING_PATH)
            with closing(sqlite3.connect(db_path)) as conn:
                stored = conn.execute(
                    "SELECT situation_id, payload_json FROM run_input_snapshots WHERE run_id='RUN-old'"
                ).fetchone()

        self.assertEqual(("S-OLD", payload), stored)


if __name__ == "__main__":
    unittest.main()
