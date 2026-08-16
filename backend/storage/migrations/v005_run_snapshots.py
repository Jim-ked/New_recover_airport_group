SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS run_input_snapshots (
    run_id TEXT PRIMARY KEY,
    situation_id TEXT NOT NULL,
    situation_content_hash TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(run_id)) > 0),
    CHECK (length(trim(situation_id)) > 0),
    CHECK (length(situation_content_hash) = 64),
    CHECK (length(snapshot_hash) = 64),
    CHECK (json_valid(payload_json))
);
"""
