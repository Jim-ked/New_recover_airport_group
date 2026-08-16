from __future__ import annotations

SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS audit_events (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id TEXT,
    actor_role TEXT,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    request_method TEXT NOT NULL,
    request_path TEXT NOT NULL,
    source_address TEXT,
    response_status INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success','denied','error')),
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(action)) > 0),
    CHECK (length(trim(request_method)) > 0),
    CHECK (length(trim(request_path)) > 0),
    CHECK (response_status BETWEEN 100 AND 599)
);

CREATE INDEX IF NOT EXISTS ix_audit_events_created
ON audit_events(created_at DESC, audit_id DESC);

CREATE INDEX IF NOT EXISTS ix_audit_events_actor_created
ON audit_events(actor_user_id, created_at DESC, audit_id DESC);

CREATE INDEX IF NOT EXISTS ix_audit_events_resource_created
ON audit_events(resource_type, resource_id, created_at DESC, audit_id DESC);
"""
