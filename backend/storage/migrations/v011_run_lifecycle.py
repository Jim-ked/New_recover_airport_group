from __future__ import annotations

SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    situation_id TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    failure_code TEXT,
    failure_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY (run_id) REFERENCES run_input_snapshots(run_id) ON DELETE RESTRICT,
    CHECK (length(trim(run_id)) > 0),
    CHECK (length(trim(owner_user_id)) > 0),
    CHECK (length(trim(situation_id)) > 0),
    CHECK (length(snapshot_hash) = 64),
    CHECK (
        (status = 'failed' AND failure_message IS NOT NULL)
        OR (status <> 'failed' AND failure_code IS NULL AND failure_message IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_runs_owner_created
ON runs(owner_user_id, created_at DESC, run_id DESC);

CREATE INDEX IF NOT EXISTS ix_runs_status_created
ON runs(status, created_at, run_id);

CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK (seq > 0),
    level TEXT NOT NULL CHECK (level IN ('DEBUG','INFO','WARNING','ERROR')),
    stage TEXT NOT NULL CHECK (stage IN (
        'data_preparation',
        'candidate_generation',
        'quick_evaluation',
        'exact_optimization',
        'persistence'
    )),
    event TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    CHECK (length(trim(event)) > 0),
    CHECK (length(trim(message)) > 0)
);

CREATE TABLE IF NOT EXISTS run_results (
    run_id TEXT PRIMARY KEY,
    solution_hash TEXT NOT NULL,
    solution_json TEXT NOT NULL CHECK (json_valid(solution_json)),
    metrics_hash TEXT NOT NULL,
    metrics_json TEXT NOT NULL CHECK (json_valid(metrics_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE RESTRICT,
    CHECK (length(solution_hash) = 64),
    CHECK (length(metrics_hash) = 64)
);
"""
