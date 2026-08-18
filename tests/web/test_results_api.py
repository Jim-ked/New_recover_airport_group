from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.algorithm.runner import run_once
from backend.auth.principal import Principal
from backend.domain.damage import DamageScenario
from backend.services.run_result_service import RunResultService
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.web.results_api import ResultsApi
from tests.algorithm.test_runner import RunnerFakeModel, fixed_cluster_selector
from tests.algorithm.test_snapshot_adapter import make_snapshot


class ResultsApiTests(unittest.TestCase):
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
        self.api = ResultsApi(result_service=self.service)
        self.u1 = Principal("U1")
        self.u2 = Principal("U2")

    def tearDown(self):
        self._td.cleanup()

    def _persist(self, snapshot, *, owner="U1"):
        self.runs.create_queued(snapshot=snapshot, owner_user_id=owner)
        self.runs.claim_running(snapshot.run_id)
        kwargs = {"model_factory": RunnerFakeModel}
        if snapshot.to_dict()["run_config"]["cluster_enabled"]:
            kwargs["cluster_selector_fn"] = fixed_cluster_selector
        result = run_once(snapshot, **kwargs)
        self.service.persist_success(result=result)

    @staticmethod
    def _scenario(sid: str):
        return DamageScenario.from_mapping({
            "damage_scenario_id": sid, "name": sid, "category": "custom", "events": []
        })

    def test_damage_comparison_uses_backend_r0_r1_r2_facts(self):
        ds = self._scenario("DS1")
        self._persist(make_snapshot(run_id="R0", cluster_enabled=False, available_scenarios=(ds,)))
        self._persist(make_snapshot(run_id="R1", cluster_enabled=False, scenario=ds))
        self._persist(make_snapshot(run_id="R2", cluster_enabled=True, scenario=ds))

        response = self.api.damage_comparison(
            {"r0_run_id": "R0", "r1_run_id": "R1", "r2_run_id": "R2"},
            principal=self.u1,
        )
        self.assertEqual(200, response.status)
        self.assertEqual({"R0": "R0", "R1": "R1", "R2": "R2"}, response.body["roles"])
        self.assertEqual("R1-R0", response.body["definitions"]["damage_delta"])
        self.assertEqual("R2-R1", response.body["definitions"]["cluster_delta"])
        self.assertEqual({"R0", "R1", "R2"}, set(response.body["run_summaries"]))
        self.assertIn("mission_count", response.body["run_summaries"]["R0"])
        self.assertIn("returned_sorties_total", response.body["run_summaries"]["R0"])
        self.assertIn("required_total", response.body["tasks"]["M1"])
        self.assertIn("scheduled_total", response.body["tasks"]["M1"])
        self.assertEqual("Mission", response.body["labels"]["missions"]["M1"])

        candidates = self.api.damage_candidates(principal=self.u1)
        self.assertEqual(200, candidates.status)
        self.assertEqual(
            [{"r0_run_id": "R0", "r1_run_id": "R1", "r2_run_id": "R2",
              "damage_scenario_id": "DS1", "preference_mode": "sortie_max"}],
            candidates.body["items"],
        )

    def test_noncomparable_roles_return_422_not_best_effort_comparison(self):
        ds = self._scenario("DS1")
        self._persist(make_snapshot(run_id="R0", cluster_enabled=False, available_scenarios=(ds,)))
        self._persist(make_snapshot(run_id="R1", cluster_enabled=False, scenario=ds))
        self._persist(make_snapshot(run_id="R2", cluster_enabled=False, scenario=ds))
        response = self.api.damage_comparison(
            {"r0_run_id": "R0", "r1_run_id": "R1", "r2_run_id": "R2"},
            principal=self.u1,
        )
        self.assertEqual(422, response.status)
        self.assertEqual("RUNS_NOT_COMPARABLE", response.body["error"]["code"])

    def test_comparable_run_filter_is_success_owner_schema_and_mode_scoped(self):
        ds1 = self._scenario("DS1")
        ds2 = self._scenario("DS2")
        base = make_snapshot(
            run_id="BASE", cluster_enabled=False, scenario=ds1, available_scenarios=(ds2,)
        )
        other_scene = make_snapshot(
            run_id="SCENE", cluster_enabled=False, scenario=ds2, available_scenarios=(ds1,)
        )
        different_config = make_snapshot(
            run_id="CONFIG", cluster_enabled=True, scenario=ds1, available_scenarios=(ds2,)
        )
        self._persist(base)
        self._persist(other_scene)
        self._persist(different_config)
        # Same problem, but different owner must never enter the owner's comparison choices.
        foreign = make_snapshot(
            run_id="FOREIGN", cluster_enabled=False, scenario=ds2, available_scenarios=(ds1,)
        )
        self._persist(foreign, owner="U2")

        multi = self.api.comparable_runs(
            principal=self.u1, base_run_id="BASE", mode="multi_scenario"
        )
        self.assertEqual(200, multi.status)
        self.assertEqual(["SCENE"], [row["run_id"] for row in multi.body["items"]])

        config = self.api.comparable_runs(
            principal=self.u1, base_run_id="BASE", mode="configuration"
        )
        self.assertEqual(200, config.status)
        self.assertEqual(["CONFIG"], [row["run_id"] for row in config.body["items"]])

    def test_results_access_is_owner_scoped(self):
        ds = self._scenario("DS1")
        self._persist(make_snapshot(run_id="BASE", cluster_enabled=False, scenario=ds), owner="U1")
        response = self.api.comparable_runs(
            principal=self.u2, base_run_id="BASE", mode="configuration"
        )
        self.assertEqual(403, response.status)


    def test_multi_scenario_and_configuration_comparison_endpoints_use_real_run_ids(self):
        ds1 = self._scenario("DS1")
        ds2 = self._scenario("DS2")
        scene1 = make_snapshot(
            run_id="SCENE1", cluster_enabled=False, scenario=ds1, available_scenarios=(ds2,)
        )
        scene2 = make_snapshot(
            run_id="SCENE2", cluster_enabled=False, scenario=ds2, available_scenarios=(ds1,)
        )
        config = make_snapshot(
            run_id="CONFIG", cluster_enabled=True, scenario=ds1, available_scenarios=(ds2,)
        )
        self._persist(scene1)
        self._persist(scene2)
        self._persist(config)

        multi = self.api.scenario_comparison(
            {"run_ids": ["SCENE1", "SCENE2"]}, principal=self.u1
        )
        self.assertEqual(200, multi.status)
        self.assertEqual("multi_scenario", multi.body["mode"])
        self.assertEqual(["SCENE1", "SCENE2"], multi.body["run_ids"])
        self.assertEqual({"SCENE1", "SCENE2"}, set(multi.body["run_summaries"]))
        self.assertIn("required_total", multi.body["tasks"]["M1"]["SCENE1"])
        self.assertIn("scheduled_total", multi.body["tasks"]["M1"]["SCENE1"])
        self.assertNotIn("best", str(multi.body).lower())

        cfg = self.api.configuration_comparison(
            {"run_ids": ["SCENE1", "CONFIG"], "baseline_run_id": "SCENE1"},
            principal=self.u1,
        )
        self.assertEqual(200, cfg.status)
        self.assertEqual("configuration", cfg.body["mode"])
        self.assertEqual("SCENE1", cfg.body["baseline_run_id"])
        self.assertEqual({"SCENE1", "CONFIG"}, set(cfg.body["run_summaries"]))
        self.assertIn("required_total", cfg.body["tasks"]["M1"]["SCENE1"])
        self.assertIn("scheduled_total", cfg.body["tasks"]["M1"]["SCENE1"])
        self.assertEqual(
            0.0,
            cfg.body["summary_deltas_vs_baseline"]["SCENE1"]["participating_airport_count_delta"],
        )

    def test_comparison_request_cardinality_and_baseline_are_strict(self):
        one = self.api.scenario_comparison({"run_ids": ["ONE"]}, principal=self.u1)
        self.assertEqual(400, one.status)
        self.assertEqual("run_ids", one.body["error"]["field"])

        duplicate = self.api.scenario_comparison(
            {"run_ids": ["ONE", "ONE"]}, principal=self.u1
        )
        self.assertEqual(400, duplicate.status)
        self.assertEqual("run_ids", duplicate.body["error"]["field"])

        bad_base = self.api.configuration_comparison(
            {"run_ids": ["R1", "R2"], "baseline_run_id": "R0"}, principal=self.u1
        )
        self.assertEqual(400, bad_base.status)
        self.assertEqual("baseline_run_id", bad_base.body["error"]["field"])

    def test_export_data_is_admin_gated_and_reuses_canonical_result_facts(self):
        ds = self._scenario("DS1")
        self._persist(make_snapshot(run_id="EXPORT", cluster_enabled=False, scenario=ds))
        denied = self.api.export_data(
            {"kind": "single_run", "run_id": "EXPORT"}, principal=self.u1
        )
        self.assertEqual(403, denied.status)

        admin = Principal("ADMIN", is_admin=True)
        response = self.api.export_data(
            {"kind": "single_run", "run_id": "EXPORT"}, principal=admin
        )
        self.assertEqual(200, response.status)
        self.assertEqual("report-data.v1", response.body["schema_version"])
        self.assertEqual("single_run", response.body["kind"])
        self.assertEqual(["EXPORT"], response.body["source_run_ids"])
        self.assertEqual("EXPORT", response.body["data"]["run"]["run_id"])
        self.assertEqual("source_ready", response.body["rendering"]["status"])
        self.assertEqual(["pdf", "csv"], response.body["rendering"]["supported_formats"])

    def test_viewer_can_read_results_but_principal_without_results_permission_cannot(self):
        ds = self._scenario("DS1")
        self._persist(make_snapshot(run_id="BASE", cluster_enabled=False, scenario=ds))
        viewer = Principal("U1", role="viewer")
        ok = self.api.comparable_runs(
            principal=viewer, base_run_id="BASE", mode="configuration"
        )
        self.assertEqual(200, ok.status)

        denied = Principal("U1", permissions=frozenset({"runs.read"}))
        response = self.api.comparable_runs(
            principal=denied, base_run_id="BASE", mode="configuration"
        )
        self.assertEqual(403, response.status)
        self.assertEqual("PERMISSION_DENIED", response.body["error"]["code"])


if __name__ == "__main__":
    unittest.main()
