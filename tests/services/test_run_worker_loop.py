from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.run_worker_loop import RunWorkerLoop
from backend.services.run_worker import RunWorkerError


class FakeQueue:
    def __init__(self, rows): self.rows = list(rows)
    def next_queued(self): return self.rows.pop(0) if self.rows else None


class FakeWorker:
    def __init__(self, fail=False): self.calls = []; self.fail = fail
    def execute(self, run_id):
        self.calls.append(run_id)
        if self.fail: raise RunWorkerError("claim race")
        return SimpleNamespace(run_id=run_id, status="succeeded")


class RunWorkerLoopTests(unittest.TestCase):
    def test_execute_next_runs_one_discovered_job(self):
        queue = FakeQueue([SimpleNamespace(run_id="R1")])
        worker = FakeWorker()
        loop = RunWorkerLoop(queue_repository=queue, worker=worker, poll_interval_s=1.0)
        record = loop.execute_next()
        self.assertEqual("R1", record.run_id)
        self.assertEqual(["R1"], worker.calls)
        self.assertIsNone(loop.execute_next())

    def test_claim_race_does_not_crash_queue_process(self):
        queue = FakeQueue([SimpleNamespace(run_id="R1")])
        worker = FakeWorker(fail=True)
        loop = RunWorkerLoop(queue_repository=queue, worker=worker, poll_interval_s=1.0)
        self.assertIsNone(loop.execute_next())
        self.assertEqual(["R1"], worker.calls)

    def test_poll_interval_is_bounded(self):
        with self.assertRaises(ValueError):
            RunWorkerLoop(queue_repository=FakeQueue([]), worker=FakeWorker(), poll_interval_s=0.01)


if __name__ == "__main__":
    unittest.main()
