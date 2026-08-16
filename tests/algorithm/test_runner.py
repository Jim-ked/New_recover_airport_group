from __future__ import annotations

import builtins
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.algorithm.runner import AlgorithmInfeasibleError, run_once
from tests.algorithm.test_snapshot_adapter import make_snapshot
from tests.algorithm.test_model_builder_overlay import FakeModel


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
            if (not chosen) and var.name.startswith("X_PATH__A1__M1__A1__fighter__0__"):
                value = 2.0
                chosen = True
            self.values[var.name] = value
        if not chosen:
            raise AssertionError("expected complete A1->M1->A1 path at depart slot 0")

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
