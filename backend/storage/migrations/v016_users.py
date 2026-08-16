from __future__ import annotations

SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    login_name TEXT NOT NULL UNIQUE,
    display_name TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('viewer','operator','admin')),
    is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0,1)),
    auth_revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_users_role ON users(role, is_disabled, login_name);
"""
