from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.algorithm.runner import run_once
from backend.domain.damage import DamageScenario
from backend.services.run_result_service import (
    RunResultAccessError,
    RunResultNotReadyError,
    RunResultService,
)
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from tests.algorithm.test_runner import RunnerFakeModel, fixed_cluster_selector
from tests.algorithm.test_snapshot_adapter import make_snapshot


class RunResultServiceTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "app.sqlite"
        self.runs = RunRepository(self.db)
        self.runs.init_schema()
        self.snapshots = RunSnapshotRepository(self.db)
        self.service = RunResultService(
            run_repository=self.runs,
            snapshot_repository=self.snapshots,
        )

    def tearDown(self):
        self._td.cleanup()

    def _execute_and_persist(self, snapshot, *, owner="U1"):
        self.runs.create_queued(snapshot=snapshot, owner_user_id=owner)
        self.runs.claim_running(snapshot.run_id)
        kwargs = {"model_factory": RunnerFakeModel}
        if snapshot.to_dict()["run_config"]["cluster_enabled"]:
            kwargs["cluster_selector_fn"] = fixed_cluster_selector
        result = run_once(snapshot, **kwargs)
        self.service.persist_success(result=result)
        return result

    def test_success_persistence_builds_metrics_from_frozen_snapshot(self):
        snapshot = make_snapshot(run_id="R1")
        result = self._execute_and_persist(snapshot)
        record = self.runs.get("R1")
        self.assertEqual("succeeded", record.status)
        metrics = self.service.get_metrics("R1", actor_user_id="U1")
        solution = self.service.get_solution("R1", actor_user_id="U1")
        self.assertEqual(snapshot.content_hash, metrics["technical"]["snapshot_hash"])
        self.assertEqual(result.solution.to_dict(), solution)
        self.assertEqual("metrics.v1", metrics["schema_version"])
        self.assertEqual("optimal", metrics["technical"]["solver_status"])

    def test_non_succeeded_run_has_no_canonical_result_surface(self):
        snapshot = make_snapshot(run_id="R1")
        self.runs.create_queued(snapshot=snapshot, owner_user_id="U1")
        with self.assertRaises(RunResultNotReadyError):
            self.service.get_metrics("R1", actor_user_id="U1")
        self.assertIsNone(self.runs.get_result_payloads("R1"))

    def test_result_access_is_owner_scoped_and_admin_is_explicit(self):
        self._execute_and_persist(make_snapshot(run_id="R1"), owner="U1")
        with self.assertRaises(RunResultAccessError):
            self.service.get_single_run("R1", actor_user_id="U2")
        bundle = self.service.get_single_run("R1", actor_user_id="ADMIN", is_admin=True)
        self.assertEqual("R1", bundle["run"]["run_id"])
        self.assertIn("situation_id", bundle["situation"])

    def test_run_detail_projects_damage_name_from_immutable_snapshot(self):
        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "冻结损毁场景",
            "category": "custom", "events": [],
        })
        snapshot = make_snapshot(run_id="R1", scenario=scenario)
        self.runs.create_queued(snapshot=snapshot, owner_user_id="U1")

        detail = self.service.get_run_detail("R1", actor_user_id="U1")

        self.assertEqual(
            {
                "damage_scenario_id": "DS1",
                "name": "冻结损毁场景",
                "category": "custom",
            },
            detail["damage_scenario"],
        )
        self.assertEqual("DS1", detail["run_config"]["damage_scenario_id"])

        no_damage = make_snapshot(run_id="R2", cluster_enabled=False)
        self.runs.create_queued(snapshot=no_damage, owner_user_id="U1")
        self.assertIsNone(
            self.service.get_run_detail("R2", actor_user_id="U1")["damage_scenario"]
        )

    def test_r0_r1_r2_comparison_is_service_derived_from_three_successful_runs(self):
        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "Damage", "category": "custom", "events": []
        })
        r0 = make_snapshot(
            run_id="R0", cluster_enabled=False, available_scenarios=(scenario,),
        )
        r1 = make_snapshot(
            run_id="R1", cluster_enabled=False, scenario=scenario,
        )
        r2 = make_snapshot(
            run_id="R2", cluster_enabled=True, scenario=scenario,
        )
        self._execute_and_persist(r0)
        self._execute_and_persist(r1)
        self._execute_and_persist(r2)
        comparison = self.service.compare_r0_r1_r2(
            r0_run_id="R0", r1_run_id="R1", r2_run_id="R2", actor_user_id="U1"
        )
        self.assertEqual({"R0": "R0", "R1": "R1", "R2": "R2"}, comparison["roles"])
        self.assertEqual("R1-R0", comparison["definitions"]["damage_delta"])
        self.assertEqual("R2-R1", comparison["definitions"]["cluster_delta"])
        self.assertEqual(2, len(comparison["airports"]))

        candidates = self.service.list_damage_comparison_candidates(actor_user_id="U1")
        self.assertEqual(
            [{"r0_run_id": "R0", "r1_run_id": "R1", "r2_run_id": "R2",
              "damage_scenario_id": "DS1", "preference_mode": "sortie_max"}],
            candidates["items"],
        )


    def test_multi_scenario_and_configuration_comparisons_are_service_derived(self):
        ds1 = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "D1", "category": "custom", "events": []
        })
        ds2 = DamageScenario.from_mapping({
            "damage_scenario_id": "DS2", "name": "D2", "category": "custom", "events": []
        })
        s1 = make_snapshot(
            run_id="S1", cluster_enabled=False, scenario=ds1, available_scenarios=(ds2,)
        )
        s2 = make_snapshot(
            run_id="S2", cluster_enabled=False, scenario=ds2, available_scenarios=(ds1,)
        )
        cfg = make_snapshot(
            run_id="CFG", cluster_enabled=True, scenario=ds1, available_scenarios=(ds2,)
        )
        for snapshot in (s1, s2, cfg):
            self._execute_and_persist(snapshot)

        multi = self.service.compare_multi_scenario(
            run_ids=("S1", "S2"), actor_user_id="U1"
        )
        self.assertEqual("multi_scenario", multi["mode"])
        self.assertEqual(["S1", "S2"], multi["run_ids"])

        configuration = self.service.compare_configuration(
            run_ids=("S1", "CFG"), baseline_run_id="S1", actor_user_id="U1"
        )
        self.assertEqual("configuration", configuration["mode"])
        self.assertEqual("S1", configuration["baseline_run_id"])
        self.assertEqual(0.0, configuration["summary_deltas_vs_baseline"]["S1"]["peak_sorties_delta"])


if __name__ == "__main__":
    unittest.main()
