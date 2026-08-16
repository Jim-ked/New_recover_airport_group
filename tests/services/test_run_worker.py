from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.algorithm.runner import AlgorithmRunResult, run_once
from backend.services.run_result_service import RunResultService
from backend.services.run_service import RunService
from backend.services.run_worker import RunWorker
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from tests.algorithm.test_runner import InfeasibleFakeModel, RunnerFakeModel, fixed_cluster_selector
from tests.algorithm.test_snapshot_adapter import make_snapshot


class _UnusedSnapshotService:
    def build_snapshot(self, **_kwargs):
        raise AssertionError("worker must not rebuild snapshot")


class RunWorkerTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "app.sqlite"
        self.runs = RunRepository(self.db)
        self.runs.init_schema()
        self.snapshots = RunSnapshotRepository(self.db)
        self.run_service = RunService(
            snapshot_service=_UnusedSnapshotService(),
            run_repository=self.runs,
        )
        self.result_service = RunResultService(
            run_repository=self.runs,
            snapshot_repository=self.snapshots,
        )

    def tearDown(self):
        self._td.cleanup()

    def _queue(self, run_id="R1", *, cluster_enabled=True):
        snapshot = make_snapshot(run_id=run_id, cluster_enabled=cluster_enabled)
        self.runs.create_queued(snapshot=snapshot, owner_user_id="U1")
        return snapshot

    def _worker(self, *, algorithm_runner=run_once):
        return RunWorker(
            run_service=self.run_service,
            result_service=self.result_service,
            snapshot_repository=self.snapshots,
            algorithm_runner=algorithm_runner,
        )

    def test_successful_worker_closes_snapshot_to_canonical_result_lifecycle(self):
        self._queue()
        record = self._worker().execute(
            "R1",
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=RunnerFakeModel,
        )
        self.assertEqual("succeeded", record.status)
        solution, metrics = self.runs.get_result_payloads("R1")
        self.assertEqual("R1", solution["run_id"])
        self.assertEqual("metrics.v1", metrics["schema_version"])
        events = self.runs.list_events("R1")
        self.assertEqual("worker_started", events[0].event)
        self.assertEqual("run_succeeded", events[-1].event)
        self.assertEqual("persistence", events[-1].stage)
        self.assertEqual(list(range(1, len(events) + 1)), [e.seq for e in events])

    def test_public_events_preserve_interleaved_cluster_semantics_without_fake_quick_stage(self):
        self._queue()
        self._worker().execute(
            "R1",
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=RunnerFakeModel,
        )
        cluster_events = [
            e for e in self.runs.list_events("R1")
            if e.payload.get("internal_stage") == "cluster"
        ]
        self.assertEqual(1, len(cluster_events))
        event = cluster_events[0]
        self.assertEqual("candidate_generation", event.stage)
        self.assertEqual(
            "candidate_generation_and_quick_evaluation_interleaved",
            event.payload["activity_semantics"],
        )
        # There is no invented sequential quick-evaluation event when the algorithm
        # did not emit one as a distinct fact.
        self.assertNotIn("quick_evaluation", [e.stage for e in self.runs.list_events("R1")])

    def test_infeasible_algorithm_becomes_failed_and_never_persists_solution(self):
        self._queue()
        record = self._worker().execute(
            "R1",
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=InfeasibleFakeModel,
        )
        self.assertEqual("failed", record.status)
        self.assertEqual("INFEASIBLE", record.failure_code)
        self.assertIsNone(self.runs.get_result_payloads("R1"))
        self.assertEqual("run_failed", self.runs.list_events("R1")[-1].event)

    def test_cancel_request_during_blocking_algorithm_wins_over_success_publication(self):
        self._queue()

        def cancelling_runner(snapshot, *, event_cb=None, **kwargs) -> AlgorithmRunResult:
            # Simulate a request arriving while a blocking algorithm call owns control.
            self.runs.request_cancel(snapshot.run_id)
            return run_once(snapshot, event_cb=event_cb, **kwargs)

        record = self._worker(algorithm_runner=cancelling_runner).execute(
            "R1",
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=RunnerFakeModel,
        )
        self.assertEqual("cancelled", record.status)
        self.assertTrue(record.cancel_requested)
        self.assertIsNone(self.runs.get_result_payloads("R1"))
        self.assertEqual("run_cancelled", self.runs.list_events("R1")[-1].event)

    def test_noncluster_run_never_invents_cluster_evaluation_events(self):
        self._queue(cluster_enabled=False)
        record = self._worker().execute("R1", model_factory=RunnerFakeModel)
        self.assertEqual("succeeded", record.status)
        internal = [e.payload.get("internal_stage") for e in self.runs.list_events("R1")]
        self.assertNotIn("cluster", internal)
        self.assertNotIn("quick_evaluation", [e.stage for e in self.runs.list_events("R1")])


if __name__ == "__main__":
    unittest.main()
