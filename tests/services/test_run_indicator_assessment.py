from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.services.run_indicator_assessment import (
    RunIndicatorAssessmentError,
    RunIndicatorAssessmentService,
    derive_indicator_facts,
)


DIGEST = "a" * 64


def snapshot_payload() -> dict:
    return {
        "situation": {
            "airports": [
                {"operational_profile": {"airport_id": "AP1", "capacity_per_window": 10}},
                {"operational_profile": {"airport_id": "AP2", "capacity_per_window": 5}},
            ]
        }
    }


def complete_metrics() -> dict:
    return {
        "schema_version": "metrics.v1",
        "summary": {"completion_ratio": 0.9},
        "time_axis": {"windows": [1, 2]},
        "airports": {
            "AP1": {"capacity": {"available": [10, 5]}},
            "AP2": {"capacity": {"available": [5, 5]}},
        },
        "aircraft_inventory": {
            "by_airport": {
                "AP1": {
                    "fighter": {
                        "baseline_initial_quantity": 10,
                        "available_before_departure": [10, 5],
                    }
                },
                "AP2": {
                    "fighter": {
                        "baseline_initial_quantity": 5,
                        "available_before_departure": [5, 5],
                    }
                },
            }
        },
        "resources": {
            "resource_types": {
                "fuel": {"category": "fuel", "unit": "t"},
                "MAT-1": {"category": "material", "unit": "t"},
            },
            "category_min_remaining_ratio": {
                "fuel": {"ratio": 0.8, "resource_type_id": "fuel"},
                "material": {"ratio": 0.6, "resource_type_id": "MAT-1"},
            },
        },
    }


class CaptureAssessmentRepository:
    def __init__(self) -> None:
        self.saved = []

    def save_run_evaluation(self, evaluation):
        self.saved.append(evaluation)
        return evaluation


class FakeRunRepository:
    def __init__(self, *, status: str = "succeeded", metrics: dict | None = None) -> None:
        self.record = SimpleNamespace(
            run_id="RUN-1",
            status=status,
            snapshot_hash=DIGEST,
            metrics_hash=DIGEST if status == "succeeded" else None,
        )
        self.metrics = metrics or complete_metrics()

    def get(self, _run_id):
        return self.record

    def get_result_payloads(self, _run_id):
        if self.record.status != "succeeded":
            return None
        return ({"run_id": "RUN-1", "sortie_chains": [{"path_id": "P1"}]}, self.metrics)


class FakeSnapshotRepository:
    def get(self, _run_id):
        return SimpleNamespace(to_dict=snapshot_payload)


class FakeIndicatorRepository:
    def published_weights(self, _set_id):
        codes = (
            "task_completion_rate",
            "capacity_availability_rate",
            "aircraft_availability_rate",
            "resource_reserve_retention_rate",
        )
        return {
            "indicator_set_id": "INDSET-RUN-ASSESSMENT-V1",
            "version": "run-assessment.v1",
            "status": "available",
            "weight_source": "ahp",
            "trace": {"consistency_ratio": 0.0, "consistent": True},
            "items": [
                {
                    "indicator_id": f"IND:{code}",
                    "code": code,
                    "name": code,
                    "weight": 0.25,
                    "weight_source": "ahp",
                    "calculation_type": f"metrics.{code}",
                    "calculation_status": "available",
                }
                for code in codes
            ],
        }


class IndicatorFactTests(unittest.TestCase):
    def test_metrics_map_uses_formal_denominators_and_no_cross_unit_resource_sum(self) -> None:
        facts = derive_indicator_facts(complete_metrics(), snapshot_payload())

        self.assertEqual(0.9, facts["task_completion_rate"].raw_value)
        self.assertAlmostEqual(25 / 30, facts["capacity_availability_rate"].raw_value)
        self.assertAlmostEqual(25 / 30, facts["aircraft_availability_rate"].raw_value)
        self.assertEqual(0.6, facts["resource_reserve_retention_rate"].raw_value)
        self.assertIn("minimum", facts["resource_reserve_retention_rate"].evidence)

    def test_missing_or_out_of_range_metrics_are_unavailable_not_zero(self) -> None:
        missing = complete_metrics()
        missing["resources"]["category_min_remaining_ratio"] = {}
        with self.assertRaisesRegex(RunIndicatorAssessmentError, "resource"):
            derive_indicator_facts(missing, snapshot_payload())

        invalid = complete_metrics()
        invalid["summary"]["completion_ratio"] = 1.1
        with self.assertRaisesRegex(RunIndicatorAssessmentError, r"\[0,1\]"):
            derive_indicator_facts(invalid, snapshot_payload())

    def test_cross_airport_return_may_exceed_local_stock_but_not_fleet_total(self) -> None:
        redistributed = complete_metrics()
        redistributed["aircraft_inventory"]["by_airport"]["AP1"]["fighter"][
            "available_before_departure"
        ] = [12, 7]
        redistributed["aircraft_inventory"]["by_airport"]["AP2"]["fighter"][
            "available_before_departure"
        ] = [3, 3]

        fact = derive_indicator_facts(redistributed, snapshot_payload())[
            "aircraft_availability_rate"
        ]
        self.assertAlmostEqual(25 / 30, fact.raw_value)
        self.assertEqual("fleet_wide_window_conservation", fact.evidence["validation"])

        invalid = complete_metrics()
        invalid["aircraft_inventory"]["by_airport"]["AP1"]["fighter"][
            "available_before_departure"
        ] = [16, 5]
        with self.assertRaisesRegex(RunIndicatorAssessmentError, "fleet-wide"):
            derive_indicator_facts(invalid, snapshot_payload())


class RunIndicatorAssessmentServiceTests(unittest.TestCase):
    def build_service(self, *, status: str = "succeeded", metrics: dict | None = None):
        captured = CaptureAssessmentRepository()
        service = RunIndicatorAssessmentService(
            run_repository=FakeRunRepository(status=status, metrics=metrics),
            snapshot_repository=FakeSnapshotRepository(),
            indicator_repository=FakeIndicatorRepository(),
            assessment_repository=captured,
        )
        return service, captured

    def test_complete_run_persists_traceable_fuzzy_evaluation_and_risk(self) -> None:
        service, captured = self.build_service()
        result = service.evaluate("RUN-1")

        self.assertEqual("available", result.status)
        self.assertEqual(DIGEST, result.snapshot_hash)
        self.assertEqual(DIGEST, result.metrics_hash)
        self.assertEqual("ahp", result.weight_source)
        self.assertEqual(4, len(result.indicators))
        self.assertTrue(0 <= result.support_index <= 1)
        self.assertTrue(1 <= result.risk_level <= 5)
        self.assertEqual([result], captured.saved)

    def test_incomplete_metrics_persist_explicit_unavailable_without_score(self) -> None:
        metrics = complete_metrics()
        metrics["resources"]["category_min_remaining_ratio"] = {}
        service, captured = self.build_service(metrics=metrics)
        result = service.evaluate("RUN-1")

        self.assertEqual("unavailable", result.status)
        self.assertIsNone(result.support_index)
        self.assertIn("resource", result.unavailable_reason)
        self.assertEqual([result], captured.saved)

    def test_failed_or_cancelled_run_never_enters_assessment(self) -> None:
        for status in ("failed", "cancelled"):
            service, captured = self.build_service(status=status)
            with self.assertRaisesRegex(RunIndicatorAssessmentError, "succeeded"):
                service.evaluate("RUN-1")
            self.assertEqual([], captured.saved)


if __name__ == "__main__":
    unittest.main()
