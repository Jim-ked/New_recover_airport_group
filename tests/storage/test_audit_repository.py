import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_append_reads_record_with_the_same_connection(self):
        original_connect = self.repo.connect
        connection_count = 0

        def counted_connect():
            nonlocal connection_count
            connection_count += 1
            return original_connect()

        self.repo.connect = counted_connect
        record = self.repo.append(
            action="GET /api/me", request_method="GET", request_path="/api/me",
            response_status=200, outcome="success", details={"endpoint": "account_v1.me"},
        )
        self.assertEqual(1, connection_count)
        self.assertEqual("account_v1.me", record.details["endpoint"])

    def test_append_rolls_back_when_record_materialization_fails(self):
        original_row = self.repo._row
        self.repo._row = lambda _row: (_ for _ in ()).throw(RuntimeError("decode failed"))
        with self.assertRaisesRegex(RuntimeError, "decode failed"):
            self.repo.append(
                action="POST /api/runs", request_method="POST", request_path="/api/runs",
                response_status=201, outcome="success",
            )
        self.repo._row = original_row
        rows, total = self.repo.query()
        self.assertEqual(0, total)
        self.assertEqual([], rows)

    def test_concurrent_appends_return_unique_committed_records(self):
        def append(index):
            return self.repo.append(
                action=f"GET /api/items/{index}", request_method="GET",
                request_path=f"/api/items/{index}", response_status=200, outcome="success",
            )

        with ThreadPoolExecutor(max_workers=20) as pool:
            records = list(pool.map(append, range(20)))
        self.assertEqual(20, len({record.audit_id for record in records}))
        _, total = self.repo.query(limit=100)
        self.assertEqual(20, total)


if __name__ == "__main__":
    unittest.main()
