from __future__ import annotations

import builtins
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.algorithm.runner import AlgorithmInfeasibleError, run_once
from backend.algorithm.snapshot_adapter import build_algorithm_input
from tests.algorithm.test_snapshot_adapter import make_snapshot
from tests.algorithm.test_model_builder_overlay import FakeModel
from original_algorithm_overlay.model.model_facts import resolved_alpha


class RunnerFakeModel(FakeModel):
    def __init__(self, name):
        super().__init__(name)
        self.values = {}
        self._status = "optimal"
        self._nsols = 1

    def optimize(self):
        chosen = False
        for var, _lb, _vtype in self.vars:
            value = 0.0
            parts = var.name.split("__")
            if (
                not chosen
                and len(parts) >= 7
                and parts[0] == "X_PATH"
                and parts[1] == parts[3]
                and parts[2] == "M1"
                and parts[4] == "fighter"
            ):
                value = 2.0
                chosen = True
            self.values[var.name] = value
        if not chosen:
            raise AssertionError("expected a legal complete same-airport M1 fighter path")

    def getVal(self, var):
        return float(self.values.get(var.name, 0.0))

    def getStatus(self):
        return self._status

    def getNSols(self):
        return self._nsols

    def getBestSol(self):
        return object() if self._nsols else None

    def getObjVal(self):
        expr, _sense = self.objective
        return float(expr.const + sum(coef * self.values.get(name, 0.0) for name, coef in expr.terms.items()))


class InfeasibleFakeModel(RunnerFakeModel):
    def optimize(self):
        for var, _lb, _vtype in self.vars:
            self.values[var.name] = 0.0
        self._status = "infeasible"
        self._nsols = 0


class SolverFactsFakeModel(RunnerFakeModel):
    solver_status = "optimal"
    gap_value = 0.0041
    solve_time_value = 12.5

    def __init__(self, name):
        super().__init__(name)
        self._status = self.solver_status

    def getDualbound(self):
        # This is a maximization model: SCIP's raw dual bound is an upper bound and
        # must remain above the incumbent objective rather than being sign-flipped.
        return self.getObjVal() + 3.25

    def getGap(self):
        return self.gap_value

    def getSolvingTime(self):
        return self.solve_time_value

    def isInfinity(self, value):
        return abs(float(value)) >= 1e20


class GapLimitFakeModel(SolverFactsFakeModel):
    solver_status = "gaplimit"


class TimeLimitFakeModel(SolverFactsFakeModel):
    solver_status = "timelimit"
    gap_value = 0.125


class TimeLimitNoSolutionFakeModel(SolverFactsFakeModel):
    solver_status = "timelimit"

    def optimize(self):
        for var, _lb, _vtype in self.vars:
            self.values[var.name] = 0.0
        self._nsols = 0

    def getBestSol(self):
        return None

    def getDualbound(self):
        return 7.5

    def getGap(self):
        return 1e20



def fixed_cluster_selector(**_kwargs):
    return {
        "cluster_cfg": {"enabled": True, "K": 2, "S": ["A1", "A2"]},
        "leaderboard": [{"S": ["A1", "A2"], "F1": 1.0, "F2": 1.0, "F3": 1.0, "Z": 1.0, "status": "ok"}],
        "trajectory": [],
        "search_plan": {},
    }


class SnapshotOnlyRunnerTests(unittest.TestCase):
    def test_runner_accepts_only_snapshot_and_produces_canonical_solution(self):
        snapshot = make_snapshot()
        events = []
        result = run_once(
            snapshot,
            event_cb=events.append,
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=RunnerFakeModel,
        )
        self.assertEqual("R1", result.run_id)
        self.assertEqual("optimal", result.solver_status)
        self.assertEqual(["A1", "A2"], result.solution.to_dict()["selected_cluster"])
        self.assertEqual(1, len(result.solution.sortie_chains))
        self.assertEqual(2, result.solution.sortie_chains[0].sorties)
        self.assertEqual("complete", events[-1]["stage"])
        self.assertTrue(all("stage" in e and "progress" in e and "message" in e for e in events))

    def test_solver_facts_preserve_raw_maximization_bound_gap_and_seconds(self):
        result = run_once(
            make_snapshot(),
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=SolverFactsFakeModel,
        )
        self.assertGreater(result.best_bound, result.objective)
        self.assertAlmostEqual(result.objective + 3.25, result.best_bound)
        self.assertEqual(0.0041, result.gap)
        self.assertEqual(12.5, result.solve_time_s)

    def test_feasible_gaplimit_and_timelimit_keep_distinct_solver_statuses(self):
        for model_factory, status, gap in (
            (GapLimitFakeModel, "gaplimit", 0.0041),
            (TimeLimitFakeModel, "timelimit", 0.125),
        ):
            with self.subTest(status=status):
                result = run_once(
                    make_snapshot(),
                    cluster_selector_fn=fixed_cluster_selector,
                    model_factory=model_factory,
                )
                self.assertEqual(status, result.solver_status)
                self.assertEqual(gap, result.gap)
                self.assertIsNotNone(result.objective)

    def test_no_feasible_solution_keeps_status_bound_and_time_without_fake_gap(self):
        with self.assertRaises(AlgorithmInfeasibleError) as raised:
            run_once(
                make_snapshot(),
                cluster_selector_fn=fixed_cluster_selector,
                model_factory=TimeLimitNoSolutionFakeModel,
            )
        error = raised.exception
        self.assertEqual("timelimit", error.solver_status)
        self.assertEqual(7.5, error.best_bound)
        self.assertIsNone(error.gap)
        self.assertEqual(12.5, error.solve_time_s)

    def test_final_solution_components_reuse_model_coefficients_and_explain_objective(self):
        snapshot = make_snapshot()
        result = run_once(
            snapshot,
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=SolverFactsFakeModel,
        )
        runtime = build_algorithm_input(snapshot).runtime
        weights = resolved_alpha(runtime)
        reconstructed = (
            weights.sortie * result.f1
            - weights.resource * result.f2
            - weights.time * result.f3
            - result.unmet_demand_penalty * result.unmet_demand_total
        )
        self.assertAlmostEqual(result.objective, reconstructed)
        self.assertGreater(result.f1, 0.0)
        # This fixture has no selected damage scenario or permanent resource loss, yet
        # F2 is positive because it is weighted actual consumption/reference quantity.
        self.assertIsNone(snapshot.to_dict()["run_config"]["damage_scenario_id"])
        self.assertGreater(result.f2, 0.0)
        self.assertGreater(result.f3, 0.0)
        self.assertEqual(1.0, result.cluster_lp_objective)

    def test_noncluster_run_has_no_invented_cluster_lp_objective(self):
        result = run_once(
            make_snapshot(cluster_enabled=False),
            model_factory=SolverFactsFakeModel,
        )
        self.assertIsNone(result.cluster_lp_objective)

    def test_runner_uses_algorithm_seed_frozen_in_snapshot(self):
        snapshot = make_snapshot()
        captured = {}
        def selector(**kwargs):
            captured["seed"] = kwargs["random_seed"]
            return fixed_cluster_selector(**kwargs)
        run_once(snapshot, cluster_selector_fn=selector, model_factory=RunnerFakeModel)
        self.assertEqual(42, captured["seed"])

    def test_runner_does_not_read_scene_parameter_or_runtime_files(self):
        snapshot = make_snapshot()
        with mock.patch.object(builtins, "open", side_effect=AssertionError("file read/write forbidden in snapshot runner")):
            result = run_once(
                snapshot,
                cluster_selector_fn=fixed_cluster_selector,
                model_factory=RunnerFakeModel,
            )
        self.assertEqual("R1", result.run_id)

    def test_infeasible_solver_never_returns_solution(self):
        with self.assertRaisesRegex(AlgorithmInfeasibleError, "no feasible solution"):
            run_once(
                make_snapshot(),
                cluster_selector_fn=fixed_cluster_selector,
                model_factory=InfeasibleFakeModel,
            )

    def test_non_snapshot_input_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "RunSnapshot"):
            run_once({}, cluster_selector_fn=fixed_cluster_selector, model_factory=RunnerFakeModel)


if __name__ == "__main__":
    unittest.main()
