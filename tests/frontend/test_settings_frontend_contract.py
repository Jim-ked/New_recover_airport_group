from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE_HTML = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
SETTINGS_HTML = (ROOT / "frontend/templates/pages/settings.html").read_text(encoding="utf-8")
SETTINGS_JS = (ROOT / "frontend/static/js/modules/settings.js").read_text(encoding="utf-8")
SHELL_JS = (ROOT / "frontend/static/js/modules/shell.js").read_text(encoding="utf-8")


class SettingsFrontendContractTests(unittest.TestCase):
    def test_settings_is_secondary_shell_entry_not_primary_business_nav(self):
        self.assertIn("sidebar-footer", BASE_HTML)
        self.assertIn("settings_page", BASE_HTML)
        self.assertEqual(1, BASE_HTML.count('class="sidebar-footer"'))

    def test_settings_has_three_stable_workspaces(self):
        for token in (
            "settingsTabs",
            'data-settings-tab="account"',
            'data-settings-tab="users"',
            'data-settings-tab="audit"',
            "accountWorkspace",
            "userWorkspace",
            "auditWorkspace",
        ):
            self.assertIn(token, SETTINGS_HTML)

    def test_admin_workspace_tabs_are_permission_driven(self):
        self.assertIn("users.admin", SETTINGS_JS)
        self.assertIn("audit.read", SETTINGS_JS)
        self.assertIn("settings-tab-admin", SETTINGS_HTML)
        self.assertIn("classList.toggle('hidden'", SETTINGS_JS)

    def test_account_permissions_are_business_summaries_not_raw_chips(self):
        for label in ("基础数据", "情境构建", "指标管理", "算法运行", "结果分析"):
            self.assertIn(label, SETTINGS_JS)
        self.assertIn("permissionSummary", SETTINGS_JS)
        self.assertNotIn("settings-permission", SETTINGS_HTML + SETTINGS_JS)
        self.assertNotIn("map(p=>`<span", SETTINGS_JS)

    def test_user_workspace_has_applied_search_filters_and_inspector(self):
        for token in (
            'name="settings-user-search"',
            'autocomplete="off"',
            "userSearchButton",
            "userRoleFilter",
            "userStatusFilter",
            "userInspector",
            "selectedUserId",
            "draftSearch",
            "appliedSearch",
        ):
            self.assertIn(token, SETTINGS_HTML + SETTINGS_JS)

    def test_audit_workspace_uses_existing_api_without_polling(self):
        for token in (
            "/api/audit-events",
            "auditKeyword",
            "auditActorFilter",
            "auditOutcomeFilter",
            "auditResourceFilter",
            "auditCreatedAfter",
            "auditCreatedBefore",
            "auditPrev",
            "auditNext",
            "auditInspector",
            "formatAuditAction",
        ):
            self.assertIn(token, SETTINGS_HTML + SETTINGS_JS)
        self.assertNotIn("setInterval", SETTINGS_JS)

    def test_role_labels_are_formal_and_old_labels_are_absent(self):
        combined = SETTINGS_HTML + SETTINGS_JS
        for label in ("游客", "操作员", "管理员"):
            self.assertIn(label, combined)
        for obsolete in ("查看用户", "操作用户", "运行操作员", "系统管理员"):
            self.assertNotIn(obsolete, combined)

    def test_settings_uses_existing_account_and_user_apis(self):
        self.assertIn("/api/me", SETTINGS_JS)
        self.assertIn("/api/users", SETTINGS_JS)
        self.assertNotIn("backup", SETTINGS_JS.lower())

    def test_shell_menu_links_to_settings_without_permission_modal(self):
        for token in ("accountSettingsAction", "changePasswordAction", "logoutAction"):
            self.assertIn(token, BASE_HTML + SHELL_JS)
        self.assertNotIn("accountInfoModal", BASE_HTML + SHELL_JS)
        self.assertNotIn("accountInfoAction", BASE_HTML + SHELL_JS)
        self.assertNotIn("account-permission", BASE_HTML + SHELL_JS)
        self.assertNotIn("textContent = permission", SHELL_JS)
        self.assertNotIn("dataset.permissions", SHELL_JS)

    def test_shell_uses_formal_role_labels_without_first_paint_role_code(self):
        combined = BASE_HTML + SHELL_JS
        for label in ("游客", "操作员", "管理员"):
            self.assertIn(label, combined)
        for obsolete in ("查看用户", "操作用户", "运行操作员", "系统管理员"):
            self.assertNotIn(obsolete, combined)
        self.assertNotIn("{{ session_user.get('role') or 'viewer' }}", BASE_HTML)


if __name__ == "__main__":
    unittest.main()
