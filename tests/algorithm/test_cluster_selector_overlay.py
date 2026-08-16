from __future__ import annotations

import pathlib
import random
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.algorithm.snapshot_adapter import build_algorithm_input
from tests.algorithm.test_snapshot_adapter import make_snapshot
from tests.algorithm.test_model_builder_overlay import FakeModel, as_expr
from original_algorithm_overlay.model import cluster_selector as cs
from original_algorithm_overlay.model import decision_vars as dv
from original_algorithm_overlay.model import model_facts as mf


class SolvingFakeModel(FakeModel):
    def __init__(self, name):
        super().__init__(name)
        self.values = {}

    def hideOutput(self, _value):
        return None

    def optimize(self):
        # Contract test only: give every path variable a deterministic fractional LP
        # value. Constraint solving is covered later in the real PySCIPOpt environment.
        for var, _lb, _vtype in self.vars:
            self.values[var.name] = 0.5 if var.name.startswith("X_PATH__") else 0.0

    def getVal(self, var):
        return float(self.values.get(var.name, 0.0))

    def getObjVal(self):
        expr, _sense = self.objective
        return float(expr.const + sum(coef * self.values.get(name, 0.0) for name, coef in expr.terms.items()))


class DriftFakeModel(SolvingFakeModel):
    def getObjVal(self):
        return super().getObjVal() + 1.0


class ClusterSelectorOverlayTests(unittest.TestCase):
    def _fixture(self):
        bundle = build_algorithm_input(make_snapshot())
        base = dv.build_base_path_map(bundle.ds, bundle.run_params)
        return bundle, base

    def test_lp_evaluation_uses_same_path_objective_facts_as_model_builder(self):
        b, base = self._fixture()
        cache = {}
        row = cs._eval_cluster_lp(
            base, b.ds, b.run_params, b.runtime, 2, ["A1", "A2"], cache,
            model_factory=SolvingFakeModel,
        )
        self.assertEqual("ok", row["status"])
        self.assertGreater(row["F1"], 0.0)
        self.assertGreaterEqual(row["F2"], 0.0)

        weights = mf.resolved_alpha(b.runtime)
        expected = weights.sortie * row["F1"] - weights.resource * row["F2"] + weights.time * row["F3"]
        self.assertAlmostEqual(expected, row["Z"])

    def test_objective_drift_between_lp_report_and_model_is_a_hard_error(self):
        b, base = self._fixture()
        with self.assertRaisesRegex(cs.ClusterEvalError, "objective drift"):
            cs._eval_cluster_lp(
                base, b.ds, b.run_params, b.runtime, 2, ["A1", "A2"], {},
                model_factory=DriftFakeModel,
            )

    def test_cluster_scale_counts_xpath_not_legacy_out_return_views(self):
        b, base = self._fixture()
        maps = dv.build_path_map_from_base(base, None)
        idx = dv.build_var_index(maps, b.ds)
        expected = len(idx["XPATH"]) + len(idx["ZIDX"])
        self.assertEqual(expected, cs._estimate_var_scale(base, b.ds))
        duplicated_old = len(idx["XOUT"]) + len(idx["XRET"]) + len(idx["ZIDX"])
        self.assertNotEqual(duplicated_old, expected)

    def test_selector_no_longer_has_a_second_resource_objective_implementation(self):
        self.assertFalse(hasattr(cs, "_precompute_c_res"))
        self.assertFalse(hasattr(cs, "_lookup_ontime"))
        self.assertFalse(hasattr(cs, "_lookup_tau"))

    def test_sa_neighbour_policy_keeps_cluster_size_and_membership_valid(self):
        rng = random.Random(7)
        airports = ["A1", "A2", "A3", "A4", "A5"]
        current = ["A1", "A2", "A3"]
        for maker in (cs._neighbour_swap, cs._neighbour_two_swap):
            candidate = maker(current, airports, rng)
            self.assertEqual(3, len(candidate))
            self.assertEqual(3, len(set(candidate)))
            self.assertTrue(set(candidate) <= set(airports))
        candidate = cs._neighbour_destroy_repair(current, airports, rng, remove_n=2)
        self.assertEqual(3, len(candidate))
        self.assertEqual(3, len(set(candidate)))

    def test_core_airport_seed_is_still_a_seed_bias_not_a_new_hard_constraint(self):
        b, _base = self._fixture()
        weights = cs._seed_core_weight_map(b.runtime, ["A1", "A2", "A3"])
        self.assertEqual(2.0, weights["A1"])
        # Neighbour generation remains free to replace a core airport; batch 3 does not
        # silently turn this preference into a new hard cluster-membership rule.
        rng = random.Random(3)
        candidate = cs._neighbour_swap(["A1", "A2"], ["A1", "A2", "A3"], rng)
        self.assertEqual(2, len(candidate))
        self.assertTrue(set(candidate) <= {"A1", "A2", "A3"})


if __name__ == "__main__":
    unittest.main()
