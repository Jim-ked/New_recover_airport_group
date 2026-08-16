from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(situations)").fetchall()}
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE situations ADD COLUMN content_hash TEXT")
