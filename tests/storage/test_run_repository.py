from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.storage.run_repository import RunConflictError, RunRepository, RunTransitionError
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from tests.algorithm.test_snapshot_adapter import make_snapshot


class RunRepositoryTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "app.sqlite"
        self.repo = RunRepository(self.db)
        self.repo.init_schema()
        self.snapshots = RunSnapshotRepository(self.db)

    def tearDown(self):
        self._td.cleanup()

    def test_create_queued_atomically_persists_snapshot_and_owner_record(self):
        snapshot = make_snapshot(run_id="R1")
        record = self.repo.create_queued(snapshot=snapshot, owner_user_id="U1")
        self.assertEqual("queued", record.status)
        self.assertEqual(snapshot.content_hash, record.snapshot_hash)
        loaded = self.snapshots.get("R1")
        self.assertIsNotNone(loaded)
        self.assertEqual(snapshot.payload_json, loaded.payload_json)

    def test_duplicate_run_is_rejected_without_overwrite(self):
        first = make_snapshot(run_id="R1")
        self.repo.create_queued(snapshot=first, owner_user_id="U1")
        with self.assertRaises(RunConflictError):
            self.repo.create_queued(snapshot=make_snapshot(run_id="R1"), owner_user_id="U2")
        record = self.repo.get("R1")
        self.assertEqual("U1", record.owner_user_id)

    def test_cancel_has_no_external_cancelling_status(self):
        self.repo.create_queued(snapshot=make_snapshot(run_id="R1"), owner_user_id="U1")
        queued_cancel = self.repo.request_cancel("R1")
        self.assertEqual("cancelled", queued_cancel.status)
        self.assertTrue(queued_cancel.cancel_requested)

        self.repo.create_queued(snapshot=make_snapshot(run_id="R2"), owner_user_id="U1")
        self.repo.claim_running("R2")
        running_cancel = self.repo.request_cancel("R2")
        self.assertEqual("running", running_cancel.status)
        self.assertTrue(running_cancel.cancel_requested)
        with self.assertRaisesRegex(RunTransitionError, "cancel-requested"):
            self.repo.save_success("R2", solution={"run_id": "R2"}, metrics={"run_id": "R2"})
        self.assertEqual("cancelled", self.repo.mark_cancelled("R2").status)

    def test_events_are_monotonic_and_incrementally_readable(self):
        self.repo.create_queued(snapshot=make_snapshot(run_id="R1"), owner_user_id="U1")
        e1 = self.repo.append_event(
            "R1", level="INFO", stage="data_preparation", event="stage_start",
            message="prepare", payload={"x": 1},
        )
        e2 = self.repo.append_event(
            "R1", level="INFO", stage="data_preparation", event="stage_end",
            message="prepared", payload={},
        )
        self.assertEqual((1, 2), (e1.seq, e2.seq))
        rows = self.repo.list_events("R1", after_seq=1)
        self.assertEqual([2], [x.seq for x in rows])
        self.assertEqual("prepared", rows[0].message)

    def test_success_result_is_insert_only_and_terminal(self):
        self.repo.create_queued(snapshot=make_snapshot(run_id="R1"), owner_user_id="U1")
        self.repo.claim_running("R1")
        record = self.repo.save_success(
            "R1",
            solution={"run_id": "R1", "selected_cluster": [], "sortie_chains": [{"path_id": "P"}]},
            metrics={"schema_version": "metrics.v1", "run_id": "R1"},
        )
        self.assertEqual("succeeded", record.status)
        self.assertIsNotNone(record.solution_hash)
        self.assertIsNotNone(record.metrics_hash)
        payloads = self.repo.get_result_payloads("R1")
        self.assertEqual("R1", payloads[0]["run_id"])
        with self.assertRaises(RunTransitionError):
            self.repo.save_success("R1", solution={"run_id": "R1"}, metrics={"run_id": "R1"})
        with self.assertRaises(RunTransitionError):
            self.repo.mark_failed("R1", message="too late")


if __name__ == "__main__":
    unittest.main()
