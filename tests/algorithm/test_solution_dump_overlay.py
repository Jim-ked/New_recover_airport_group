from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.algorithm.snapshot_adapter import build_algorithm_input
from tests.algorithm.test_snapshot_adapter import make_snapshot
from original_algorithm_overlay.model import decision_vars as dv
from original_algorithm_overlay.utils import solution_dump as sd


class FakeSolvedModel:
    def __init__(self, values, *, status="optimal", n_solutions=1):
        self.values = dict(values)
        self.status = status
        self.n_solutions = n_solutions
    def getStatus(self): return self.status
    def getNSols(self): return self.n_solutions
    def getBestSol(self): return object() if self.n_solutions else None
    def getVal(self, var): return float(self.values.get(var, 0.0))


class SolutionDumpOverlayTests(unittest.TestCase):
    def _fixture(self):
        b = build_algorithm_input(make_snapshot())
        maps = dv.build_path_map(b.ds, b.run_params, {"enabled": True, "S": ["A1", "A2"]})
        p = next(x for x in maps.path_records if x.origin_airport_id == "A1" and x.return_airport_id == "A1" and x.depart_slot == 0)
        vars_by_path = {x.key: f"v{i}" for i, x in enumerate(maps.path_records)}
        model = FakeSolvedModel({vars_by_path[p.key]: 2})
        return b, maps, p, {"x_path": vars_by_path}, model

    def test_build_solution_exports_one_complete_chain_with_absolute_windows(self):
        b, maps, p, pack, model = self._fixture()
        sol = sd.build_solution(
            b.ds, maps, pack, model, run_id="R1", run_params=b.run_params,
            cluster_cfg={"enabled": True, "K": 2, "S": ["A1", "A2"]},
        )
        self.assertEqual(1, len(sol.sortie_chains))
        row = sol.sortie_chains[0]
        offset = b.ds["range"][0]
        self.assertEqual(offset + p.depart_slot, row.depart_window)
        self.assertEqual(offset + p.landing_slot, row.return_window)
        self.assertEqual(offset + p.ready_slot, row.ready_window)
        self.assertEqual(2, row.sorties)
        self.assertTrue(row.path_id.startswith("P/A1/M1/A1/fighter/"))

    def test_infeasible_solver_does_not_create_canonical_solution(self):
        b, maps, _p, pack, _model = self._fixture()
        with self.assertRaisesRegex(sd.SolutionDumpError, "forbidden"):
            sd.build_solution(
                b.ds, maps, pack, FakeSolvedModel({}, status="infeasible", n_solutions=0),
                run_id="R1", run_params=b.run_params,
            )

    def test_solution_dump_revalidates_model_facts_before_export(self):
        b, maps, p, pack, model = self._fixture()
        # Make solved quantity violate the independent departure-capacity fact.
        b.ds["timeview"]["cap"]["A1"][0] = 1
        with self.assertRaisesRegex(sd.SolutionDumpError, "invariant validation"):
            sd.build_solution(
                b.ds, maps, pack, model, run_id="R1", run_params=b.run_params,
                cluster_cfg={"enabled": True, "S": ["A1", "A2"]},
            )

    def test_noninteger_mip_quantity_is_rejected(self):
        b, maps, p, pack, _model = self._fixture()
        model = FakeSolvedModel({pack["x_path"][p.key]: 1.5})
        with self.assertRaisesRegex(sd.SolutionDumpError, "non-integer"):
            sd.build_solution(b.ds, maps, pack, model, run_id="R1", run_params=b.run_params)

    def test_written_json_contains_no_legacy_operations_or_solver_diagnostics(self):
        b, maps, _p, pack, model = self._fixture()
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "solution.json"
            sd.dump_solution(
                b.ds, maps, pack, model, str(path), run_id="R1", run_params=b.run_params,
                cluster_cfg={"enabled": True, "S": ["A1", "A2"]},
            )
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual({"run_id", "selected_cluster", "sortie_chains"}, set(data))
        self.assertNotIn("operations", data)
        self.assertNotIn("solver", data)


if __name__ == "__main__":
    unittest.main()
