from __future__ import annotations

import sqlite3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS security_alert (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_security_alert_created
ON security_alert(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS backup_record (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind = 'full'),
    path TEXT NOT NULL,
    encrypted INTEGER NOT NULL DEFAULT 0 CHECK(encrypted IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_report (
    id TEXT PRIMARY KEY,
    period_start TEXT,
    period_end TEXT,
    report_path TEXT NOT NULL,
    format TEXT NOT NULL CHECK(format IN ('csv','pdf')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_audit_report_created
ON audit_report(created_at DESC, id DESC);
"""


def apply(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


__all__ = ["SCHEMA_SQL", "apply"]
