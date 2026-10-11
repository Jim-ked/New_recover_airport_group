"""Replace only an empty legacy table. Never infer formal history from research labels."""
import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    # No executescript: SQLite DDL and the caller's migration marker share the transaction.
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assessment_comparison'").fetchone()
    if exists and conn.execute('SELECT COUNT(*) FROM assessment_comparison').fetchone()[0]:
        raise RuntimeError('v027 refused: nonempty legacy assessment_comparison; preserve and isolate historical evidence')
    if exists:
        conn.execute('DROP TABLE assessment_comparison')
    hash_check = "length({0})=64 AND {0} NOT GLOB '*[^0-9a-f]*'"
    conn.execute('''CREATE TABLE assessment_comparison (
        id TEXT PRIMARY KEY,
        comparison_mode TEXT NOT NULL CHECK(comparison_mode IN ('damage_effect','cluster_effect','multi_scenario','configuration')),
        base_run_id TEXT NOT NULL,
        compared_run_id TEXT NOT NULL CHECK(compared_run_id != base_run_id),
        base_snapshot_hash TEXT NOT NULL CHECK(''' + hash_check.format('base_snapshot_hash') + '''),
        base_metrics_hash TEXT NOT NULL CHECK(''' + hash_check.format('base_metrics_hash') + '''),
        compared_snapshot_hash TEXT NOT NULL CHECK(''' + hash_check.format('compared_snapshot_hash') + '''),
        compared_metrics_hash TEXT NOT NULL CHECK(''' + hash_check.format('compared_metrics_hash') + '''),
        comparison_version TEXT NOT NULL,
        created_by_user_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        conclusion TEXT NOT NULL,
        result_json TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE CHECK(''' + hash_check.format('idempotency_key') + ''')
    )''')
    conn.execute('CREATE INDEX idx_comparison_creator_page ON assessment_comparison(created_by_user_id, created_at DESC, id DESC)')
    conn.execute('CREATE INDEX idx_comparison_page ON assessment_comparison(created_at DESC, id DESC)')
