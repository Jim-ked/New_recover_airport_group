from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.algorithm.runner import run_once
from backend.analysis.metrics import build_metrics_core
from backend.domain.damage import DamageScenario
from backend.services.run_result_service import (
    RunResultAccessError,
    RunResultNotReadyError,
    RunResultService,
    RunResultServiceError,
)
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from tests.algorithm.test_runner import (
    GapLimitFakeModel,
    RunnerFakeModel,
    SolverFactsFakeModel,
    TimeLimitFakeModel,
    fixed_cluster_selector,
)
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

    def _execute_and_persist(self, snapshot, *, owner="U1", model_factory=RunnerFakeModel):
        self.runs.create_queued(snapshot=snapshot, owner_user_id=owner)
        self.runs.claim_running(snapshot.run_id)
        kwargs = {"model_factory": model_factory}
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
        self.assertIsNone(metrics["technical"]["best_bound"])
        self.assertIsNone(metrics["technical"]["gap"])
        self.assertIsNone(metrics["technical"]["solve_time_s"])

    def test_solver_facts_and_objective_components_survive_persistence_and_query(self):
        snapshot = make_snapshot(run_id="R-FACTS")
        result = self._execute_and_persist(
            snapshot, model_factory=SolverFactsFakeModel
        )

        metrics = self.service.get_metrics("R-FACTS", actor_user_id="U1")
        technical = metrics["technical"]
        self.assertEqual(result.objective, technical["objective"])
        self.assertEqual(result.best_bound, technical["best_bound"])
        self.assertEqual(0.0041, technical["gap"])
        self.assertEqual(12.5, technical["solve_time_s"])
        self.assertEqual(1.0, technical["cluster_lp_objective"])
        self.assertEqual(result.f1, technical["f1"])
        self.assertEqual(result.f2, technical["f2"])
        self.assertEqual(result.f3, technical["f3"])
        self.assertEqual(result.unmet_demand_total, technical["unmet_demand_total"])
        self.assertEqual(result.unmet_demand_penalty, technical["unmet_demand_penalty"])

        single = self.service.get_single_run("R-FACTS", actor_user_id="U1")
        self.assertEqual(technical, single["metrics"]["technical"])

    def test_executor_metadata_cannot_override_algorithm_solver_facts(self):
        snapshot = make_snapshot(run_id="R-CONFLICT")
        self.runs.create_queued(snapshot=snapshot, owner_user_id="U1")
        self.runs.claim_running(snapshot.run_id)
        result = run_once(
            snapshot,
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=SolverFactsFakeModel,
        )

        with self.assertRaisesRegex(RunResultServiceError, "objective"):
            self.service.persist_success(
                result=result,
                technical={"objective": result.objective + 1.0},
            )

        self.assertEqual("running", self.runs.get(snapshot.run_id).status)
        self.assertIsNone(self.runs.get_result_payloads(snapshot.run_id))

    def test_feasible_limit_statuses_are_persisted_without_coercion(self):
        for index, (model_factory, status, gap) in enumerate((
            (GapLimitFakeModel, "gaplimit", 0.0041),
            (TimeLimitFakeModel, "timelimit", 0.125),
        )):
            run_id = f"R-LIMIT-{index}"
            self._execute_and_persist(
                make_snapshot(run_id=run_id), model_factory=model_factory
            )
            technical = self.service.get_metrics(
                run_id, actor_user_id="U1"
            )["technical"]
            self.assertEqual(status, technical["solver_status"])
            self.assertEqual(gap, technical["gap"])
            self.assertIsNotNone(technical["objective"])

    def test_old_metrics_without_new_technical_fields_remain_unchanged_on_read(self):
        snapshot = make_snapshot(run_id="R-OLD")
        self.runs.create_queued(snapshot=snapshot, owner_user_id="U1")
        self.runs.claim_running("R-OLD")
        result = run_once(
            snapshot,
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=RunnerFakeModel,
        )
        old_metrics = build_metrics_core(
            snapshot,
            result.solution,
            technical={"solver_status": result.solver_status, "objective": result.objective},
        )
        self.runs.save_success(
            "R-OLD", solution=result.solution.to_dict(), metrics=old_metrics
        )
        before_hash = self.runs.get("R-OLD").metrics_hash

        loaded = self.service.get_metrics("R-OLD", actor_user_id="U1")

        self.assertEqual(old_metrics, loaded)
        for key in (
            "best_bound", "gap", "solve_time_s", "cluster_lp_objective",
            "f1", "f2", "f3", "unmet_demand_total", "unmet_demand_penalty",
        ):
            self.assertNotIn(key, loaded["technical"])
        self.assertEqual(before_hash, self.runs.get("R-OLD").metrics_hash)

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

    def test_exploratory_summary_and_object_detail_are_service_derived(self):
        base = make_snapshot(
            run_id="EXP-BASE",
            cluster_enabled=False,
            mission_required_sorties=2,
        )
        pressure = make_snapshot(
            run_id="EXP-PRESSURE",
            cluster_enabled=False,
            mission_required_sorties=3,
        )
        self._execute_and_persist(base)
        self._execute_and_persist(pressure)

        summary = self.service.compare_exploratory(
            run_ids=("EXP-BASE", "EXP-PRESSURE"),
            baseline_run_id="EXP-BASE",
            actor_user_id="U1",
        )
        self.assertEqual("exploratory", summary["mode"])
        self.assertIn(
            "base mission inputs differ",
            summary["comparability"]["EXP-PRESSURE"]["differences"],
        )

        detail = self.service.compare_object(
            run_ids=("EXP-BASE", "EXP-PRESSURE"),
            comparison_type="exploratory",
            baseline_run_id="EXP-BASE",
            object_type="task",
            object_id="M1",
            actor_user_id="U1",
        )
        self.assertEqual("comparison-object.v1", detail["schema_version"])
        self.assertEqual(2, detail["baseline"]["absolute"]["required_total"])
        self.assertEqual(3, detail["runs"]["EXP-PRESSURE"]["absolute"]["required_total"])

    def test_candidate_query_exposes_cross_condition_runs_only_in_exploratory_mode(self):
        base = make_snapshot(
            run_id="CAND-BASE",
            cluster_enabled=False,
            mission_required_sorties=2,
        )
        pressure = make_snapshot(
            run_id="CAND-PRESSURE",
            cluster_enabled=False,
            mission_required_sorties=3,
        )
        self._execute_and_persist(base)
        self._execute_and_persist(pressure)

        strict = self.service.list_comparable_successful(
            "CAND-BASE", mode="multi_scenario", actor_user_id="U1"
        )
        exploratory = self.service.list_comparable_successful(
            "CAND-BASE", mode="exploratory", actor_user_id="U1"
        )

        self.assertEqual([], strict["items"])
        self.assertEqual(["CAND-PRESSURE"], [row["run_id"] for row in exploratory["items"]])
        self.assertIn(
            "base mission inputs differ",
            exploratory["items"][0]["comparability"]["differences"],
        )


if __name__ == "__main__":
    unittest.main()
