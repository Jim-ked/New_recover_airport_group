from __future__ import annotations

from unittest import mock
import unittest

from backend.algorithm.runner import run_once
from backend.auth.principal import Principal
from backend.services.run_capacity_assessment import backfill_capacity
from backend.services.run_service import RunService
from backend.services.run_worker_runtime import build_run_worker_loop
from backend.storage.airport_repository import AirportRepository
from backend.web.assessment_results_api import AssessmentResultsApi
from tests.algorithm.test_runner import RunnerFakeModel, fixed_cluster_selector
from tests.algorithm.test_snapshot_adapter import make_snapshot
from tests.services.test_run_capacity_assessment import CapacityFixture


class CapacityProductionChainTests(CapacityFixture, unittest.TestCase):
    def api(self):
        return AssessmentResultsApi(
            assessment_repository=self.assessments, airport_repository=AirportRepository(self.db),
            run_repository=self.runs, snapshot_repository=self.snapshots,
        )

    def queued_worker(self, run_id="R1"):
        from backend.domain.airport import AirportBase
        snapshot = make_snapshot(run_id=run_id, include_storage_capacity=True)
        from backend.domain.run_snapshot import RunSnapshot
        from backend.storage.run_repository import content_hash
        payload = snapshot.to_dict()
        payload["situation"]["airports"][1]["operational_profile"]["aircraft_support"][0]["initial_quantity"] = 1
        digest, text = content_hash(payload)
        snapshot = RunSnapshot(run_id, snapshot.situation_id, digest, text)
        for row in snapshot.to_dict()["situation"]["airports"]:
            AirportRepository(self.db).save_airport(AirportBase.from_mapping(row["airport"]))
        RunService(snapshot_service=None, run_repository=self.runs).submit_snapshot(snapshot=snapshot, owner_user_id="U1")
        worker = build_run_worker_loop(self.db).worker
        worker.algorithm_runner = lambda snapshot, **kwargs: run_once(
            snapshot, **kwargs, cluster_selector_fn=fixed_cluster_selector, model_factory=RunnerFakeModel,
        )
        return worker

    def test_production_worker_closes_both_artifacts_to_read_apis(self):
        record = self.queued_worker().execute("R1")
        self.assertEqual("succeeded", record.status)
        self.assertIsNotNone(self.runs.get_result_payloads("R1"))
        evaluation = self.assessments.get_run_evaluation("R1")
        self.assertEqual("available", evaluation.status)
        self.assertEqual(4, len(evaluation.indicators))
        self.assertEqual(2, self.assessments.list_results()[1])
        viewer = Principal("U1")
        overview = self.api().overview(principal=viewer)
        self.assertEqual(2, overview.body["total_results"])
        self.assertEqual(["R1"], overview.body["scenario_keys"])
        curves = self.api().curves(principal=viewer, airport_id="A1")
        self.assertEqual("R1", curves.body["series"][0]["source_run_id"])
        events = {row.event for row in self.runs.list_events("R1")}
        self.assertIn("run_capacity_assessment_persisted", events)
        self.assertIn("run_indicator_assessment_persisted", events)
        self.assertNotIn("run_assessment_failed", events)

    def test_capacity_failure_preserves_success_and_indicator_evaluation(self):
        worker = self.queued_worker()
        with self.assessments.connect() as conn:
            conn.execute("CREATE TRIGGER reject_capacity BEFORE INSERT ON assessment_result BEGIN SELECT RAISE(ABORT,'unavailable'); END")
        self.assertEqual("succeeded", worker.execute("R1").status)
        self.assertEqual("succeeded", self.runs.get("R1").status)
        self.assertIsNotNone(self.runs.get_result_payloads("R1"))
        self.assertEqual("available", self.assessments.get_run_evaluation("R1").status)
        self.assertIn("run_capacity_assessment_failed", {e.event for e in self.runs.list_events("R1")})

    def test_indicator_failure_does_not_block_capacity(self):
        worker = self.queued_worker()
        with mock.patch("backend.services.run_indicator_assessment.RunIndicatorAssessmentService.evaluate", side_effect=RuntimeError("indicator unavailable")):
            self.assertEqual("succeeded", worker.execute("R1").status)
        self.assertEqual(2, self.assessments.list_results()[1])
        self.assertIn("run_indicator_assessment_failed", {e.event for e in self.runs.list_events("R1")})

    def test_derived_event_failure_never_enters_canonical_failure_transition(self):
        worker = self.queued_worker()
        append = worker.run_service.append_worker_event
        def fail_derived(*args, **kwargs):
            if kwargs.get("event", "").startswith(("run_capacity_", "run_indicator_")):
                raise RuntimeError("event store unavailable")
            return append(*args, **kwargs)
        with mock.patch.object(worker.run_service, "append_worker_event", side_effect=fail_derived):
            self.assertEqual("succeeded", worker.execute("R1").status)
        self.assertEqual("succeeded", self.runs.get("R1").status)
        self.assertEqual(2, self.assessments.list_results()[1])

    def test_backfill_dry_run_generation_and_repeat_preserve_canonical(self):
        self.succeed()
        self.succeed("R2", cluster_enabled=False)
        self.runs.create_queued(snapshot=make_snapshot(run_id="QUEUED"), owner_user_id="U1")
        before = self.canonical()
        data = self.db.read_bytes()
        dry = backfill_capacity(self.db, all_succeeded=True, dry_run=True)
        self.assertEqual(2, dry["scanned"])
        self.assertEqual(2, dry["eligible"])
        self.assertEqual(0, dry["generated_rows"])
        self.assertEqual(data, self.db.read_bytes())
        first = backfill_capacity(self.db, all_succeeded=True)
        self.assertEqual(2, first["generated_runs"])
        self.assertEqual(4, first["generated_rows"])
        saved = self.rows()
        second = backfill_capacity(self.db, all_succeeded=True)
        self.assertEqual(2, second["skipped"])
        self.assertEqual(0, second["generated_rows"])
        self.assertEqual(saved, self.rows())
        self.assertEqual(before, self.canonical())

    def test_backfill_reports_conflict_and_missing_facts_without_overwrite(self):
        self.succeed()
        self.succeed("R2")
        self.service.generate_capacity_assessment("R1")
        with self.assessments.connect() as conn:
            conn.execute("UPDATE assessment_result SET snapshot_hash=?", ("f" * 64,))
            conn.execute("DELETE FROM run_results WHERE run_id='R2'")
        saved = self.rows()
        report = backfill_capacity(self.db, all_succeeded=True)
        self.assertEqual(1, report["conflicts"])
        self.assertEqual(1, report["failed"])
        self.assertEqual(saved, self.rows())

    def test_overview_preserves_same_scenario_runs_and_curves_have_distinct_sources(self):
        self.succeed()
        self.succeed("R2", cluster_enabled=False)
        backfill_capacity(self.db, all_succeeded=True)
        api = self.api()
        body = api.overview(principal=Principal("U1")).body
        self.assertEqual({"R1", "R2"}, set(body["scenario_keys"]))
        self.assertEqual({"R1", "R2"}, set(body["run_cells"]["A1"]))
        self.assertIn("组群", body["scenario_labels"]["R1"])
        self.assertNotEqual(body["scenario_labels"]["R1"], body["scenario_labels"]["R2"])
        series = api.curves(principal=Principal("U1"), airport_id="A1").body["series"]
        self.assertEqual({"R1", "R2"}, {row["source_run_id"] for row in series})
        self.assertTrue(all(row["scenario_label"] for row in series))

    def test_overview_same_damage_id_different_cluster_runs_keep_frozen_labels(self):
        from backend.domain.damage import DamageScenario
        # No-damage events still form a formally selected named scenario.
        scenario = DamageScenario(damage_scenario_id="DS-SAME", name="Frozen damage", category="medium", events=())
        from backend.domain.airport import AirportBase
        from backend.services.run_worker import RunWorker
        from backend.services.run_result_service import RunResultService
        for run_id, cluster in (("R1", False), ("R2", True)):
            snapshot = make_snapshot(run_id=run_id, scenario=scenario, cluster_enabled=cluster)
            for row in snapshot.to_dict()["situation"]["airports"]:
                AirportRepository(self.db).save_airport(AirportBase.from_mapping(row["airport"]))
            service = RunService(snapshot_service=None, run_repository=self.runs)
            service.submit_snapshot(snapshot=snapshot, owner_user_id="U1")
            RunWorker(run_service=service, snapshot_repository=self.snapshots,
                      result_service=RunResultService(run_repository=self.runs, snapshot_repository=self.snapshots)).execute(
                          run_id, cluster_selector_fn=fixed_cluster_selector, model_factory=RunnerFakeModel)
        backfill_capacity(self.db, all_succeeded=True)
        with self.assessments.connect() as conn:
            conn.execute("INSERT INTO situations(situation_id,name) VALUES('S1','Renamed mutable situation')")
        body = self.api().overview(principal=Principal("U1")).body
        self.assertEqual(["DS-SAME"], body["scenario_types"])
        self.assertEqual({"R1", "R2"}, set(body["run_cells"]["A1"]))
        self.assertTrue(all("Frozen damage" in label for label in body["scenario_labels"].values()))
        self.assertTrue(all("Renamed" not in label for label in body["scenario_labels"].values()))

    def test_backfill_explicit_ids_do_not_scan_non_succeeded_or_duplicate_runs(self):
        self.succeed()
        self.runs.create_queued(snapshot=make_snapshot(run_id="QUEUED"), owner_user_id="U1")
        result = backfill_capacity(self.db, run_ids=["R1", "R1", "QUEUED", "missing"])
        self.assertEqual(1, result["scanned"])
        self.assertEqual(2, len(result["not_succeeded"]))
        self.assertEqual(1, result["generated_runs"])
