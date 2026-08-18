from __future__ import annotations

import sqlite3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS identifier_sequences (
    namespace TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL CHECK(next_value >= 1)
)
"""


def apply(conn: sqlite3.Connection) -> None:
    """Install monotonic project-ID sequences without changing existing records."""
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO identifier_sequences(namespace, next_value) VALUES ('airport', 1), ('situation', 1)"
    )


__all__ = ["SCHEMA_SQL", "apply"]
