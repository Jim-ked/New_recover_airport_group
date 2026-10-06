from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "frontend/templates/pages/settings.html").read_text(encoding="utf-8")
JS = (ROOT / "frontend/static/js/modules/settings-security.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/static/css/settings.css").read_text(encoding="utf-8")


class SettingsSecurityContractTests(unittest.TestCase):
    def test_admin_security_workspace_has_three_capabilities(self) -> None:
        for token in (
            'data-settings-tab="security"',
            'data-permission="audit.read"',
            'id="securityWorkspace"',
            "异常预警",
            "数据备份与恢复",
            "安全审计报告",
        ):
            self.assertIn(token, HTML)

    def test_frontend_uses_security_api_and_does_not_offer_fake_incremental_or_encryption(self) -> None:
        for endpoint in (
            "/api/security/alerts",
            "/api/security/backups",
            "/api/security/audit-reports",
        ):
            self.assertIn(endpoint, JS)
        self.assertNotIn("incremental", HTML.lower())
        self.assertNotIn("encrypted:true", JS.replace(" ", "").lower())
        self.assertIn("未加密", JS)

    def test_module_and_scoped_styles_are_loaded(self) -> None:
        self.assertIn("settings-security.js", HTML)
        for token in (".security-workspace", ".security-chip", ".security-grid"):
            self.assertIn(token, CSS)

    def test_existing_report_download_uses_get(self) -> None:
        self.assertRegex(
            JS,
            r"apiDownload\(`/api/security/audit-reports/\$\{encodeURIComponent\(button\.dataset\.downloadReport\)\}/file`,\s*\{ method: ['\"]GET['\"] \}\)",
        )


if __name__ == "__main__":
    unittest.main()
