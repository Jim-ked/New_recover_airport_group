from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.storage.run_queue_repository import RunQueueRepository
from tests.algorithm.test_snapshot_adapter import make_snapshot


class RunQueueRepositoryTests(unittest.TestCase):
    def test_next_queued_is_system_scoped_and_oldest_first(self):
        with tempfile.TemporaryDirectory() as td:
            repo = RunQueueRepository(pathlib.Path(td) / "runtime.sqlite3")
            repo.init_schema()
            r1 = make_snapshot(run_id="R1")
            r2 = make_snapshot(run_id="R2")
            repo.create_queued(snapshot=r1, owner_user_id="U1")
            repo.create_queued(snapshot=r2, owner_user_id="U2")
            # R1 is inserted first; run_id is also a deterministic timestamp tie-break.
            row = repo.next_queued()
            self.assertIsNotNone(row)
            self.assertEqual("R1", row.run_id)
            repo.request_cancel("R1")
            self.assertEqual("R2", repo.next_queued().run_id)


if __name__ == "__main__":
    unittest.main()
