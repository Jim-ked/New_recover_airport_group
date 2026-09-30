from __future__ import annotations

import sqlite3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assessment_run_evaluation (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    snapshot_hash TEXT NOT NULL CHECK(length(snapshot_hash)=64),
    metrics_hash TEXT NOT NULL CHECK(length(metrics_hash)=64),
    indicator_set_id TEXT NOT NULL REFERENCES indicator_sets(indicator_set_id),
    indicator_set_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('available','unavailable')),
    unavailable_reason TEXT,
    calculation_version TEXT NOT NULL,
    weight_source TEXT NOT NULL CHECK(weight_source IN ('expert','ahp')),
    weight_trace_json TEXT NOT NULL,
    support_index REAL CHECK(support_index IS NULL OR support_index BETWEEN 0 AND 1),
    overall_membership_json TEXT,
    weighted_composite REAL CHECK(weighted_composite IS NULL OR weighted_composite BETWEEN 0 AND 1),
    risk_score REAL CHECK(risk_score IS NULL OR risk_score BETWEEN 0 AND 1),
    risk_level INTEGER CHECK(risk_level IS NULL OR risk_level BETWEEN 1 AND 5),
    risk_status TEXT NOT NULL CHECK(risk_status IN ('available','unavailable')),
    risk_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK((status='available' AND support_index IS NOT NULL AND unavailable_reason IS NULL)
       OR (status='unavailable' AND support_index IS NULL AND unavailable_reason IS NOT NULL)),
    CHECK((risk_status='available' AND risk_score IS NOT NULL AND risk_level IS NOT NULL)
       OR risk_status='unavailable')
);
CREATE INDEX IF NOT EXISTS ix_assessment_run_evaluation_status
ON assessment_run_evaluation(status, updated_at DESC, run_id);

CREATE TABLE IF NOT EXISTS assessment_indicator_value (
    run_id TEXT NOT NULL REFERENCES assessment_run_evaluation(run_id) ON DELETE CASCADE,
    indicator_id TEXT NOT NULL REFERENCES indicator_nodes(indicator_id),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    raw_value REAL NOT NULL CHECK(raw_value BETWEEN 0 AND 1),
    normalized_value REAL NOT NULL CHECK(normalized_value BETWEEN 0 AND 1),
    weight REAL NOT NULL CHECK(weight >= 0),
    weight_source TEXT NOT NULL CHECK(weight_source IN ('expert','ahp')),
    calculation_type TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY(run_id, indicator_id)
);
CREATE INDEX IF NOT EXISTS ix_assessment_indicator_value_code
ON assessment_indicator_value(code, run_id);
"""


def apply(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


__all__ = ["SCHEMA_SQL", "apply"]
