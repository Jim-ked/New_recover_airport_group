from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(situations)").fetchall()}
    if "owner_user_id" not in cols:
        conn.execute("ALTER TABLE situations ADD COLUMN owner_user_id TEXT")
    if "created_at" not in cols:
        conn.execute("ALTER TABLE situations ADD COLUMN created_at TEXT")
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE situations ADD COLUMN updated_at TEXT")

    # Existing pre-owner records remain explicitly unowned. They are not silently
    # assigned to the user who first happens to read/run them; admin can inspect and
    # migrate/claim them in a future explicit Situation-management slice.
    conn.execute(
        "UPDATE situations SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP), "
        "updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_situations_owner_updated "
        "ON situations(owner_user_id, updated_at DESC, situation_id)"
    )
