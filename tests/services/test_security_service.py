from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.domain.security import AuditReport, BackupRecord
from backend.services.security_service import SecurityService, SecurityServiceError
from backend.storage.database import initialize_database
from backend.storage.security_repository import SecurityRepository


class SecurityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "db" / "test.sqlite3"
        self.db_path.parent.mkdir(parents=True)
        initialize_database(self.db_path)
        self.backup_root = root / "backups"
        self.report_root = root / "reports"
        self.repository = SecurityRepository(self.db_path)
        self.service = SecurityService(
            self.repository,
            db_path=self.db_path,
            backup_root=self.backup_root,
            report_root=self.report_root,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_backup_is_real_unencrypted_sqlite_and_can_restore(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("CREATE TABLE protected_fact(value TEXT NOT NULL)")
            connection.execute("INSERT INTO protected_fact VALUES ('before')")
            connection.commit()
        finally:
            connection.close()
        record = self.service.create_backup()
        self.assertEqual("full", record.kind)
        self.assertFalse(record.encrypted)
        backup_path = self.backup_root / record.path
        self.assertTrue(backup_path.is_file())
        self.assertEqual(b"SQLite format 3\x00", backup_path.read_bytes()[:16])

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("UPDATE protected_fact SET value='after'")
            connection.commit()
        finally:
            connection.close()
        self.service.restore_backup(record.id)
        connection = sqlite3.connect(self.db_path)
        try:
            value = connection.execute("SELECT value FROM protected_fact").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual("before", value)

    def test_incremental_and_fake_encrypted_backups_are_rejected(self) -> None:
        with self.assertRaisesRegex(SecurityServiceError, "incremental"):
            self.service.create_backup(kind="incremental")
        with self.assertRaisesRegex(SecurityServiceError, "encryption"):
            self.service.create_backup(encrypted=True)

    def test_restore_rejects_recorded_path_outside_approved_root(self) -> None:
        outside = Path(self.temp_dir.name) / "outside.sqlite3"
        outside.write_bytes(b"not a database")
        self.repository.save_backup(
            BackupRecord(id="unsafe", kind="full", path=str(outside), encrypted=False)
        )
        with self.assertRaisesRegex(SecurityServiceError, "approved backup directory"):
            self.service.restore_backup("unsafe")

    def test_restore_rejects_invalid_sqlite_inside_approved_root(self) -> None:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        invalid = self.backup_root / "invalid.sqlite3"
        invalid.write_bytes(b"not a SQLite database")
        self.repository.save_backup(
            BackupRecord(id="invalid-sqlite", kind="full", path=invalid.name, encrypted=False)
        )

        with self.assertRaisesRegex(SecurityServiceError, "not a valid SQLite database"):
            self.service.restore_backup("invalid-sqlite")

    def test_audit_report_is_generated_inside_root_and_tampered_path_is_rejected(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "INSERT INTO audit_events (actor_user_id, actor_role, action, request_method, "
                "request_path, response_status, outcome) VALUES "
                "('admin','admin','POST /api/runs','POST','/api/runs',201,'success')"
            )
            connection.commit()
        finally:
            connection.close()
        report = self.service.generate_audit_report(format="csv")
        report_path = self.report_root / report.report_path
        self.assertTrue(report_path.is_file())
        content, mimetype, filename = self.service.read_audit_report(report.id)
        self.assertIn(b"POST /api/runs", content)
        self.assertTrue(mimetype.startswith("text/csv"))
        self.assertEqual(report_path.name, filename)

        outside = Path(self.temp_dir.name) / "outside.csv"
        outside.write_text("secret", encoding="utf-8")
        self.repository.save_audit_report(AuditReport(id="unsafe-report", report_path=str(outside)))
        with self.assertRaisesRegex(SecurityServiceError, "approved report directory"):
            self.service.read_audit_report("unsafe-report")

    def test_alerts_are_persisted_and_filterable(self) -> None:
        self.service.raise_alert("login_failure", "连续登录失败", severity="critical")
        self.service.raise_alert("worker_delay", "Worker 延迟", severity="warning")
        self.assertEqual(2, len(self.service.list_alerts()))
        self.assertEqual("login_failure", self.service.list_alerts(severity="critical")[0].kind)


if __name__ == "__main__":
    unittest.main()
