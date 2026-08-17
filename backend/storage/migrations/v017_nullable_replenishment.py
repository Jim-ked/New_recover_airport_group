from __future__ import annotations

import sqlite3

"""v017: allow unknown replenishment capacity in resource stock tables.

Business semantics (frozen):
- replenishment_capacity_per_window = NULL  -> not configured / unknown
- replenishment_capacity_per_window = 0     -> explicitly confirmed no capacity

v010 introduced the column as NOT NULL DEFAULT 0, which makes the two states
indistinguishable at the storage layer and conflicts with the domain model
(AirportResourceStock.replenishment_capacity_per_window is Optional).
This migration only relaxes the schema to admit NULL going forward; existing
0 values are preserved verbatim and are never reinterpreted.
"""

MIGRATION_ID = "v017_nullable_replenishment"

_AIRPORT_STOCKS_SQL = """
CREATE TABLE airport_resource_stocks_v017 (
    airport_id TEXT NOT NULL,
    resource_type_id TEXT NOT NULL,
    quantity REAL CHECK (quantity IS NULL OR quantity >= 0),
    replenishment_capacity_per_window REAL CHECK (replenishment_capacity_per_window IS NULL OR replenishment_capacity_per_window >= 0),
    PRIMARY KEY (airport_id, resource_type_id),
    FOREIGN KEY (airport_id) REFERENCES airport_operational_profiles(airport_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_type_id) REFERENCES resource_types(resource_type_id) ON DELETE RESTRICT
)
"""

_SITUATION_STOCKS_SQL = """
CREATE TABLE situation_resource_stocks_v017 (
    situation_id TEXT NOT NULL,
    airport_id TEXT NOT NULL,
    resource_type_id TEXT NOT NULL,
    quantity REAL CHECK (quantity IS NULL OR quantity >= 0),
    replenishment_capacity_per_window REAL CHECK (replenishment_capacity_per_window IS NULL OR replenishment_capacity_per_window >= 0),
    PRIMARY KEY (situation_id, airport_id, resource_type_id),
    FOREIGN KEY (situation_id, airport_id) REFERENCES situation_airports(situation_id, airport_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_type_id) REFERENCES resource_types(resource_type_id) ON DELETE RESTRICT
)
"""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _replenishment_not_null(conn: sqlite3.Connection, table: str) -> bool:
    for row in conn.execute(f"PRAGMA table_info({table})"):
        if str(row[1]) == "replenishment_capacity_per_window":
            return bool(row[3])
    return False


def _rebuild(
    conn: sqlite3.Connection,
    table: str,
    create_sql: str,
    columns: tuple[str, ...],
) -> None:
    if not _table_exists(conn, table):
        return
    if not _replenishment_not_null(conn, table):
        return  # already nullable
    tmp = f"{table}_v017"
    # Earlier statements in the migration chain may have left an implicit
    # transaction open; close it so the FK pragma can take effect.
    conn.commit()
    # Rebuilding a parent table under foreign_keys=ON would fire implicit
    # deletes against referencing rows, so the rebuild runs with FK checks
    # suspended and rows are copied verbatim (existing 0 values stay 0).
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(create_sql)
        column_list = ", ".join(columns)
        conn.execute(
            f"INSERT INTO {tmp} ({column_list}) "
            f"SELECT {column_list} FROM {table}"
        )
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def apply(conn: sqlite3.Connection) -> None:
    _rebuild(
        conn,
        "airport_resource_stocks",
        _AIRPORT_STOCKS_SQL,
        (
            "airport_id",
            "resource_type_id",
            "quantity",
            "replenishment_capacity_per_window",
        ),
    )
    _rebuild(
        conn,
        "situation_resource_stocks",
        _SITUATION_STOCKS_SQL,
        (
            "situation_id",
            "airport_id",
            "resource_type_id",
            "quantity",
            "replenishment_capacity_per_window",
        ),
    )
