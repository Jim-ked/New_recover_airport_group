from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.services.run_service import RunAccessError, RunService
from backend.storage.run_repository import RunRepository
from tests.algorithm.test_snapshot_adapter import make_snapshot


class _FakeSnapshotService:
    def build_snapshot(self, *, run_id, situation_id, run_config, od_distances):
        if situation_id != "S1":
            raise AssertionError("unexpected situation")
        return make_snapshot(run_id=run_id)


class RunServiceTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = RunRepository(Path(self._td.name) / "app.sqlite")
        self.repo.init_schema()
        self.service = RunService(snapshot_service=_FakeSnapshotService(), run_repository=self.repo)

    def tearDown(self):
        self._td.cleanup()

    def test_submit_uses_business_inputs_and_creates_owner_scoped_queued_run(self):
        record = self.service.submit(
            run_id="R1", owner_user_id="U1", situation_id="S1",
            run_config={"ignored": "by fake"}, od_distances=[],
        )
        self.assertEqual("queued", record.status)
        self.assertEqual(["R1"], [x.run_id for x in self.service.list(actor_user_id="U1")])
        self.assertEqual([], self.service.list(actor_user_id="U2"))
        with self.assertRaises(RunAccessError):
            self.service.get("R1", actor_user_id="U2")
        self.assertEqual("R1", self.service.get("R1", actor_user_id="ADMIN", is_admin=True).run_id)

    def test_cancel_respects_owner_and_keeps_five_status_contract(self):
        self.service.submit(
            run_id="R1", owner_user_id="U1", situation_id="S1",
            run_config={}, od_distances=[],
        )
        with self.assertRaises(RunAccessError):
            self.service.request_cancel("R1", actor_user_id="U2")
        record = self.service.request_cancel("R1", actor_user_id="U1")
        self.assertEqual("cancelled", record.status)


if __name__ == "__main__":
    unittest.main()
