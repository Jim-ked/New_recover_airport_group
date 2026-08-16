from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.storage.database import initialize_database
from backend.storage.user_repository import (
    AccountDisabledError,
    AuthenticationFailedError,
    UserRepository,
)


class UserRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = Path(self.td.name) / "app.sqlite"
        initialize_database(self.db)
        self.repo = UserRepository(self.db)
        self.repo.create_user(
            user_id="U1", login_name="operator1", password="password-123", role="operator",
            display_name="Operator One",
        )

    def tearDown(self):
        self.td.cleanup()

    def test_authenticate_and_auth_revision_revoke_old_sessions_on_password_or_role_change(self):
        user = self.repo.authenticate("operator1", "password-123")
        self.assertEqual(1, user["auth_revision"])
        with self.assertRaises(AuthenticationFailedError):
            self.repo.authenticate("operator1", "bad-password")
        changed = self.repo.change_password(
            "U1", current_password="password-123", new_password="password-456"
        )
        self.assertEqual(2, changed["auth_revision"])
        with self.assertRaises(AuthenticationFailedError):
            self.repo.authenticate("operator1", "password-123")
        self.assertEqual("U1", self.repo.authenticate("operator1", "password-456")["user_id"])
        role_changed = self.repo.set_role("U1", "viewer")
        self.assertEqual(3, role_changed["auth_revision"])
        self.assertEqual("viewer", role_changed["role"])

    def test_disabled_account_cannot_authenticate(self):
        disabled = self.repo.set_disabled("U1", True)
        self.assertTrue(disabled["is_disabled"])
        with self.assertRaises(AccountDisabledError):
            self.repo.authenticate("operator1", "password-123")

    def test_bootstrap_admin_has_no_built_in_password(self):
        empty_db = Path(self.td.name) / "empty.sqlite"
        initialize_database(empty_db)
        repo = UserRepository(empty_db)
        admin = repo.bootstrap_admin(login_name="admin", password="Admin-pass-123")
        self.assertIsNotNone(admin)
        self.assertEqual("admin", admin["role"])
        self.assertIsNone(repo.bootstrap_admin(login_name="admin2", password="Other-pass-123"))


if __name__ == "__main__":
    unittest.main()
