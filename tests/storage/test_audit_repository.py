import tempfile
import unittest
from pathlib import Path

from backend.storage.audit_repository import AuditRepository
from backend.storage.database import initialize_database


class AuditRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = Path(self.td.name) / "app.sqlite"
        initialize_database(self.db)
        self.repo = AuditRepository(self.db)

    def tearDown(self):
        self.td.cleanup()

    def test_append_and_query_are_append_only_facts(self):
        first = self.repo.append(
            actor_user_id="U1", actor_role="operator", action="POST /api/runs",
            resource_type="runs", request_method="POST", request_path="/api/runs",
            source_address="127.0.0.1", response_status=201, outcome="success",
            details={"endpoint": "runs_v1.submit_run"},
        )
        self.repo.append(
            actor_user_id="U2", actor_role="viewer", action="DELETE /api/airports/<airport_id>",
            resource_type="airports", resource_id="A1", request_method="DELETE",
            request_path="/api/airports/A1", response_status=403, outcome="denied",
        )
        rows, total = self.repo.query(actor_user_id="U1")
        self.assertEqual(1, total)
        self.assertEqual(first.audit_id, rows[0].audit_id)
        self.assertEqual("runs_v1.submit_run", rows[0].details["endpoint"])
        denied, denied_total = self.repo.query(outcome="denied", resource_type="airports")
        self.assertEqual(1, denied_total)
        self.assertEqual("A1", denied[0].resource_id)


if __name__ == "__main__":
    unittest.main()
