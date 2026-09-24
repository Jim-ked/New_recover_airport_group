from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def apply(conn: sqlite3.Connection) -> None:
    # Optional columns preserve every existing catalog and Situation snapshot row.
    _add_column(
        conn,
        "airports",
        "parking_stand_count INTEGER CHECK (parking_stand_count IS NULL OR parking_stand_count >= 0)",
    )
    _add_column(
        conn,
        "airport_operational_profiles",
        "emergency_response_level TEXT CHECK (emergency_response_level IS NULL OR emergency_response_level IN ('level_1','level_2','level_3','level_4','level_5'))",
    )
    _add_column(
        conn,
        "situation_airports",
        "parking_stand_count INTEGER CHECK (parking_stand_count IS NULL OR parking_stand_count >= 0)",
    )
    _add_column(
        conn,
        "situation_airports",
        "emergency_response_level TEXT CHECK (emergency_response_level IS NULL OR emergency_response_level IN ('level_1','level_2','level_3','level_4','level_5'))",
    )
