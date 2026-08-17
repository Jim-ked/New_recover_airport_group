from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.storage.database import _MIGRATIONS, connect, initialize_database


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

            self.assertEqual(17, migration_count)
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
            self.assertEqual(17, migrations)

    def test_v017_upgrades_v016_stocks_without_reinterpreting_zero_or_losing_children(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "v016.sqlite"
            with connect(path) as conn:
                conn.execute(
                    """
                    CREATE TABLE schema_migrations (
                        migration_id TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                for migration_id, apply in _MIGRATIONS:
                    if migration_id == "v017_nullable_replenishment":
                        break
                    apply(conn)
                    conn.execute(
                        "INSERT INTO schema_migrations (migration_id) VALUES (?)",
                        (migration_id,),
                    )

            with connect(path) as conn:
                conn.execute(
                    """
                    INSERT INTO airports (
                        airport_id, airport_name, facility_type, role, longitude,
                        latitude, scheduled_service, runways_known
                    ) VALUES ('A1', 'Airport', 'small_airport', 'military', 110, 30, 0, 0)
                    """
                )
                conn.executemany(
                    "INSERT INTO resource_types (resource_type_id, name, category, unit) VALUES (?,?,?,?)",
                    [
                        ("R1", "Resource 1", "material", "unit"),
                        ("R2", "Resource 2", "material", "unit"),
                    ],
                )
                conn.execute(
                    """
                    INSERT INTO airport_operational_profiles (
                        airport_id, configuration_complete, capacity_per_window, support_level
                    ) VALUES ('A1', 0, 8, NULL)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO airport_resource_stocks (
                        airport_id, resource_type_id, quantity, replenishment_capacity_per_window
                    ) VALUES ('A1', 'R1', 100, 0)
                    """
                )
                conn.execute(
                    "INSERT INTO situations (situation_id, name, description) VALUES ('S1', 'Situation', NULL)"
                )
                conn.execute(
                    """
                    INSERT INTO situation_airports (
                        situation_id, airport_id, airport_name, facility_type, role,
                        longitude, latitude, scheduled_service, runways_known,
                        configuration_complete, capacity_per_window, support_level
                    ) VALUES ('S1', 'A1', 'Airport', 'small_airport', 'military',
                              110, 30, 0, 0, 0, 8, NULL)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO situation_resource_stocks (
                        situation_id, airport_id, resource_type_id, quantity,
                        replenishment_capacity_per_window
                    ) VALUES ('S1', 'A1', 'R1', 90, 0)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO situation_resource_replenishments (
                        situation_id, airport_id, resource_type_id, slot, quantity
                    ) VALUES ('S1', 'A1', 'R1', 3, 5)
                    """
                )

            initialize_database(path)

            with connect(path) as conn:
                airport_zero = conn.execute(
                    """
                    SELECT replenishment_capacity_per_window
                    FROM airport_resource_stocks
                    WHERE airport_id='A1' AND resource_type_id='R1'
                    """
                ).fetchone()[0]
                situation_zero = conn.execute(
                    """
                    SELECT replenishment_capacity_per_window
                    FROM situation_resource_stocks
                    WHERE situation_id='S1' AND airport_id='A1' AND resource_type_id='R1'
                    """
                ).fetchone()[0]
                child_row = tuple(conn.execute(
                    """
                    SELECT situation_id, airport_id, resource_type_id, slot, quantity
                    FROM situation_resource_replenishments
                    """
                ).fetchone())

                conn.execute(
                    """
                    INSERT INTO airport_resource_stocks (
                        airport_id, resource_type_id, quantity, replenishment_capacity_per_window
                    ) VALUES ('A1', 'R2', 50, NULL)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO situation_resource_stocks (
                        situation_id, airport_id, resource_type_id, quantity,
                        replenishment_capacity_per_window
                    ) VALUES ('S1', 'A1', 'R2', 40, NULL)
                    """
                )
                airport_unknown = conn.execute(
                    """
                    SELECT replenishment_capacity_per_window
                    FROM airport_resource_stocks
                    WHERE airport_id='A1' AND resource_type_id='R2'
                    """
                ).fetchone()[0]
                situation_unknown = conn.execute(
                    """
                    SELECT replenishment_capacity_per_window
                    FROM situation_resource_stocks
                    WHERE situation_id='S1' AND airport_id='A1' AND resource_type_id='R2'
                    """
                ).fetchone()[0]
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        """
                        UPDATE airport_resource_stocks
                        SET replenishment_capacity_per_window=-1
                        WHERE airport_id='A1' AND resource_type_id='R2'
                        """
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        """
                        UPDATE situation_resource_stocks
                        SET replenishment_capacity_per_window=-1
                        WHERE situation_id='S1' AND airport_id='A1' AND resource_type_id='R2'
                        """
                    )

                airport_columns = {
                    row[1]: (row[2], row[3], row[5])
                    for row in conn.execute("PRAGMA table_info(airport_resource_stocks)")
                }
                situation_columns = {
                    row[1]: (row[2], row[3], row[5])
                    for row in conn.execute("PRAGMA table_info(situation_resource_stocks)")
                }
                airport_foreign_keys = {
                    (row[3], row[2], row[4], row[6])
                    for row in conn.execute("PRAGMA foreign_key_list(airport_resource_stocks)")
                }
                situation_foreign_keys = {
                    (row[3], row[2], row[4], row[6])
                    for row in conn.execute("PRAGMA foreign_key_list(situation_resource_stocks)")
                }
                foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

            self.assertEqual(0, airport_zero)
            self.assertEqual(0, situation_zero)
            self.assertEqual(("S1", "A1", "R1", 3, 5.0), child_row)
            self.assertIsNone(airport_unknown)
            self.assertIsNone(situation_unknown)
            self.assertEqual([], foreign_key_errors)
            self.assertIn("quantity", airport_columns)
            self.assertNotIn("initial_quantity", airport_columns)
            self.assertIn("quantity", situation_columns)
            self.assertNotIn("initial_quantity", situation_columns)
            self.assertEqual(("REAL", 0, 0), airport_columns["replenishment_capacity_per_window"])
            self.assertEqual(("REAL", 0, 0), situation_columns["replenishment_capacity_per_window"])
            self.assertEqual(1, airport_columns["airport_id"][2])
            self.assertEqual(2, airport_columns["resource_type_id"][2])
            self.assertEqual(1, situation_columns["situation_id"][2])
            self.assertEqual(2, situation_columns["airport_id"][2])
            self.assertEqual(3, situation_columns["resource_type_id"][2])
            self.assertIn(
                ("airport_id", "airport_operational_profiles", "airport_id", "CASCADE"),
                airport_foreign_keys,
            )
            self.assertIn(
                ("resource_type_id", "resource_types", "resource_type_id", "RESTRICT"),
                airport_foreign_keys,
            )
            self.assertIn(
                ("situation_id", "situation_airports", "situation_id", "CASCADE"),
                situation_foreign_keys,
            )
            self.assertIn(
                ("airport_id", "situation_airports", "airport_id", "CASCADE"),
                situation_foreign_keys,
            )
            self.assertIn(
                ("resource_type_id", "resource_types", "resource_type_id", "RESTRICT"),
                situation_foreign_keys,
            )

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
