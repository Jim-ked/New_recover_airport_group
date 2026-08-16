import unittest

from backend.auth.principal import Principal
from backend.web.account_api import AccountApi


class AccountApiTests(unittest.TestCase):
    def test_current_returns_effective_role_and_permissions(self):
        response = AccountApi().current(principal=Principal("U1", role="operator"))
        self.assertEqual(200, response.status)
        self.assertEqual("U1", response.body["user_id"])
        self.assertEqual("operator", response.body["role"])
        self.assertFalse(response.body["is_admin"])
        self.assertIn("runs.execute", response.body["permissions"])
        self.assertNotIn("catalog.write", response.body["permissions"])

    def test_admin_returns_admin_permissions(self):
        response = AccountApi().current(principal=Principal("A", is_admin=True))
        self.assertTrue(response.body["is_admin"])
        self.assertIn("results.export", response.body["permissions"])
        self.assertIn("audit.read", response.body["permissions"])


if __name__ == "__main__":
    unittest.main()
