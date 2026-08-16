from __future__ import annotations

import sqlite3


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?", (table,)
    ).fetchone() is not None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_meta(conn: sqlite3.Connection, table: str) -> None:
    if not _table_exists(conn, table):
        return
    cols = _cols(conn, table)
    if "revision" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    if "created_at" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN created_at TEXT")
    if "updated_at" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT")
    conn.execute(
        f"UPDATE {table} SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP), "
        f"updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP), revision = COALESCE(revision, 1)"
    )


def apply(conn: sqlite3.Connection) -> None:
    # Metadata is attached to user-maintained catalog roots. Child rows are versioned as
    # part of their owning root object and therefore do not need separate revisions.
    for table in ("airports", "mission_records", "aircraft_types", "resource_types"):
        _ensure_meta(conn, table)

    if _table_exists(conn, "airports"):
        conn.execute("CREATE INDEX IF NOT EXISTS ix_airports_region_role ON airports(region, role, airport_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_airports_updated ON airports(updated_at DESC, airport_id)")
    if _table_exists(conn, "mission_records"):
        conn.execute("CREATE INDEX IF NOT EXISTS ix_mission_records_updated ON mission_records(updated_at DESC, mission_id)")
