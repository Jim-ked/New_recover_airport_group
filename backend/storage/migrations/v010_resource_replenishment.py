from __future__ import annotations

import sqlite3


MIGRATION_ID = "v010_resource_replenishment"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_capacity_column(conn: sqlite3.Connection, table: str) -> None:
    if not _table_exists(conn, table):
        return
    cols = _columns(conn, table)
    if "replenishment_capacity_per_window" in cols:
        return
    # Existing rows predate replenishment semantics. Migrating them to zero capacity is
    # conservative: it never invents supply and keeps every historical stock feasible.
    conn.execute(
        f"""
        ALTER TABLE {table}
        ADD COLUMN replenishment_capacity_per_window REAL NOT NULL DEFAULT 0
        CHECK (replenishment_capacity_per_window >= 0)
        """
    )


def apply(conn: sqlite3.Connection) -> None:
    _add_capacity_column(conn, "airport_resource_stocks")
    _add_capacity_column(conn, "situation_resource_stocks")
    if not _table_exists(conn, "situation_resource_stocks"):
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS situation_resource_replenishments (
            situation_id TEXT NOT NULL,
            airport_id TEXT NOT NULL,
            resource_type_id TEXT NOT NULL,
            slot INTEGER NOT NULL CHECK (slot >= 0),
            quantity REAL NOT NULL CHECK (quantity > 0),
            PRIMARY KEY (situation_id, airport_id, resource_type_id, slot),
            FOREIGN KEY (situation_id, airport_id, resource_type_id)
                REFERENCES situation_resource_stocks(situation_id, airport_id, resource_type_id)
                ON DELETE CASCADE
        )
        """
    )
