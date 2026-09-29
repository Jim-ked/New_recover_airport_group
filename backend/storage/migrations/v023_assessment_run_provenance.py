from __future__ import annotations

import sqlite3


_COLUMNS = {
    "metric_kind": "TEXT NOT NULL DEFAULT 'support_index_v1'",
    "matrix_value": "REAL",
    "source_run_id": "TEXT",
    "snapshot_hash": "TEXT",
    "metrics_hash": "TEXT",
    "effective_windows": "INTEGER",
    "unavailable_windows": "INTEGER",
}


def apply(conn: sqlite3.Connection) -> None:
    """Add explicit metric semantics and immutable Run provenance to assessment rows."""
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(assessment_result)").fetchall()
    }
    for name, declaration in _COLUMNS.items():
        if name not in existing:
            conn.execute(
                f"ALTER TABLE assessment_result ADD COLUMN {name} {declaration}"
            )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_assessment_result_source_run "
        "ON assessment_result(source_run_id, airport_id)"
    )


__all__ = ["apply"]
