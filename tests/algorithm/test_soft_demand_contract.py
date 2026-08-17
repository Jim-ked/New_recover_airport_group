from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.algorithm.snapshot_adapter import build_algorithm_input
from tests.algorithm.test_snapshot_adapter import make_snapshot
from tests.algorithm.test_model_builder_overlay import FakeModel
from tests.algorithm.test_solution_dump_overlay import FakeSolvedModel
from original_algorithm_overlay.model import decision_vars as dv
from original_algorithm_overlay.model import model_builder as mb
from original_algorithm_overlay.utils import solution_dump as sd


class SoftDemandContractTests(unittest.TestCase):
    def _fixture(self):
        bundle = build_algorithm_input(make_snapshot())
        maps = dv.build_path_map(
            bundle.ds,
            bundle.run_params,
            {"enabled": True, "S": ["A1", "A2"]},
        )
        return bundle, maps

    def test_required_sorties_is_soft_penalty_not_hard_or_upper_bound(self):
        b, maps = self._fixture()
        model, pack = mb.build_model(
            b.ds,
            b.run_params,
            maps,
            integer_vars=True,
            runtime=b.runtime,
            model_factory=FakeModel,
        )
        self.assertIn(("M1", "fighter"), pack["unmet_demand"])
        unmet = pack["unmet_demand"][("M1", "fighter")]
        req = model.cons["REQ__M1__fighter"]
        self.assertEqual(">=", req.op)
        self.assertAlmostEqual(1.0, req.left.terms[unmet.name])
        self.assertAlmostEqual(2.0, req.right.const)
        self.assertFalse(any(name.startswith("REQ_MAX__") for name in model.cons))

        objective, sense = model.objective
        self.assertEqual("maximize", sense)
        self.assertLess(objective.terms[unmet.name], 0.0)
        self.assertEqual(
            -mb.DEFAULT_UNMET_DEMAND_PENALTY,
            objective.terms[unmet.name],
        )

    def test_solution_export_accepts_partial_baseline_demand(self):
        b, maps = self._fixture()
        path = next(
            p for p in maps.path_records
            if p.origin_airport_id == "A1"
            and p.return_airport_id == "A1"
            and p.depart_slot == 0
        )
        vars_by_path = {p.key: f"v{i}" for i, p in enumerate(maps.path_records)}
        # Snapshot baseline requires two fighter sorties.  One physically valid sortie is
        # now a valid solved schedule with one unit of unmet baseline demand.
        model = FakeSolvedModel({vars_by_path[path.key]: 1})
        solution = sd.build_solution(
            b.ds,
            maps,
            {"x_path": vars_by_path},
            model,
            run_id="R-SOFT",
            run_params=b.run_params,
            cluster_cfg={"enabled": True, "K": 2, "S": ["A1", "A2"]},
        )
        self.assertEqual(1, sum(row.sorties for row in solution.sortie_chains))


if __name__ == "__main__":
    unittest.main()
