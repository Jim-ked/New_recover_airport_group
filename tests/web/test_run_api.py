from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from backend.auth.principal import Principal
from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportAircraftSupport, AirportOperationalProfile, AirportResourceStock
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.domain.situation import Situation, SituationAirport
from backend.storage.airport_repository import AirportRepository
from backend.storage.run_repository import RunRepository
from backend.storage.situation_repository import SituationRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.services.run_submission_service import SolverProbeResult
from backend.web.composition import build_run_api


class RunApiTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "app.sqlite"
        self.airports = AirportRepository(self.db)
        self.situations = SituationRepository(self.db)
        self.runs = RunRepository(self.db)
        self.airports.init_schema()

        self.airports.save_aircraft_type(AircraftType.from_mapping({
            "aircraft_type_id": "fighter", "name": "Fighter", "speed_kmh": 800,
            "max_range_km": 1200, "reserve_ratio": 0.2,
            "departure_capacity_occupancy_factor": 1.0,
            "arrival_capacity_occupancy_factor": 1.0,
        }))
        self.airports.save_resource_type(ResourceType.from_mapping({
            "resource_type_id": "FUEL-1", "name": "Fuel", "category": "fuel", "unit": "t",
        }))
        self.airports.save_aircraft_resource_requirement(AircraftResourceRequirement.from_mapping({
            "aircraft_type_id": "fighter", "resource_type_id": "FUEL-1",
            "basis": "per_hour", "quantity": 1.5,
        }))
        airport = AirportBase.from_mapping({
            "airport_id": "A1", "airport_name": "A1", "facility_type": "medium_airport",
            "role": "joint", "longitude": 118.8, "latitude": 31.7,
            "scheduled_service": True, "runway_count": 0, "max_runway_length_m": None, "runways": [],
        })
        profile = AirportOperationalProfile(
            airport_id="A1", configuration_complete=True, capacity_per_window=8,
            support_level="L1", aircraft_support=(AirportAircraftSupport("fighter", 3, 2),),
            resource_stocks=(AirportResourceStock("FUEL-1", 50, 0),),
        )
        self.situations.save_situation(Situation(
            situation_id="S1", name="S1",
            airports=(SituationAirport(airport=airport, operational_profile=profile),),
            missions=(Mission(
                "M1", "M1", 120.0, 32.0, 4, 10,
                (MissionAircraftRequirement("fighter", 2, 1),),
            ),),
        ), owner_user_id="U1")
        ids = iter(["RUN-API-1", "RUN-API-2", "RUN-API-3", "RUN-API-4"])
        self.api = build_run_api(
            self.db,
            run_id_factory=lambda: next(ids),
            solver_probe=lambda: SolverProbeResult(True, "solver available in test"),
        )
        self.u1 = Principal("U1")
        self.u2 = Principal("U2")
        self.admin = Principal("ADMIN", is_admin=True)
        self.config = {
            "damage_scenario_id": None,
            "preference_mode": "sortie_max",
            "cluster_enabled": False,
            "cluster_size": None,
            "core_airports": [],
            "aircraft_type_weight": {"fighter": 1.0},
            "mip_time_limit_s": 120,
        }
        self.body = {"situation_id": "S1", "run_config": self.config}

    def tearDown(self):
        self._td.cleanup()

    def test_validate_is_200_and_has_no_persistence_side_effect(self):
        response = self.api.validate(self.body, principal=self.u1)
        self.assertEqual(200, response.status)
        self.assertEqual("passed", response.body["status"])
        self.assertTrue(response.body["can_submit"])
        self.assertEqual(1, response.body["input_summary"]["od_pair_count"])
        self.assertRegex(response.body["validated_input_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual([], self.runs.list_for_owner("U1"))

    def test_worker_status_is_read_only_and_reports_unconfigured_or_stale_worker(self):
        response = self.api.worker_status(principal=self.u1)
        self.assertEqual(200, response.status)
        self.assertFalse(response.body["connected"])
        self.assertIn(response.body["reason"], {"heartbeat_missing", "heartbeat_stale", "status_unconfigured"})

    def test_submit_can_bind_to_exact_validated_input_and_rejects_external_mutation(self):
        validation = self.api.validate(self.body, principal=self.u1)
        validated_hash = validation.body["validated_input_hash"]

        current = self.situations.get_situation("S1")
        metadata = self.situations.get_metadata("S1")
        self.assertIsNotNone(current)
        self.assertIsNotNone(metadata)
        self.situations.save_situation(
            replace(current, name="S1 changed elsewhere"),
            owner_user_id="U1",
            expected_content_hash=metadata["content_hash"],
        )

        stale_body = dict(self.body)
        stale_body["expected_input_hash"] = validated_hash
        stale = self.api.submit(stale_body, principal=self.u1)
        self.assertEqual(409, stale.status)
        self.assertEqual("RUN_VALIDATION_STALE", stale.body["error"]["code"])
        self.assertEqual([], self.runs.list_for_owner("U1"))

        current_validation = self.api.validate(self.body, principal=self.u1)
        current_body = dict(self.body)
        current_body["expected_input_hash"] = current_validation.body["validated_input_hash"]
        created = self.api.submit(current_body, principal=self.u1)
        self.assertEqual(201, created.status)
        self.assertEqual("RUN-API-2", created.body["run_id"])

    def test_submit_list_detail_events_and_cancel_are_owner_scoped(self):
        created = self.api.submit(self.body, principal=self.u1)
        self.assertEqual(201, created.status)
        self.assertEqual("RUN-API-1", created.body["run_id"])
        self.assertEqual("queued", created.body["status"])

        listed = self.api.list(principal=self.u1, statuses=["queued"], limit="10", offset="0")
        self.assertEqual(200, listed.status)
        self.assertEqual(["RUN-API-1"], [x["run_id"] for x in listed.body["items"]])

        forbidden = self.api.detail("RUN-API-1", principal=self.u2)
        self.assertEqual(403, forbidden.status)
        self.assertEqual("FORBIDDEN", forbidden.body["error"]["code"])
        admin = self.api.detail("RUN-API-1", principal=self.admin)
        self.assertEqual(200, admin.status)

        self.runs.append_event(
            "RUN-API-1", level="INFO", stage="data_preparation", event="validated",
            message="validated", payload={"x": 1},
        )
        events = self.api.events("RUN-API-1", principal=self.u1, after_seq="0", limit="20")
        self.assertEqual(1, len(events.body["events"]))
        self.assertEqual(1, events.body["next_after_seq"])

        cancelled = self.api.cancel("RUN-API-1", principal=self.u1)
        self.assertEqual(200, cancelled.status)
        self.assertEqual("cancelled", cancelled.body["status"])

    def test_solution_metrics_are_success_only_and_never_reconstructed_in_web(self):
        created = self.api.submit(self.body, principal=self.u1)
        run_id = created.body["run_id"]
        not_ready = self.api.metrics(run_id, principal=self.u1)
        self.assertEqual(409, not_ready.status)
        self.assertEqual("RUN_RESULT_NOT_READY", not_ready.body["error"]["code"])

        queued_situation = self.api.situation(run_id, principal=self.u1)
        self.assertEqual(409, queued_situation.status)

        self.runs.claim_running(run_id)
        solution = {"run_id": run_id, "selected_cluster": [], "sortie_chains": []}
        metrics = {"run_id": run_id, "schema_version": "metrics.v1", "summary": {"x": 1}}
        self.runs.save_success(run_id, solution=solution, metrics=metrics)
        self.assertEqual(solution, self.api.solution(run_id, principal=self.u1).body)
        self.assertEqual(metrics, self.api.metrics(run_id, principal=self.u1).body)
        situation = self.api.situation(run_id, principal=self.u1)
        self.assertEqual("S1", situation.body["situation_id"])

    def test_history_filters_are_snapshot_backed_and_return_total(self):
        first = self.api.submit(self.body, principal=self.u1)
        second = self.api.submit(
            {"situation_id": "S1", "run_config": {**self.config, "cluster_enabled": True, "cluster_size": 1}},
            principal=self.u1,
        )
        self.assertEqual(201, first.status)
        self.assertEqual(201, second.status)

        by_query = self.api.list(principal=self.u1, run_id_query="API-1", limit="10", offset="0")
        self.assertEqual(1, by_query.body["total"])
        self.assertEqual(["RUN-API-1"], [x["run_id"] for x in by_query.body["items"]])

        clustered = self.api.list(principal=self.u1, cluster_enabled="true")
        self.assertEqual(1, clustered.body["total"])
        self.assertEqual("RUN-API-2", clustered.body["items"][0]["run_id"])

        task = self.api.list(principal=self.u1, task_id="M1", no_damage="true")
        self.assertEqual(2, task.body["total"])

        self.runs.claim_running("RUN-API-1")
        self.runs.save_success(
            "RUN-API-1",
            solution={"run_id": "RUN-API-1", "selected_cluster": ["A1"], "sortie_chains": []},
            metrics={"run_id": "RUN-API-1", "schema_version": "metrics.v1", "summary": {}},
        )
        selected = self.api.list(principal=self.u1, selected_airport_id="A1")
        self.assertEqual(1, selected.body["total"])
        self.assertEqual("RUN-API-1", selected.body["items"][0]["run_id"])

        bad = self.api.list(principal=self.u1, no_damage="true", damage_scenario_id="D1")
        self.assertEqual(400, bad.status)
        self.assertEqual("no_damage", bad.body["error"]["field"])

    def test_error_envelope_distinguishes_format_not_found_semantic_and_state_errors(self):
        bad_shape = self.api.submit({**self.body, "scene_file": "legacy.json"}, principal=self.u1)
        self.assertEqual(400, bad_shape.status)
        self.assertEqual("INVALID_REQUEST", bad_shape.body["error"]["code"])
        self.assertEqual("scene_file", bad_shape.body["error"]["field"])

        invalid_config = self.api.validate(
            {"situation_id": "S1", "run_config": {**self.config, "cluster_enabled": "yes"}},
            principal=self.u1,
        )
        self.assertEqual(422, invalid_config.status)
        self.assertEqual("RUN_VALIDATION_FAILED", invalid_config.body["error"]["code"])
        self.assertEqual("cluster_enabled", invalid_config.body["error"]["field"])

        missing = self.api.validate(
            {"situation_id": "NOPE", "run_config": self.config}, principal=self.u1
        )
        self.assertEqual(404, missing.status)
        self.assertEqual("SITUATION_NOT_FOUND", missing.body["error"]["code"])

        bad_filter = self.api.list(principal=self.u1, statuses=["postprocessing"])
        self.assertEqual(400, bad_filter.status)
        self.assertEqual("status", bad_filter.body["error"]["field"])


    def test_preflight_reports_solver_failure_and_submit_cannot_bypass_it(self):
        blocked_api = build_run_api(
            self.db,
            run_id_factory=lambda: "RUN-BLOCKED",
            solver_probe=lambda: SolverProbeResult(False, "solver unavailable in test"),
        )
        report = blocked_api.validate(self.body, principal=self.u1)
        self.assertEqual(200, report.status)
        self.assertEqual("failed", report.body["status"])
        self.assertFalse(report.body["can_submit"])
        solver_check = next(x for x in report.body["checks"] if x["code"] == "solver_service")
        self.assertEqual("failed", solver_check["status"])

        submit = blocked_api.submit(self.body, principal=self.u1)
        self.assertEqual(422, submit.status)
        self.assertEqual("RUN_PREFLIGHT_FAILED", submit.body["error"]["code"])
        self.assertFalse(submit.body["error"]["validation"]["can_submit"])
        self.assertIsNone(self.runs.get("RUN-BLOCKED"))

    def test_identical_active_run_is_explicit_warning_not_hidden_deduplication(self):
        first = self.api.submit(self.body, principal=self.u1)
        self.assertEqual(201, first.status)
        report = self.api.validate(self.body, principal=self.u1)
        self.assertEqual("warning", report.body["status"])
        self.assertTrue(report.body["can_submit"])
        duplicate = next(x for x in report.body["checks"] if x["code"] == "duplicate_active_run")
        self.assertEqual("warning", duplicate["status"])
        self.assertEqual([first.body["run_id"]], duplicate["details"]["run_ids"])

    def test_failed_run_retry_clones_immutable_snapshot_instead_of_current_situation(self):
        created = self.api.submit(self.body, principal=self.u1)
        source_id = created.body["run_id"]
        self.runs.claim_running(source_id)
        self.runs.mark_failed(source_id, message="solver failed", code="SOLVER")

        snapshots = RunSnapshotRepository(self.db)
        source_snapshot = snapshots.get(source_id)
        self.assertIsNotNone(source_snapshot)

        current = self.situations.get_situation("S1")
        meta = self.situations.get_metadata("S1")
        changed = Situation(
            situation_id=current.situation_id, name="S1 changed after failure",
            description=current.description, airports=current.airports, missions=current.missions,
            damage_scenarios=current.damage_scenarios,
        )
        self.situations.save_situation(
            changed, owner_user_id="U1", expected_content_hash=meta["content_hash"]
        )

        retried = self.api.retry(source_id, principal=self.u1)
        self.assertEqual(201, retried.status)
        retry_id = retried.body["run_id"]
        self.assertEqual("queued", retried.body["status"])
        retry_snapshot = snapshots.get(retry_id)
        self.assertIsNotNone(retry_snapshot)

        source_payload = source_snapshot.to_dict()
        retry_payload = retry_snapshot.to_dict()
        self.assertEqual(source_id, source_payload.pop("run_id"))
        self.assertEqual(retry_id, retry_payload.pop("run_id"))
        self.assertEqual(source_payload, retry_payload)
        self.assertEqual("S1", retry_payload["situation"]["name"])

    def test_retry_rejects_nonfailed_run_and_hidden_body_controls(self):
        created = self.api.submit(self.body, principal=self.u1)
        run_id = created.body["run_id"]
        blocked = self.api.retry(run_id, principal=self.u1)
        self.assertEqual(409, blocked.status)
        self.assertEqual("RUN_STATE_CONFLICT", blocked.body["error"]["code"])
        self.runs.claim_running(run_id)
        self.runs.mark_failed(run_id, message="failed")
        bad_body = self.api.retry(run_id, principal=self.u1, raw_body={"use_current_situation": True})
        self.assertEqual(400, bad_body.status)
        self.assertEqual("use_current_situation", bad_body.body["error"]["field"])

    def test_cancel_body_has_no_hidden_business_controls(self):
        created = self.api.submit(self.body, principal=self.u1)
        response = self.api.cancel(
            created.body["run_id"], principal=self.u1, raw_body={"force": True}
        )
        self.assertEqual(400, response.status)
        self.assertEqual("force", response.body["error"]["field"])


    def test_viewer_is_read_only_operator_can_execute_and_missing_permission_is_403(self):
        viewer = Principal("U1", role="viewer")
        denied = self.api.validate(self.body, principal=viewer)
        self.assertEqual(403, denied.status)
        self.assertEqual("PERMISSION_DENIED", denied.body["error"]["code"])

        operator = Principal("U1", role="operator")
        created = self.api.submit(self.body, principal=operator)
        self.assertEqual(201, created.status)
        listed = self.api.list(principal=viewer)
        self.assertEqual(200, listed.status)
        self.assertEqual([created.body["run_id"]], [x["run_id"] for x in listed.body["items"]])

        no_read = Principal("U1", permissions=frozenset({"runs.execute"}))
        read_denied = self.api.list(principal=no_read)
        self.assertEqual(403, read_denied.status)
        self.assertEqual("PERMISSION_DENIED", read_denied.body["error"]["code"])

    def test_run_submission_cannot_bypass_situation_owner_scope(self):
        denied = self.api.validate({"situation_id": "S1", "run_config": self.config}, principal=self.u2)
        self.assertEqual(403, denied.status)
        self.assertEqual("FORBIDDEN", denied.body["error"]["code"])

        admin = self.api.validate({"situation_id": "S1", "run_config": self.config}, principal=self.admin)
        self.assertEqual(200, admin.status)


if __name__ == "__main__":
    unittest.main()
