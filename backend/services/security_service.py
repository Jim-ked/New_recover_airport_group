from __future__ import annotations

import csv
import io
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.domain.security import AuditReport, BackupRecord, SecurityAlert
from backend.storage.audit_repository import AuditEventRecord, AuditRepository
from backend.storage.security_repository import SecurityRepository


class SecurityServiceError(RuntimeError):
    pass


class SecurityService:
    def __init__(
        self,
        repository: SecurityRepository,
        *,
        db_path: str | Path,
        backup_root: str | Path,
        report_root: str | Path,
        audit_repository: Optional[AuditRepository] = None,
    ) -> None:
        self.repository = repository
        self.db_path = Path(db_path).resolve()
        self.backup_root = Path(backup_root).resolve()
        self.report_root = Path(report_root).resolve()
        self.audit_repository = audit_repository or AuditRepository(db_path)

    @staticmethod
    def _date(value: Optional[str], field: str) -> Optional[str]:
        if value is None or not str(value).strip():
            return None
        text = str(value).strip()
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise SecurityServiceError(f"{field} must be YYYY-MM-DD") from exc
        return text

    @staticmethod
    def _inside(root: Path, relative_path: str, purpose: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise SecurityServiceError(f"{purpose} path is outside the approved {purpose} directory")
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise SecurityServiceError(f"{purpose} path is outside the approved {purpose} directory") from exc
        return resolved

    def raise_alert(self, kind: str, message: str, *, severity: str = "warning") -> SecurityAlert:
        alert = SecurityAlert.from_mapping(
            {"id": str(uuid.uuid4()), "kind": kind, "message": message, "severity": severity}
        )
        return self.repository.save_alert(alert)

    def list_alerts(self, *, severity: Optional[str] = None) -> list[SecurityAlert]:
        if severity is not None and severity not in {"info", "warning", "critical"}:
            raise SecurityServiceError("severity must be info, warning or critical")
        return self.repository.list_alerts(severity=severity)

    def create_backup(self, *, kind: str = "full", encrypted: bool = False) -> BackupRecord:
        if kind == "incremental":
            raise SecurityServiceError("incremental backup is not implemented; use a full backup")
        if kind != "full":
            raise SecurityServiceError("kind must be full")
        if encrypted:
            raise SecurityServiceError("backup encryption is not configured; encrypted backup cannot be claimed")
        if not self.db_path.is_file():
            raise SecurityServiceError("source database does not exist")
        self.backup_root.mkdir(parents=True, exist_ok=True)
        backup_id = str(uuid.uuid4())
        relative = f"backup_full_{backup_id}.sqlite3"
        target = self._inside(self.backup_root, relative, "backup")
        source_connection = sqlite3.connect(str(self.db_path))
        target_connection = sqlite3.connect(str(target))
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        record = BackupRecord(id=backup_id, kind="full", path=relative, encrypted=False)
        try:
            return self.repository.save_backup(record)
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def list_backups(self) -> list[BackupRecord]:
        return self.repository.list_backups()

    def restore_backup(self, backup_id: str) -> BackupRecord:
        record = self.repository.get_backup(backup_id)
        if record is None:
            raise SecurityServiceError("backup record was not found")
        source = self._inside(self.backup_root, record.path, "backup")
        if not source.is_file():
            raise SecurityServiceError("backup file is missing")
        verification = None
        try:
            verification = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
            if verification.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise SecurityServiceError("backup database integrity check failed")
        except sqlite3.DatabaseError as exc:
            raise SecurityServiceError("backup file is not a valid SQLite database") from exc
        finally:
            if verification is not None:
                verification.close()
        source_connection = sqlite3.connect(str(source))
        target_connection = sqlite3.connect(str(self.db_path))
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        return record

    def delete_backup(self, backup_id: str) -> bool:
        record = self.repository.get_backup(backup_id)
        if record is None:
            return False
        target = self._inside(self.backup_root, record.path, "backup")
        removed = self.repository.delete_backup(backup_id)
        if removed and target.is_file():
            target.unlink()
        return removed

    def _audit_events(self, start: Optional[str], end: Optional[str]) -> list[AuditEventRecord]:
        items, _ = self.audit_repository.query(
            created_after=f"{start} 00:00:00" if start else None,
            created_before=f"{end} 23:59:59" if end else None,
            limit=500,
        )
        return list(reversed(items))

    @staticmethod
    def _csv_report(events: list[AuditEventRecord]) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["时间", "用户", "角色", "操作", "对象", "请求", "状态码", "结果"])
        for event in events:
            writer.writerow(
                [
                    event.created_at,
                    event.actor_user_id or "",
                    event.actor_role or "",
                    event.action,
                    event.resource_type or "",
                    f"{event.request_method} {event.request_path}",
                    event.response_status,
                    event.outcome,
                ]
            )
        return output.getvalue().encode("utf-8-sig")

    @staticmethod
    def _pdf_report(events: list[AuditEventRecord], start: Optional[str], end: Optional[str]) -> bytes:
        try:
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
            from reportlab.lib import colors
            from reportlab.lib.styles import getSampleStyleSheet
        except ImportError as exc:  # pragma: no cover
            raise SecurityServiceError("PDF report generation requires reportlab") from exc
        output = io.BytesIO()
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        styles = getSampleStyleSheet()
        styles["Title"].fontName = "STSong-Light"
        document = SimpleDocTemplate(output, pagesize=landscape(A4))
        rows = [["时间", "用户", "操作", "请求", "状态", "结果"]]
        rows.extend(
            [[event.created_at, event.actor_user_id or "—", event.action, f"{event.request_method} {event.request_path}", str(event.response_status), event.outcome] for event in events]
        )
        table = Table(rows, repeatRows=1)
        table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "STSong-Light"), ("GRID", (0, 0), (-1, -1), .25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("FONTSIZE", (0, 0), (-1, -1), 8)]))
        document.build([Paragraph("安全审计报告", styles["Title"]), Paragraph(f"统计范围：{start or '最早记录'} 至 {end or '最新记录'}", styles["Normal"]), Spacer(1, 8), table])
        return output.getvalue()

    def generate_audit_report(
        self,
        *,
        format: str = "csv",
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
    ) -> AuditReport:
        if format not in {"csv", "pdf"}:
            raise SecurityServiceError("format must be csv or pdf")
        start = self._date(period_start, "period_start")
        end = self._date(period_end, "period_end")
        if start and end and start > end:
            raise SecurityServiceError("period_start must not be after period_end")
        events = self._audit_events(start, end)
        report_id = str(uuid.uuid4())
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        relative = f"audit_{stamp}_{report_id[:8]}.{format}"
        target = self._inside(self.report_root, relative, "report")
        self.report_root.mkdir(parents=True, exist_ok=True)
        content = self._pdf_report(events, start, end) if format == "pdf" else self._csv_report(events)
        target.write_bytes(content)
        report = AuditReport(
            id=report_id,
            report_path=relative,
            format=format,
            period_start=start,
            period_end=end,
        )
        try:
            return self.repository.save_audit_report(report)
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def list_audit_reports(self) -> list[AuditReport]:
        return self.repository.list_audit_reports()

    def read_audit_report(self, report_id: str) -> tuple[bytes, str, str]:
        report = self.repository.get_audit_report(report_id)
        if report is None:
            raise SecurityServiceError("audit report was not found")
        target = self._inside(self.report_root, report.report_path, "report")
        if not target.is_file():
            raise SecurityServiceError("audit report file is missing")
        mimetype = "application/pdf" if report.format == "pdf" else "text/csv; charset=utf-8"
        return target.read_bytes(), mimetype, target.name


__all__ = ["SecurityService", "SecurityServiceError"]
