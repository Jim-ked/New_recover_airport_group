from __future__ import annotations

import sqlite3


MIGRATION_ID = "v020_interval_replenishment"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def apply(conn: sqlite3.Connection) -> None:
    required = (
        "airport_resource_stocks",
        "situation_resource_stocks",
        "situation_resource_replenishments",
    )
    if not all(_table_exists(conn, table) for table in required):
        return
    stock_columns = _columns(conn, "airport_resource_stocks")
    replenishment_columns = _columns(conn, "situation_resource_replenishments")
    if (
        "replenishment_capacity_per_window" not in stock_columns
        and "start_slot" in replenishment_columns
    ):
        return

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(
            """
            BEGIN;
            CREATE TABLE airport_resource_stocks_v020 (
                airport_id TEXT NOT NULL,
                resource_type_id TEXT NOT NULL,
                quantity REAL CHECK (quantity IS NULL OR quantity >= 0),
                PRIMARY KEY (airport_id, resource_type_id),
                FOREIGN KEY (airport_id) REFERENCES airport_operational_profiles(airport_id) ON DELETE CASCADE,
                FOREIGN KEY (resource_type_id) REFERENCES resource_types(resource_type_id) ON DELETE RESTRICT
            );

            CREATE TABLE situation_resource_stocks_v020 (
                situation_id TEXT NOT NULL,
                airport_id TEXT NOT NULL,
                resource_type_id TEXT NOT NULL,
                quantity REAL CHECK (quantity IS NULL OR quantity >= 0),
                PRIMARY KEY (situation_id, airport_id, resource_type_id),
                FOREIGN KEY (situation_id, airport_id) REFERENCES situation_airports(situation_id, airport_id) ON DELETE CASCADE,
                FOREIGN KEY (resource_type_id) REFERENCES resource_types(resource_type_id) ON DELETE RESTRICT
            );

            CREATE TABLE situation_resource_replenishments_v020 (
                situation_id TEXT NOT NULL,
                airport_id TEXT NOT NULL,
                resource_type_id TEXT NOT NULL,
                start_slot INTEGER NOT NULL CHECK (start_slot >= 0),
                end_slot INTEGER NOT NULL CHECK (end_slot > start_slot),
                quantity REAL NOT NULL CHECK (quantity > 0),
                PRIMARY KEY (situation_id, airport_id, resource_type_id, start_slot, end_slot),
                FOREIGN KEY (situation_id, airport_id, resource_type_id)
                    REFERENCES situation_resource_stocks(situation_id, airport_id, resource_type_id)
                    ON DELETE CASCADE
            );

            INSERT INTO airport_resource_stocks_v020 (airport_id, resource_type_id, quantity)
            SELECT airport_id, resource_type_id, quantity FROM airport_resource_stocks;

            INSERT INTO situation_resource_stocks_v020 (situation_id, airport_id, resource_type_id, quantity)
            SELECT situation_id, airport_id, resource_type_id, quantity FROM situation_resource_stocks;

            INSERT INTO situation_resource_replenishments_v020 (
                situation_id, airport_id, resource_type_id, start_slot, end_slot, quantity
            )
            SELECT situation_id, airport_id, resource_type_id, slot, slot + 1, quantity
            FROM situation_resource_replenishments;

            DROP TABLE situation_resource_replenishments;
            DROP TABLE situation_resource_stocks;
            DROP TABLE airport_resource_stocks;
            ALTER TABLE airport_resource_stocks_v020 RENAME TO airport_resource_stocks;
            ALTER TABLE situation_resource_stocks_v020 RENAME TO situation_resource_stocks;
            ALTER TABLE situation_resource_replenishments_v020 RENAME TO situation_resource_replenishments;
            COMMIT;
            """
        )
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
