from __future__ import annotations
import pathlib, unittest
ROOT = pathlib.Path(__file__).resolve().parents[2]
class SettingsFrontendContractTests(unittest.TestCase):
    def test_settings_is_secondary_shell_entry_not_primary_business_nav(self):
        base=(ROOT/'frontend/templates/base.html').read_text(encoding='utf-8')
        self.assertIn('sidebar-footer',base);self.assertIn('settings_page',base)
        self.assertEqual(1, base.count('class="sidebar-footer"'))
    def test_settings_page_has_account_and_admin_user_workspaces(self):
        text=(ROOT/'frontend/templates/pages/settings.html').read_text(encoding='utf-8')
        for token in ('settingsAccountBody','userAdminSection','createUserModal','resetPasswordModal'):
            self.assertIn(token,text)
    def test_settings_js_uses_real_permission_and_user_apis(self):
        text=(ROOT/'frontend/static/js/modules/settings.js').read_text(encoding='utf-8')
        self.assertIn("/api/me",text);self.assertIn("/api/users",text);self.assertIn("users.admin",text)
        self.assertNotIn('backup',text.lower())
if __name__=='__main__': unittest.main()
