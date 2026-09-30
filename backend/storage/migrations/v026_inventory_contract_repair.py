from __future__ import annotations

import sqlite3


MIGRATION_ID = "v026_inventory_contract_repair"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _repair_stock_table(conn: sqlite3.Connection, table: str) -> None:
    if not _table_exists(conn, table):
        return
    columns = _columns(conn, table)
    definitions = {
        "quantity": "REAL CHECK (quantity IS NULL OR quantity >= 0)",
        "replenishment_capacity_per_window": (
            "REAL CHECK (replenishment_capacity_per_window IS NULL "
            "OR replenishment_capacity_per_window >= 0)"
        ),
        "storage_capacity": (
            "REAL CHECK (storage_capacity IS NULL OR storage_capacity >= 0)"
        ),
    }
    for name, sql_type in definitions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def apply(conn: sqlite3.Connection) -> None:
    # Inspect physical schema rather than trusting historical ledger entries. Some
    # databases record the orphan v020 dynamic-inventory migration although its columns
    # are absent. Nullable columns preserve that unknown state without inventing facts.
    _repair_stock_table(conn, "airport_resource_stocks")
    _repair_stock_table(conn, "situation_resource_stocks")
