from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    """Add fields whose semantics were explicitly frozen after the initial schema.

    max_range_km is a single-leg maximum range.
    reserve_ratio is the aircraft's own mandatory fuel-safety reserve ratio.
    Neither field is an airport-resource-stock reserve.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(aircraft_types)").fetchall()}
    if "max_range_km" not in cols:
        conn.execute(
            "ALTER TABLE aircraft_types ADD COLUMN max_range_km REAL "
            "CHECK (max_range_km IS NULL OR max_range_km > 0)"
        )
    if "reserve_ratio" not in cols:
        conn.execute(
            "ALTER TABLE aircraft_types ADD COLUMN reserve_ratio REAL "
            "CHECK (reserve_ratio IS NULL OR (reserve_ratio >= 0 AND reserve_ratio < 1))"
        )
