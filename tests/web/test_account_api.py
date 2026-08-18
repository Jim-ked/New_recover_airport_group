import tempfile
import unittest
from pathlib import Path

from backend.auth.principal import Principal
from backend.storage.user_repository import UserRepository
from backend.web.account_api import AccountApi
from backend.web.composition import build_account_api


class AccountApiTests(unittest.TestCase):
    def test_current_returns_effective_role_and_permissions(self):
        response = AccountApi().current(principal=Principal("U1", role="operator"))
        self.assertEqual(200, response.status)
        self.assertEqual("U1", response.body["user_id"])
        self.assertEqual("operator", response.body["role"])
        self.assertFalse(response.body["is_admin"])
        self.assertIn("runs.execute", response.body["permissions"])
        self.assertNotIn("catalog.write", response.body["permissions"])
        self.assertNotIn("login_name", response.body)
        self.assertNotIn("display_name", response.body)

    def test_admin_returns_admin_permissions(self):
        response = AccountApi().current(principal=Principal("A", is_admin=True))
        self.assertTrue(response.body["is_admin"])
        self.assertIn("results.export", response.body["permissions"])
        self.assertIn("audit.read", response.body["permissions"])

    def test_repository_projection_adds_account_profile_without_security_internals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = UserRepository(Path(temp_dir) / "account.sqlite3")
            repository.init_schema()
            repository.create_user(
                user_id="U-PROFILE",
                login_name="profile.user",
                display_name="资料用户",
                password="password1",
                role="operator",
            )
            repository.authenticate("profile.user", "password1")

            response = build_account_api(repository.db_path).current(
                principal=Principal("U-PROFILE", role="operator")
            )

            self.assertEqual(200, response.status)
            self.assertEqual("profile.user", response.body["login_name"])
            self.assertEqual("资料用户", response.body["display_name"])
            self.assertIsNotNone(response.body["created_at"])
            self.assertIsNotNone(response.body["last_login_at"])
            self.assertEqual(
                {
                    "user_id", "login_name", "display_name", "role", "is_admin",
                    "permissions", "created_at", "last_login_at",
                },
                set(response.body),
            )
            self.assertNotIn("password_hash", response.body)
            self.assertNotIn("auth_revision", response.body)


if __name__ == "__main__":
    unittest.main()
