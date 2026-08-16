from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.storage.database import initialize_database


class DatabaseMigrationTests(unittest.TestCase):
    def test_all_migrations_are_non_destructive_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "app.sqlite"
            initialize_database(path)
            conn = sqlite3.connect(path)
            try:
                conn.execute(
                    "INSERT INTO airports (airport_id, airport_name, facility_type, role, longitude, latitude, scheduled_service, runways_known) VALUES (?,?,?,?,?,?,?,?)",
                    ("A1", "A", "small_airport", "civil", 110.0, 30.0, 1, 0),
                )
                conn.commit()
            finally:
                conn.close()

            initialize_database(path)
            conn = sqlite3.connect(path)
            try:
                migration_count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
                airport_count = conn.execute("SELECT COUNT(*) FROM airports WHERE airport_id='A1'").fetchone()[0]
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                conn.close()

            self.assertEqual(16, migration_count)
            self.assertEqual(1, airport_count)
            self.assertIn("situations", tables)
            self.assertIn("situation_damage_scenarios", tables)
            self.assertIn("situation_damage_events", tables)
            self.assertIn("mission_records", tables)
            self.assertIn("run_input_snapshots", tables)
            self.assertIn("runs", tables)
            self.assertIn("run_events", tables)
            self.assertIn("run_results", tables)
            self.assertIn("audit_events", tables)

    def test_step5_database_upgrades_without_reinterpreting_legacy_damage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "step5.sqlite"
            conn = sqlite3.connect(path)
            try:
                conn.executescript(
                    """
                    PRAGMA foreign_keys = ON;
                    CREATE TABLE schema_migrations (
                        migration_id TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE aircraft_types (
                        aircraft_type_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        speed_kmh REAL,
                        max_range_km REAL,
                        reserve_ratio REAL,
                        capacity_factor REAL
                    );
                    CREATE TABLE situations (
                        situation_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        content_hash TEXT
                    );
                    CREATE TABLE situation_airports (
                        situation_id TEXT NOT NULL,
                        airport_id TEXT NOT NULL,
                        PRIMARY KEY (situation_id, airport_id),
                        FOREIGN KEY (situation_id) REFERENCES situations(situation_id) ON DELETE CASCADE
                    );
                    CREATE TABLE situation_damage_events (
                        situation_id TEXT NOT NULL,
                        damage_event_id TEXT NOT NULL,
                        sequence_no INTEGER NOT NULL,
                        airport_id TEXT NOT NULL,
                        target_type TEXT NOT NULL,
                        target_id TEXT,
                        start_slot INTEGER NOT NULL,
                        end_slot INTEGER NOT NULL,
                        damage_degree REAL NOT NULL,
                        recovery_duration_slots INTEGER,
                        PRIMARY KEY (situation_id, damage_event_id),
                        FOREIGN KEY (situation_id, airport_id)
                            REFERENCES situation_airports(situation_id, airport_id) ON DELETE RESTRICT
                    );
                    CREATE TABLE sentinel_records (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                    """
                )
                conn.executemany(
                    "INSERT INTO schema_migrations (migration_id) VALUES (?)",
                    [(f"v00{i}_{name}",) for i, name in (
                        (1, "airport_data"), (2, "situations"), (3, "aircraft_type_semantics"),
                        (4, "situation_damage"), (5, "run_snapshots"), (6, "situation_content_hash"),
                    )],
                )
                conn.execute(
                    "INSERT INTO aircraft_types VALUES (?,?,?,?,?,?)",
                    ("fighter", "Fighter", 800.0, 1200.0, 0.2, 1.3),
                )
                conn.execute("INSERT INTO situations VALUES (?,?,?,?)", ("S1", "S", None, "x" * 64))
                conn.execute("INSERT INTO situation_airports VALUES (?,?)", ("S1", "A1"))
                conn.execute(
                    "INSERT INTO situation_damage_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("S1", "D1", 0, "A1", "airport", None, 4, 8, 0.5, 2),
                )
                conn.execute("INSERT INTO sentinel_records VALUES (?,?)", ("keep", "unchanged"))
                conn.commit()
            finally:
                conn.close()

            initialize_database(path)

            conn = sqlite3.connect(path)
            try:
                conn.row_factory = sqlite3.Row
                ac = conn.execute(
                    "SELECT * FROM aircraft_types WHERE aircraft_type_id='fighter'"
                ).fetchone()
                legacy = conn.execute(
                    "SELECT * FROM legacy_situation_damage_events_v004 WHERE damage_event_id='D1'"
                ).fetchone()
                canonical_count = conn.execute(
                    "SELECT COUNT(*) FROM situation_damage_events"
                ).fetchone()[0]
                scenario_count = conn.execute(
                    "SELECT COUNT(*) FROM situation_damage_scenarios"
                ).fetchone()[0]
                sentinel = conn.execute(
                    "SELECT value FROM sentinel_records WHERE id='keep'"
                ).fetchone()[0]
                migrations = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(1.3, ac["departure_capacity_occupancy_factor"])
            self.assertEqual(1.3, ac["arrival_capacity_occupancy_factor"])
            self.assertEqual(0.5, legacy["damage_degree"])
            self.assertEqual(0, canonical_count)
            self.assertEqual(0, scenario_count)
            self.assertEqual("unchanged", sentinel)
            self.assertEqual(16, migrations)

    def test_damage_migration_fails_fast_if_legacy_preservation_table_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "collision.sqlite"
            conn = sqlite3.connect(path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE schema_migrations (
                        migration_id TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE aircraft_types (
                        aircraft_type_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                        speed_kmh REAL, max_range_km REAL, reserve_ratio REAL,
                        departure_capacity_occupancy_factor REAL,
                        arrival_capacity_occupancy_factor REAL
                    );
                    CREATE TABLE situations (situation_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, content_hash TEXT);
                    CREATE TABLE situation_airports (
                        situation_id TEXT NOT NULL, airport_id TEXT NOT NULL,
                        PRIMARY KEY (situation_id, airport_id)
                    );
                    CREATE TABLE situation_damage_events (
                        situation_id TEXT, damage_event_id TEXT, damage_degree REAL
                    );
                    CREATE TABLE legacy_situation_damage_events_v004 (
                        situation_id TEXT, damage_event_id TEXT, damage_degree REAL
                    );
                    """
                )
                conn.executemany(
                    "INSERT INTO schema_migrations (migration_id) VALUES (?)",
                    [(x,) for x in (
                        "v001_airport_data", "v002_situations", "v003_aircraft_type_semantics",
                        "v004_situation_damage", "v005_run_snapshots", "v006_situation_content_hash",
                        "v007_capacity_occupancy_factors",
                    )],
                )
                conn.commit()
            finally:
                conn.close()

            with self.assertRaisesRegex(RuntimeError, "legacy preservation table already exists"):
                initialize_database(path)


if __name__ == "__main__":
    unittest.main()
