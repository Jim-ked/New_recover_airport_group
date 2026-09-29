from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from backend.domain.security import AuditReport, BackupRecord, SecurityAlert


class SecurityRepositoryError(RuntimeError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class SecurityRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def save_alert(self, alert: SecurityAlert) -> SecurityAlert:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO security_alert (id, kind, message, severity, created_at) "
                "VALUES (?, ?, ?, ?, COALESCE(?, datetime('now')))",
                (alert.id, alert.kind, alert.message, alert.severity, alert.created_at),
            )
        return alert

    def list_alerts(self, *, severity: Optional[str] = None, limit: int = 200) -> list[SecurityAlert]:
        if not 1 <= limit <= 500:
            raise SecurityRepositoryError("limit must be in [1,500]")
        where = "WHERE severity=?" if severity else ""
        params = [severity] if severity else []
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM security_alert {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [SecurityAlert.from_mapping(dict(row)) for row in rows]

    def save_backup(self, record: BackupRecord) -> BackupRecord:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO backup_record (id, kind, path, encrypted, created_at) "
                "VALUES (?, ?, ?, ?, COALESCE(?, datetime('now')))",
                (record.id, record.kind, record.path, int(record.encrypted), record.created_at),
            )
        return record

    def get_backup(self, backup_id: str) -> Optional[BackupRecord]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM backup_record WHERE id=?", (backup_id,)).fetchone()
        if row is None:
            return None
        raw = dict(row); raw["encrypted"] = bool(raw["encrypted"])
        return BackupRecord.from_mapping(raw)

    def list_backups(self, *, limit: int = 100) -> list[BackupRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM backup_record ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        records = []
        for row in rows:
            raw = dict(row); raw["encrypted"] = bool(raw["encrypted"])
            records.append(BackupRecord.from_mapping(raw))
        return records

    def delete_backup(self, backup_id: str) -> bool:
        with self.connect() as conn:
            result = conn.execute("DELETE FROM backup_record WHERE id=?", (backup_id,))
        return result.rowcount > 0

    def save_audit_report(self, report: AuditReport) -> AuditReport:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_report (id, period_start, period_end, report_path, format, created_at) "
                "VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
                (report.id, report.period_start, report.period_end, report.report_path, report.format, report.created_at),
            )
        return report

    def get_audit_report(self, report_id: str) -> Optional[AuditReport]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM audit_report WHERE id=?", (report_id,)).fetchone()
        return AuditReport.from_mapping(dict(row)) if row is not None else None

    def list_audit_reports(self, *, limit: int = 100) -> list[AuditReport]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_report ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [AuditReport.from_mapping(dict(row)) for row in rows]


__all__ = ["SecurityRepository", "SecurityRepositoryError"]
