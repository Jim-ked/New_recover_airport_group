from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    """Split the legacy single aircraft capacity factor into explicit departure/arrival fields.

    Existing databases may have only `capacity_factor`. That legacy value historically
    applied to both departure and arrival load, so the one-time migration copies it into
    both new columns. New writes never use or silently fall back to the legacy column.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(aircraft_types)").fetchall()}
    if "departure_capacity_occupancy_factor" not in cols:
        conn.execute(
            "ALTER TABLE aircraft_types ADD COLUMN departure_capacity_occupancy_factor REAL "
            "CHECK (departure_capacity_occupancy_factor IS NULL OR departure_capacity_occupancy_factor > 0)"
        )
    if "arrival_capacity_occupancy_factor" not in cols:
        conn.execute(
            "ALTER TABLE aircraft_types ADD COLUMN arrival_capacity_occupancy_factor REAL "
            "CHECK (arrival_capacity_occupancy_factor IS NULL OR arrival_capacity_occupancy_factor > 0)"
        )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(aircraft_types)").fetchall()}
    if "capacity_factor" in cols:
        conn.execute(
            "UPDATE aircraft_types SET "
            "departure_capacity_occupancy_factor = COALESCE(departure_capacity_occupancy_factor, capacity_factor), "
            "arrival_capacity_occupancy_factor = COALESCE(arrival_capacity_occupancy_factor, capacity_factor)"
        )
