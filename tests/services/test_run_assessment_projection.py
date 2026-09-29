from __future__ import annotations

import unittest

from backend.services.run_assessment_projection import (
    RunAssessmentProjectionError,
    derive_capacity_observation,
)


class RunAssessmentProjectionTests(unittest.TestCase):
    def test_capacity_observation_uses_sum_over_valid_denominators(self) -> None:
        observation = derive_capacity_observation(
            airport_id="AP1",
            baseline_capacity=10,
            windows=[9, 10, 11, 12],
            capacity={
                "available": [10, 0, 5, 10],
                "used_total": [5, 0, 5, 0],
            },
        )

        self.assertAlmostEqual(10 / 25, observation.period_occupancy)
        self.assertEqual(((9, 1.0), (10, 0.0), (11, 0.5), (12, 1.0)), observation.availability_curve)
        self.assertEqual(3, observation.effective_windows)
        self.assertEqual(1, observation.unavailable_windows)

    def test_no_effective_capacity_denominator_is_not_converted_to_zero(self) -> None:
        observation = derive_capacity_observation(
            airport_id="AP1",
            baseline_capacity=10,
            windows=[1, 2],
            capacity={"available": [0, 0], "used_total": [0, 0]},
        )

        self.assertIsNone(observation.period_occupancy)
        self.assertEqual(0, observation.effective_windows)
        self.assertEqual(2, observation.unavailable_windows)

    def test_partial_or_inconsistent_metrics_are_rejected(self) -> None:
        with self.assertRaisesRegex(RunAssessmentProjectionError, "formal time axis"):
            derive_capacity_observation(
                airport_id="AP1",
                baseline_capacity=10,
                windows=[1, 2, 3],
                capacity={"available": [10, 10], "used_total": [0, 0]},
            )
        with self.assertRaisesRegex(RunAssessmentProjectionError, "exceeds available"):
            derive_capacity_observation(
                airport_id="AP1",
                baseline_capacity=10,
                windows=[1],
                capacity={"available": [5], "used_total": [6]},
            )


if __name__ == "__main__":
    unittest.main()
