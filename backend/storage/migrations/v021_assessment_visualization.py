from __future__ import annotations

import sqlite3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assessment_result (
    id TEXT PRIMARY KEY,
    airport_id TEXT NOT NULL REFERENCES airports(airport_id),
    scenario_type TEXT NOT NULL,
    curves TEXT NOT NULL,
    decline_rate REAL NOT NULL,
    recovery_slope REAL NOT NULL,
    task_threshold REAL,
    initial_capacity REAL NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_assessment_result_airport_scenario_created
ON assessment_result(airport_id, scenario_type, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS assessment_airport_risk (
    airport_id TEXT PRIMARY KEY REFERENCES airports(airport_id),
    risk_level INTEGER NOT NULL CHECK(risk_level BETWEEN 1 AND 5),
    risk_score REAL NOT NULL DEFAULT 0 CHECK(risk_score BETWEEN 0 AND 1),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assessment_comparison (
    id TEXT PRIMARY KEY,
    base_airport_id TEXT,
    base_scenario_type TEXT,
    compared_airport_id TEXT,
    compared_scenario_type TEXT,
    conclusion TEXT,
    log TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_assessment_comparison_created
ON assessment_comparison(created_at DESC, id DESC);
"""


def apply(conn: sqlite3.Connection) -> None:
    """Add persisted assessment artifacts without importing an assessment model."""
    conn.executescript(SCHEMA_SQL)


__all__ = ["SCHEMA_SQL", "apply"]
