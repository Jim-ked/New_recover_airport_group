from __future__ import annotations

import sqlite3

from backend.storage.migrations.v004_situation_damage import SCHEMA_SQL


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def apply(conn: sqlite3.Connection) -> None:
    """Upgrade the temporary flat Step5 damage table to Situation->Scenario->Event.

    The old flat rows cannot be migrated losslessly because they used a generic
    damage_degree instead of typed effects. They are therefore preserved read-only in a
    legacy table and never interpreted as canonical damage events.
    """
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "situation_damage_events" in tables:
        cols = _columns(conn, "situation_damage_events")
        if "damage_scenario_id" not in cols:
            if "legacy_situation_damage_events_v004" in tables:
                raise RuntimeError(
                    "cannot migrate flat damage table: legacy preservation table already exists"
                )
            conn.execute(
                "ALTER TABLE situation_damage_events RENAME TO legacy_situation_damage_events_v004"
            )
    conn.executescript(SCHEMA_SQL)
