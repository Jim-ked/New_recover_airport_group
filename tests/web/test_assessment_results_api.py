from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from backend.auth.principal import Principal
from backend.domain.airport import AirportBase
from backend.domain.assessment import (
    AssessmentAirportRisk,
    AssessmentComparison,
    AssessmentResult,
    IndicatorAssessmentValue,
    RunIndicatorAssessment,
)
from backend.storage.airport_repository import AirportRepository
from backend.storage.assessment_repository import AssessmentRepository
from backend.storage.database import initialize_database
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.web.assessment_results_api import AssessmentResultsApi
from backend.web.app import create_app
from backend.web.composition import build_run_api


def airport(airport_id: str, name: str) -> AirportBase:
    return AirportBase.from_mapping(
        {
            "airport_id": airport_id,
            "airport_name": name,
            "facility_type": "medium_airport",
            "role": "joint",
            "icao_code": None,
            "iata_code": None,
            "region": "East",
            "municipality": "City",
            "longitude": 110.0,
            "latitude": 30.0,
            "elevation_m": None,
            "scheduled_service": True,
            "parking_stand_count": None,
            "runway_count": 0,
            "max_runway_length_m": None,
            "runways": [],
        }
    )


class AssessmentResultsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "assessment.sqlite3"
        initialize_database(self.db_path)
        airport_repository = AirportRepository(self.db_path)
        airport_repository.save_airport(airport("AP1", "一号机场"))
        airport_repository.save_airport(airport("AP2", "二号机场"))
        self.repository = AssessmentRepository(self.db_path)
        self.api = AssessmentResultsApi(
            assessment_repository=self.repository,
            airport_repository=airport_repository,
            run_repository=RunRepository(self.db_path),
            snapshot_repository=RunSnapshotRepository(self.db_path),
        )
        self.viewer = Principal("viewer", role="viewer")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed(self) -> None:
        self.repository.save_result(
            AssessmentResult.from_mapping(
                {
                    "id": "AR-1",
                    "airport_id": "AP1",
                    "scenario_type": "medium",
                    "curves": {"0": 1.0, "1": 0.6, "2": 0.75},
                    "decline_rate": 0.4,
                    "recovery_slope": 0.15,
                    "task_threshold": 0.5,
                    "initial_capacity": 1.0,
                }
            )
        )
        self.repository.save_result(
            AssessmentResult.from_mapping(
                {
                    "id": "AR-2",
                    "airport_id": "AP2",
                    "scenario_type": "high",
                    "curves": {"0": 1.0, "1": 0.4},
                    "decline_rate": 0.6,
                    "recovery_slope": 0.1,
                    "initial_capacity": 0.9,
                }
            )
        )
        self.repository.upsert_risk(
            AssessmentAirportRisk.from_mapping(
                {"airport_id": "AP1", "risk_level": 4, "risk_score": 0.72}
            )
        )
        self.repository.save_comparison(
            AssessmentComparison.from_mapping(
                {
                    "id": "CMP-1",
                    "base_airport_id": "AP1",
                    "base_scenario_type": "medium",
                    "compared_airport_id": "AP2",
                    "compared_scenario_type": "high",
                    "conclusion": "二号机场恢复较慢",
                    "log": "正式评估记录",
                }
            )
        )

    def test_overview_is_empty_without_persisted_assessment(self) -> None:
        response = self.api.overview(principal=self.viewer)
        self.assertEqual(200, response.status)
        self.assertEqual([], response.body["airport_ids"])
        self.assertEqual({}, response.body["cells"])
        self.assertEqual(0, response.body["total_results"])

    def test_overview_returns_latest_matrix_cell_risk_and_airport_names(self) -> None:
        self.seed()
        self.repository.save_result(
            AssessmentResult.from_mapping(
                {
                    "id": "AR-1-NEW",
                    "airport_id": "AP1",
                    "scenario_type": "medium",
                    "curves": {"0": 1.0, "1": 0.8},
                    "decline_rate": 0.2,
                    "recovery_slope": 0.2,
                    "initial_capacity": 1.0,
                    "created_at": "2099-01-01 00:00:00",
                }
            )
        )
        response = self.api.overview(principal=self.viewer)
        self.assertEqual(200, response.status)
        self.assertEqual("assessment-visual.v1", response.body["schema_version"])
        self.assertEqual("一号机场", response.body["airport_names"]["AP1"])
        self.assertEqual("AR-1-NEW", response.body["cells"]["AP1"]["medium"]["result_id"])
        self.assertEqual(4, response.body["risks"][0]["risk_level"])

    def test_curves_and_comparisons_return_persisted_facts(self) -> None:
        self.seed()
        curves = self.api.curves(principal=self.viewer, airport_id="AP1")
        self.assertEqual([0, 1, 2], curves.body["series"][0]["steps"])
        self.assertEqual([1.0, 0.6, 0.75], curves.body["series"][0]["support"])
        comparisons = self.api.comparisons(principal=self.viewer)
        self.assertEqual(1, comparisons.body["total"])
        self.assertEqual("CMP-1", comparisons.body["items"][0]["id"])

    def test_indicator_evaluations_expose_traceable_values_weights_and_risk(self) -> None:
        evaluation = RunIndicatorAssessment(
            run_id="RUN-1",
            snapshot_hash="a" * 64,
            metrics_hash="b" * 64,
            indicator_set_id="INDSET-RUN-ASSESSMENT-V1",
            indicator_set_version="run-assessment.v1",
            indicators=(
                IndicatorAssessmentValue(
                    indicator_id="IND:task",
                    code="task_completion_rate",
                    name="任务履行率",
                    raw_value=0.9,
                    normalized_value=0.9,
                    weight=1.0,
                    weight_source="ahp",
                    calculation_type="metrics.summary.completion_ratio",
                    evidence={"metrics_path": "summary.completion_ratio"},
                ),
            ),
            status="available",
            calculation_version="run-indicator-assessment.v1",
            weight_source="ahp",
            weight_trace={"consistency_ratio": 0.0},
            support_index=0.9,
            overall_membership={"very_high": 1.0},
            weighted_composite=0.9,
            risk_score=0.1,
            risk_level=1,
            risk_status="available",
        )
        self.repository.list_run_evaluations = lambda **_kwargs: ([evaluation], 1)
        self.api.runs.get = lambda _run_id: type(
            "Run",
            (),
            {
                "owner_user_id": "viewer",
                "situation_id": "ST-1",
                "snapshot_hash": "a" * 64,
                "metrics_hash": "b" * 64,
            },
        )()
        self.api.snapshots.get = lambda _run_id: type(
            "Snapshot",
            (),
            {
                "to_dict": lambda _self: {
                    "situation": {"situation_name": "验证情境"},
                    "run_config": {"damage_scenario_id": "DS-1"},
                }
            },
        )()

        response = self.api.evaluations(principal=self.viewer)

        self.assertEqual(200, response.status)
        self.assertEqual("assessment-evaluation.v1", response.body["schema_version"])
        self.assertEqual("RUN-1", response.body["items"][0]["run_id"])
        self.assertEqual("ahp", response.body["items"][0]["weight_source"])
        self.assertEqual(1, response.body["items"][0]["risk"]["level"])
        self.assertEqual("current", response.body["items"][0]["provenance_status"])
        self.assertEqual("ST-1", response.body["items"][0]["run"]["situation_id"])
        self.assertEqual("验证情境", response.body["items"][0]["run"]["situation_name"])
        self.assertEqual("DS-1", response.body["items"][0]["run"]["damage_scenario_id"])

    def _save_unavailable_run_evaluation(self, run_id: str, owner_user_id: str) -> None:
        payload = {
            "schema": "run_input_snapshot_v4",
            "run_id": run_id,
            "situation": {"situation_id": "ST-1", "situation_name": "权限测试情境"},
            "run_config": {"damage_scenario_id": None},
        }
        payload_json = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        snapshot_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self.repository.connect() as conn:
            conn.execute(
                """INSERT INTO run_input_snapshots
                   (run_id,situation_id,situation_content_hash,snapshot_hash,payload_json)
                   VALUES (?,?,?,?,?)""",
                (run_id, "ST-1", "c" * 64, snapshot_hash, payload_json),
            )
            conn.execute(
                """INSERT INTO runs
                   (run_id,owner_user_id,situation_id,snapshot_hash,status)
                   VALUES (?,?,?,?, 'succeeded')""",
                (run_id, owner_user_id, "ST-1", snapshot_hash),
            )
            conn.execute(
                """INSERT INTO run_results
                   (run_id,solution_hash,solution_json,metrics_hash,metrics_json)
                   VALUES (?,?,?,?,?)""",
                (run_id, "e" * 64, "{}", "d" * 64, "{}"),
            )
        self.repository.save_run_evaluation(
            RunIndicatorAssessment(
                run_id=run_id,
                snapshot_hash=snapshot_hash,
                metrics_hash="d" * 64,
                indicator_set_id="INDSET-RUN-ASSESSMENT-V1",
                indicator_set_version="run-assessment.v1",
                indicators=(),
                status="unavailable",
                unavailable_reason="正式指标输入不完整",
                calculation_version="run-indicator-assessment.v1",
                weight_source="ahp",
                weight_trace={"consistent": True},
                risk_status="unavailable",
                risk_reason="正式指标输入不完整",
            )
        )

    def test_indicator_evaluations_are_owner_scoped_and_admins_see_all_runs(self) -> None:
        self._save_unavailable_run_evaluation("RUN-OWN", "operator-a")
        self._save_unavailable_run_evaluation("RUN-FOREIGN", "operator-b")

        operator = self.api.evaluations(
            principal=Principal("operator-a", role="operator")
        )
        admin = self.api.evaluations(
            principal=Principal("admin", role="admin")
        )

        self.assertEqual(["RUN-OWN"], [item["run_id"] for item in operator.body["items"]])
        self.assertEqual(1, operator.body["total"])
        self.assertEqual(
            {"RUN-OWN", "RUN-FOREIGN"},
            {item["run_id"] for item in admin.body["items"]},
        )
        self.assertEqual(2, admin.body["total"])

    def test_stale_evaluation_provenance_is_not_returned_as_available(self) -> None:
        evaluation = RunIndicatorAssessment(
            run_id="RUN-STALE",
            snapshot_hash="a" * 64,
            metrics_hash="b" * 64,
            indicator_set_id="INDSET-RUN-ASSESSMENT-V1",
            indicator_set_version="run-assessment.v1",
            indicators=(
                IndicatorAssessmentValue(
                    indicator_id="IND:task",
                    code="task_completion_rate",
                    name="任务履行率",
                    raw_value=0.9,
                    normalized_value=0.9,
                    weight=1.0,
                    weight_source="ahp",
                    calculation_type="metrics.summary.completion_ratio",
                    evidence={},
                ),
            ),
            status="available",
            calculation_version="run-indicator-assessment.v1",
            weight_source="ahp",
            weight_trace={"consistent": True},
            support_index=0.9,
            overall_membership={"very_high": 1.0},
            weighted_composite=0.9,
            risk_score=0.1,
            risk_level=1,
            risk_status="available",
        )
        self.repository.list_run_evaluations = lambda **_kwargs: ([evaluation], 1)
        self.api.runs.get = lambda _run_id: type(
            "Run",
            (),
            {
                "owner_user_id": "viewer",
                "situation_id": "ST-1",
                "snapshot_hash": "a" * 64,
                "metrics_hash": "e" * 64,
            },
        )()
        self.api.snapshots.get = lambda _run_id: None

        response = self.api.evaluations(principal=self.viewer)
        item = response.body["items"][0]

        self.assertEqual("stale", item["provenance_status"])
        self.assertEqual("unavailable", item["status"])
        self.assertIsNone(item["support_index"])
        self.assertEqual([], item["indicators"])
        self.assertIn("重新生成", item["unavailable_reason"])

    def test_run_derived_capacity_observation_has_explicit_semantics_and_provenance(self) -> None:
        digest = "a" * 64
        self.repository.save_result(
            AssessmentResult.from_mapping(
                {
                    "id": "v5cap:RUN-1:AP1",
                    "airport_id": "AP1",
                    "scenario_type": "R1",
                    "curves": {"9": 1.0, "10": 0.5},
                    "decline_rate": 0.5,
                    "recovery_slope": 0.0,
                    "initial_capacity": 10,
                    "metric_kind": "capacity_availability_v1",
                    "matrix_value": 0.4,
                    "source_run_id": "RUN-1",
                    "snapshot_hash": digest,
                    "metrics_hash": digest,
                    "effective_windows": 2,
                    "unavailable_windows": 0,
                }
            )
        )

        overview = self.api.overview(principal=self.viewer).body
        cell = overview["cells"]["AP1"]["R1"]
        self.assertEqual("capacity_availability_v1", cell["metric_kind"])
        self.assertEqual(0.4, cell["matrix_value"])
        self.assertEqual("RUN-1", cell["source_run_id"])
        self.assertEqual("unavailable", overview["risk_status"]["status"])
        curves = self.api.curves(principal=self.viewer, airport_id="AP1").body
        self.assertEqual("起降容量可用比例", curves["series"][0]["metric_label"])
        self.assertEqual([1.0, 0.5], curves["series"][0]["values"])

    def test_invalid_curve_request_and_missing_permission_are_rejected(self) -> None:
        invalid = self.api.curves(principal=self.viewer, airport_id=" ")
        self.assertEqual(400, invalid.status)
        self.assertEqual("airport_id", invalid.body["error"]["field"])
        denied = self.api.overview(
            principal=Principal("restricted", permissions=frozenset())
        )
        self.assertEqual(403, denied.status)

    def test_flask_routes_are_authenticated_and_return_the_same_contract(self) -> None:
        self.seed()
        app = create_app(
            run_api=build_run_api(self.db_path),
            assessment_results_api=self.api,
            principal_resolver=lambda _request: self.viewer,
            enable_ui=False,
        )
        response = app.test_client().get("/api/results/assessment/overview")
        self.assertEqual(200, response.status_code)
        self.assertEqual("assessment-visual.v1", response.get_json()["schema_version"])

        denied_app = create_app(
            run_api=build_run_api(self.db_path),
            assessment_results_api=self.api,
            principal_resolver=lambda _request: None,
            enable_ui=False,
        )
        denied = denied_app.test_client().get("/api/results/assessment/overview")
        self.assertEqual(401, denied.status_code)


if __name__ == "__main__":
    unittest.main()
