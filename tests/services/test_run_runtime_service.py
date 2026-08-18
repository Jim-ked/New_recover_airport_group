from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from backend.algorithm.runner import run_once
from backend.domain.damage import DamageScenario
from backend.services.run_result_service import RunResultAccessError, RunResultNotReadyError, RunResultService
from backend.services.run_runtime_service import RunRuntimeService
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from tests.algorithm.test_runner import RunnerFakeModel, fixed_cluster_selector
from tests.algorithm.test_snapshot_adapter import make_snapshot


class RunRuntimeServiceTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "app.sqlite"
        self.runs = RunRepository(self.db)
        self.runs.init_schema()
        self.snapshots = RunSnapshotRepository(self.db)
        self.results = RunResultService(run_repository=self.runs, snapshot_repository=self.snapshots)
        self.runtime = RunRuntimeService(result_service=self.results)

    def tearDown(self):
        self._td.cleanup()

    def _persist(self, snapshot, owner="U1"):
        self.runs.create_queued(snapshot=snapshot, owner_user_id=owner)
        self.runs.claim_running(snapshot.run_id)
        kwargs = {"model_factory": RunnerFakeModel}
        if snapshot.to_dict()["run_config"]["cluster_enabled"]:
            kwargs["cluster_selector_fn"] = fixed_cluster_selector
        result = run_once(snapshot, **kwargs)
        self.results.persist_success(result=result)
        return result

    def test_runtime_is_server_derived_from_snapshot_solution_metrics(self):
        snapshot = make_snapshot(run_id="R1")
        result = self._persist(snapshot)
        runtime = self.runtime.get_runtime("R1", actor_user_id="U1")
        self.assertEqual("runtime.v1", runtime["schema_version"])
        self.assertEqual("R1", runtime["run_id"])
        self.assertEqual(result.solution.to_dict()["sortie_chains"][0]["path_id"], runtime["routes"][0]["path_id"])
        self.assertEqual(runtime["time_axis"]["windows"], [x["window"] for x in runtime["frames"]])
        self.assertEqual({"A1", "A2"}, {x["airport_id"] for x in runtime["airports"]})
        self.assertEqual(len(snapshot.to_dict()["situation"]["airports"]), len(runtime["airports"]))
        self.assertEqual({"M1"}, {x["mission_id"] for x in runtime["missions"]})
        route_ids = [x["path_id"] for x in runtime["routes"]]
        self.assertEqual(len(route_ids), len(set(route_ids)))
        self.assertEqual(
            {x["path_id"] for x in result.solution.to_dict()["sortie_chains"]},
            set(route_ids),
        )

    def test_runtime_window_totals_match_canonical_metrics_timeline(self):
        snapshot = make_snapshot(run_id="R1")
        self._persist(snapshot)
        runtime = self.runtime.get_runtime("R1", actor_user_id="U1")
        metrics = self.results.get_metrics("R1", actor_user_id="U1")
        self.assertEqual(
            metrics["timeline"]["departures_total"],
            [frame["departures_total"] for frame in runtime["frames"]],
        )
        self.assertEqual(
            metrics["timeline"]["returns_total"],
            [frame["returns_total"] for frame in runtime["frames"]],
        )

    def test_new_project_ids_flow_through_snapshot_solution_metrics_and_runtime(self):
        snapshot = make_snapshot(
            run_id="RUN-project-ids",
            situation_id="ST001",
            airport_ids=("AP001", "AP002"),
            cluster_enabled=False,
        )
        result = self._persist(snapshot)
        runtime = self.runtime.get_runtime("RUN-project-ids", actor_user_id="U1")
        metrics = self.results.get_metrics("RUN-project-ids", actor_user_id="U1")
        payload = json.dumps(
            {"snapshot": snapshot.to_dict(), "solution": result.solution.to_dict(), "metrics": metrics, "runtime": runtime},
            ensure_ascii=False,
        )

        self.assertNotIn("oa:", payload)
        self.assertIn("ST001", payload)
        self.assertIn("AP001", payload)

    def test_runtime_frames_expose_depart_return_events_without_interpolating_flight_position(self):
        snapshot = make_snapshot(run_id="R1")
        result = self._persist(snapshot)
        chain = result.solution.sortie_chains[0]
        runtime = self.runtime.get_runtime("R1", actor_user_id="U1")
        by_window = {x["window"]: x for x in runtime["frames"]}
        self.assertIn(chain.path_id, [x["path_id"] for x in by_window[chain.depart_window]["departures"]])
        self.assertIn(chain.path_id, [x["path_id"] for x in by_window[chain.return_window]["returns"]])
        self.assertEqual(chain.sorties, by_window[chain.depart_window]["departures_total"])

    def test_damage_phase_and_projection_are_backend_facts(self):
        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "Damage", "category": "custom",
            "events": [{
                "event_id": "E1", "sequence": 0,
                "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                "damage_type": "capacity_damage",
                "start_slot": 2, "end_slot": 4,
                "effect": {"closed": False, "remaining_capacity_per_window": 2},
                "recovery_mode": "average", "recovery_duration_slots": 2,
            }],
        })
        self._persist(make_snapshot(run_id="R1", scenario=scenario))
        runtime = self.runtime.get_runtime("R1", actor_user_id="U1")
        frames = {x["window"]: x for x in runtime["frames"]}
        self.assertEqual("active", frames[2]["damage_events"][0]["phase"])
        self.assertEqual("recovering", frames[4]["damage_events"][0]["phase"])
        self.assertEqual(2, frames[2]["airports"]["A1"]["capacity_available"])

    def test_runtime_is_success_only_and_owner_scoped(self):
        snapshot = make_snapshot(run_id="R1")
        self.runs.create_queued(snapshot=snapshot, owner_user_id="U1")
        with self.assertRaises(RunResultNotReadyError):
            self.runtime.get_runtime("R1", actor_user_id="U1")
        self.runs.claim_running("R1")
        result = run_once(snapshot, model_factory=RunnerFakeModel, cluster_selector_fn=fixed_cluster_selector)
        self.results.persist_success(result=result)
        with self.assertRaises(RunResultAccessError):
            self.runtime.get_runtime("R1", actor_user_id="U2")


if __name__ == "__main__":
    unittest.main()
