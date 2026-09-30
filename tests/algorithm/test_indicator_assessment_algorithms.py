from __future__ import annotations

import unittest

from backend.algorithm.assessment.ahp_weights import AHPError, analyze_ahp_matrix
from backend.algorithm.assessment.fuzzy_comprehensive import (
    FuzzyInputError,
    evaluate_elements,
    membership_vector,
)


class AhpWeightTests(unittest.TestCase):
    def test_consistent_matrix_returns_normalized_weights_and_trace(self) -> None:
        result = analyze_ahp_matrix(
            [[1, 2, 4], [0.5, 1, 2], [0.25, 0.5, 1]]
        )

        self.assertAlmostEqual(1.0, sum(result.weights.values()), places=8)
        self.assertLessEqual(result.consistency_ratio, 0.1)
        self.assertTrue(result.consistent)
        self.assertGreater(result.weights[0], result.weights[2])

    def test_inconsistent_or_nonreciprocal_matrix_is_rejected(self) -> None:
        with self.assertRaisesRegex(AHPError, "inconsistent"):
            analyze_ahp_matrix([[1, 9, 1 / 9], [1 / 9, 1, 9], [9, 1 / 9, 1]])
        with self.assertRaisesRegex(AHPError, "reciprocal"):
            analyze_ahp_matrix([[1, 2], [0.25, 1]])


class FuzzyEvaluationTests(unittest.TestCase):
    def test_reference_membership_and_weighted_evaluation_are_preserved(self) -> None:
        membership = membership_vector(0.9)
        result = evaluate_elements(
            {"mission": 0.9, "capacity": 0.6},
            {"mission": 3, "capacity": 1},
        )

        self.assertAlmostEqual(1.0, sum(membership.values()), places=8)
        self.assertAlmostEqual(1.0, sum(result.normalized_weights.values()), places=8)
        self.assertGreater(result.support_index, 0.6)

    def test_out_of_range_values_and_invalid_weights_are_rejected_not_clamped(self) -> None:
        with self.assertRaisesRegex(FuzzyInputError, r"\[0, 1\]"):
            evaluate_elements({"mission": 1.01}, {"mission": 1})
        with self.assertRaisesRegex(FuzzyInputError, "nonnegative"):
            evaluate_elements({"mission": 0.5}, {"mission": -1})
        with self.assertRaisesRegex(FuzzyInputError, "missing weights"):
            evaluate_elements({"mission": 0.5}, {"capacity": 1})


if __name__ == "__main__":
    unittest.main()
