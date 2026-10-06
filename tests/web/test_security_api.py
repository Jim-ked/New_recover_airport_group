from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.auth.principal import Principal
from backend.services.security_service import SecurityService
from backend.storage.audit_repository import AuditRepository
from backend.storage.database import initialize_database
from backend.storage.security_repository import SecurityRepository
from backend.web.app import create_app
from backend.web.composition import build_run_api
from backend.web.security_api import SecurityApi


class SecurityApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "security.sqlite3"
        initialize_database(self.db_path)
        self.api = SecurityApi(
            SecurityService(
                SecurityRepository(self.db_path),
                db_path=self.db_path,
                backup_root=root / "backups",
                report_root=root / "reports",
                audit_repository=AuditRepository(self.db_path),
            )
        )
        self.admin = Principal("admin", role="admin")
        self.viewer = Principal("viewer", role="viewer")
        self.auditor = Principal("auditor", permissions=frozenset({"audit.read"}))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_admin_creates_honest_full_backup_and_incremental_is_rejected(self) -> None:
        created = self.api.create_backup(
            {"kind": "full", "encrypted": False}, principal=self.admin
        )
        self.assertEqual(201, created.status)
        self.assertFalse(created.body["encrypted"])
        rejected = self.api.create_backup(
            {"kind": "incremental", "encrypted": False}, principal=self.admin
        )
        self.assertEqual(422, rejected.status)
        self.assertIn("incremental", rejected.body["error"]["message"])

    def test_viewer_can_read_but_cannot_mutate_security_data(self) -> None:
        self.api.raise_alert(
            {"kind": "login_failure", "message": "连续登录失败", "severity": "critical"},
            principal=self.admin,
        )
        listed = self.api.list_alerts(principal=self.auditor)
        self.assertEqual(200, listed.status)
        self.assertEqual(1, len(listed.body["items"]))
        denied = self.api.create_backup({}, principal=self.auditor)
        self.assertEqual(403, denied.status)

    def test_report_body_does_not_accept_client_controlled_path(self) -> None:
        response = self.api.generate_audit_report(
            {"format": "csv", "report_path": "../../outside.csv"}, principal=self.admin
        )
        self.assertEqual(400, response.status)
        self.assertEqual("report_path", response.body["error"]["field"])

    def test_flask_download_sets_private_no_store_and_authentication_is_required(self) -> None:
        report = self.api.generate_audit_report({"format": "csv"}, principal=self.admin)
        report_id = report.body["id"]
        app = create_app(
            run_api=build_run_api(self.db_path),
            security_api=self.api,
            principal_resolver=lambda _request: self.admin,
            enable_ui=False,
        )
        downloaded = app.test_client().get(f"/api/security/audit-reports/{report_id}/file")
        self.assertEqual(200, downloaded.status_code)
        self.assertEqual("text/csv", downloaded.mimetype)
        self.assertTrue(downloaded.data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual("private, no-store", downloaded.headers["Cache-Control"])
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])

        denied_app = create_app(
            run_api=build_run_api(self.db_path),
            security_api=self.api,
            principal_resolver=lambda _request: None,
            enable_ui=False,
        )
        denied = denied_app.test_client().get("/api/security/backups")
        self.assertEqual(401, denied.status_code)

    def test_generated_pdf_can_be_downloaded_with_get(self) -> None:
        report = self.api.generate_audit_report({"format": "pdf"}, principal=self.admin)
        report_id = report.body["id"]
        app = create_app(
            run_api=build_run_api(self.db_path),
            security_api=self.api,
            principal_resolver=lambda _request: self.admin,
            enable_ui=False,
        )

        downloaded = app.test_client().get(
            f"/api/security/audit-reports/{report_id}/file"
        )

        self.assertEqual(200, downloaded.status_code)
        self.assertEqual("application/pdf", downloaded.mimetype)
        self.assertTrue(downloaded.data.startswith(b"%PDF"))

    def test_missing_report_download_returns_404(self) -> None:
        app = create_app(
            run_api=build_run_api(self.db_path),
            security_api=self.api,
            principal_resolver=lambda _request: self.admin,
            enable_ui=False,
        )

        downloaded = app.test_client().get(
            "/api/security/audit-reports/missing-report/file"
        )

        self.assertEqual(404, downloaded.status_code)

    def test_invalid_report_id_is_rejected(self) -> None:
        response = self.api.download_audit_report("..", principal=self.admin)

        self.assertEqual(422, response.status)
        self.assertIn("invalid", response.body["error"]["message"])

    def test_report_download_requires_audit_read_permission(self) -> None:
        report = self.api.generate_audit_report({"format": "csv"}, principal=self.admin)
        report_id = report.body["id"]
        no_permissions = Principal("restricted-user", permissions=frozenset())
        app = create_app(
            run_api=build_run_api(self.db_path),
            security_api=self.api,
            principal_resolver=lambda _request: no_permissions,
            enable_ui=False,
        )

        downloaded = app.test_client().get(
            f"/api/security/audit-reports/{report_id}/file"
        )

        self.assertEqual(403, downloaded.status_code)


if __name__ == "__main__":
    unittest.main()
