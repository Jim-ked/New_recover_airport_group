from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.domain.solution import Solution, SortieChain, SolutionValidationError


class SolutionDomainTests(unittest.TestCase):
    def chain(self, **overrides):
        data = dict(
            path_id="P/A1/M1/A1/fighter/4/6/8",
            origin_airport_id="A1", mission_id="M1", return_airport_id="A1",
            aircraft_type="fighter", depart_window=4, return_window=6, ready_window=8,
            sorties=2,
        )
        data.update(overrides)
        return SortieChain(**data)

    def test_solution_is_complete_chain_fact_not_split_legs(self):
        sol = Solution.build(run_id="R1", selected_cluster=["A2", "A1"], sortie_chains=[self.chain()])
        data = sol.to_dict()
        self.assertEqual(["A1", "A2"], data["selected_cluster"])
        self.assertIn("sortie_chains", data)
        self.assertNotIn("operations", data)
        self.assertEqual("A1", data["sortie_chains"][0]["origin_airport_id"])
        self.assertEqual("A1", data["sortie_chains"][0]["return_airport_id"])

    def test_nonpositive_or_nonordered_chain_is_rejected(self):
        with self.assertRaises(SolutionValidationError):
            self.chain(sorties=0)
        with self.assertRaises(SolutionValidationError):
            self.chain(return_window=3)

    def test_duplicate_path_id_is_rejected(self):
        row = self.chain()
        with self.assertRaisesRegex(SolutionValidationError, "path_id"):
            Solution(run_id="R1", selected_cluster=(), sortie_chains=(row, row))


if __name__ == "__main__":
    unittest.main()
