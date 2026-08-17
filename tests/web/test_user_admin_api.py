from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.auth.principal import Principal
from backend.storage.user_repository import UserRepository
from backend.web.user_admin_api import UserAdminApi


class UserAdminApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = UserRepository(pathlib.Path(self.tmp.name) / "users.sqlite3")
        self.repo.init_schema()
        self.repo.create_user(user_id="A1", login_name="admin", password="password1", role="admin")
        self.api = UserAdminApi(self.repo)
        self.admin = Principal(user_id="A1", role="admin")
        self.viewer = Principal(user_id="V1", role="viewer")
    def tearDown(self): self.tmp.cleanup()

    def test_admin_can_create_list_change_and_reset(self):
        created = self.api.create({"login_name":"operator1","display_name":"操作员一","role":"operator","password":"password2"}, principal=self.admin)
        self.assertEqual(201, created.status)
        uid = created.body["user"]["user_id"]
        self.assertTrue(uid.startswith("USR-"))
        self.assertNotIn("auth_revision", created.body["user"])
        self.assertEqual(2, len(self.api.list(principal=self.admin).body["users"]))
        self.assertEqual("viewer", self.api.set_role(uid,{"role":"viewer"},principal=self.admin).body["user"]["role"])
        self.assertTrue(self.api.set_disabled(uid,{"disabled":True},principal=self.admin).body["user"]["is_disabled"])
        self.assertEqual(200, self.api.reset_password(uid,{"new_password":"newpass99"},principal=self.admin).status)

    def test_viewer_is_denied(self):
        self.assertEqual(403, self.api.list(principal=self.viewer).status)

    def test_admin_cannot_lock_out_current_account_from_settings(self):
        self.assertEqual(400, self.api.set_disabled("A1",{"disabled":True},principal=self.admin).status)
        self.assertEqual(400, self.api.set_role("A1",{"role":"viewer"},principal=self.admin).status)
        self.assertEqual(400, self.api.reset_password("A1",{"new_password":"newpass99"},principal=self.admin).status)


if __name__ == "__main__": unittest.main()
