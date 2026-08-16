import tempfile
import unittest
from pathlib import Path

from backend.auth.principal import Principal
from backend.storage.audit_repository import AuditRepository
from backend.storage.database import initialize_database
from backend.web.audit_api import AuditApi
from backend.web.flask_audit import derive_resource_target


class AuditApiTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = Path(self.td.name) / "app.sqlite"
        initialize_database(self.db)
        self.repo = AuditRepository(self.db)
        self.api = AuditApi(repository=self.repo)
        self.repo.append(
            actor_user_id="U1", actor_role="operator", action="POST /api/runs",
            resource_type="runs", request_method="POST", request_path="/api/runs",
            response_status=201, outcome="success",
        )

    def tearDown(self):
        self.td.cleanup()

    def test_only_admin_can_query_audit_events(self):
        denied = self.api.list(principal=Principal("U1", role="operator"))
        self.assertEqual(403, denied.status)
        ok = self.api.list(principal=Principal("A", is_admin=True), actor_user_id="U1")
        self.assertEqual(200, ok.status)
        self.assertEqual(1, ok.body["total"])
        self.assertEqual("POST /api/runs", ok.body["items"][0]["action"])

    def test_resource_target_does_not_treat_collection_action_as_object_id(self):
        self.assertEqual(("runs", "RUN-1"), derive_resource_target("/api/runs/RUN-1/retry"))
        self.assertEqual(("missions", None), derive_resource_target("/api/missions/history"))
        self.assertEqual(("results", None), derive_resource_target("/api/results/damage-comparison"))


if __name__ == "__main__":
    unittest.main()
