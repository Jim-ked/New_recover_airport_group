from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Tuple

from backend.storage.migrations.v001_airport_data import SCHEMA_SQL as V001_SQL
from backend.storage.migrations.v002_situations import SCHEMA_SQL as V002_SQL
from backend.storage.migrations.v003_aircraft_type_semantics import apply as apply_v003
from backend.storage.migrations.v004_situation_damage import SCHEMA_SQL as V004_SQL
from backend.storage.migrations.v005_run_snapshots import SCHEMA_SQL as V005_SQL
from backend.storage.migrations.v006_situation_content_hash import apply as apply_v006
from backend.storage.migrations.v007_capacity_occupancy_factors import apply as apply_v007
from backend.storage.migrations.v008_damage_scenarios import apply as apply_v008
from backend.storage.migrations.v009_mission_records import SCHEMA_SQL as V009_SQL
from backend.storage.migrations.v010_resource_replenishment import apply as apply_v010
from backend.storage.migrations.v011_run_lifecycle import SCHEMA_SQL as V011_SQL
from backend.storage.migrations.v012_situation_metadata import apply as apply_v012
from backend.storage.migrations.v013_catalog_metadata import apply as apply_v013
from backend.storage.migrations.v014_indicators import apply as apply_v014
from backend.storage.migrations.v015_audit_events import SCHEMA_SQL as V015_SQL
from backend.storage.migrations.v016_users import SCHEMA_SQL as V016_SQL


class _ClosingConnection(sqlite3.Connection):
    def __enter__(self):
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _script(sql: str) -> Callable[[sqlite3.Connection], None]:
    def apply(conn: sqlite3.Connection) -> None:
        conn.executescript(sql)
    return apply


_MIGRATIONS: Tuple[Tuple[str, Callable[[sqlite3.Connection], None]], ...] = (
    ("v001_airport_data", _script(V001_SQL)),
    ("v002_situations", _script(V002_SQL)),
    ("v003_aircraft_type_semantics", apply_v003),
    ("v004_situation_damage", _script(V004_SQL)),
    ("v005_run_snapshots", _script(V005_SQL)),
    ("v006_situation_content_hash", apply_v006),
    ("v007_capacity_occupancy_factors", apply_v007),
    ("v008_damage_scenarios", apply_v008),
    ("v009_mission_records", _script(V009_SQL)),
    ("v010_resource_replenishment", apply_v010),
    ("v011_run_lifecycle", _script(V011_SQL)),
    ("v012_situation_metadata", apply_v012),
    ("v013_catalog_metadata", apply_v013),
    ("v014_indicators", apply_v014),
    ("v015_audit_events", _script(V015_SQL)),
    ("v016_users", _script(V016_SQL)),
)


def initialize_database(db_path: str | Path) -> None:
    """Apply non-destructive, ordered migrations to the single business SQLite authority."""
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {
            row["migration_id"]
            for row in conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
        }
        for migration_id, apply in _MIGRATIONS:
            if migration_id in applied:
                continue
            apply(conn)
            conn.execute(
                "INSERT INTO schema_migrations (migration_id) VALUES (?)",
                (migration_id,),
            )
