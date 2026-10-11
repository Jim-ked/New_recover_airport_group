from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.services.run_capacity_assessment import RunCapacityAssessmentService
from backend.services.run_result_service import RunResultService
from backend.services.run_service import RunService
from backend.services.run_worker import RunWorker
from backend.storage.airport_repository import AirportRepository
from backend.storage.assessment_repository import AssessmentRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from tests.algorithm.test_runner import RunnerFakeModel, fixed_cluster_selector
from tests.algorithm.test_snapshot_adapter import make_snapshot


class CapacityFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "app.sqlite3"
        self.runs = RunRepository(self.db)
        self.runs.init_schema()
        self.snapshots = RunSnapshotRepository(self.db)
        self.assessments = AssessmentRepository(self.db)
        self.service = RunCapacityAssessmentService(
            run_repository=self.runs, snapshot_repository=self.snapshots,
            assessment_repository=self.assessments,
        )

    def succeed(self, run_id="R1", cluster_enabled=True):
        snapshot = make_snapshot(run_id=run_id, cluster_enabled=cluster_enabled, include_storage_capacity=True)
        from backend.domain.airport import AirportBase
        airports = AirportRepository(self.db)
        for row in snapshot.to_dict()["situation"]["airports"]:
            airports.save_airport(AirportBase.from_mapping(row["airport"]))
        run_service = RunService(snapshot_service=None, run_repository=self.runs)
        run_service.submit_snapshot(snapshot=snapshot, owner_user_id="U1")
        worker = RunWorker(
            run_service=run_service,
            result_service=RunResultService(run_repository=self.runs, snapshot_repository=self.snapshots),
            snapshot_repository=self.snapshots,
        )
        worker.execute(run_id, cluster_selector_fn=fixed_cluster_selector, model_factory=RunnerFakeModel)

    def rows(self):
        with self.assessments.connect() as conn:
            return conn.execute("SELECT * FROM assessment_result ORDER BY id").fetchall()

    def canonical(self):
        with self.assessments.connect() as conn:
            return {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in ("runs", "run_input_snapshots", "run_results")}


class CapacityAssessmentTests(CapacityFixture, unittest.TestCase):

    def test_generation_is_traceable_and_repeated_execution_does_not_write(self):
        self.succeed()
        before = self.canonical()
        result = self.service.generate_capacity_assessment("R1")
        self.assertEqual("generated", result["status"])
        self.assertEqual(2, result["result_count"])
        records, total = self.assessments.list_results()
        self.assertEqual(2, total)
        self.assertTrue(all(row.id.startswith("capacity:R1:") for row in records))
        self.assertTrue(all(row.metrics_hash == self.runs.get("R1").metrics_hash for row in records))
        saved = self.rows()
        with self.assessments.connect() as conn:
            conn.execute("CREATE TRIGGER no_capacity_update BEFORE UPDATE ON assessment_result BEGIN SELECT RAISE(ABORT,'no update'); END")
        self.assertEqual("skipped", self.service.generate_capacity_assessment("R1")["status"])
        self.assertEqual(saved, self.rows())
        self.assertEqual(before, self.canonical())

    def test_dry_run_has_no_business_or_database_byte_changes(self):
        self.succeed()
        before = self.db.read_bytes()
        result = self.service.generate_capacity_assessment("R1", dry_run=True)
        self.assertEqual("eligible", result["status"])
        self.assertEqual(2, result["result_count"])
        self.assertEqual([], self.rows())
        self.assertEqual(before, self.db.read_bytes())

    def test_corrupt_metrics_are_rejected_before_writing(self):
        self.succeed()
        with self.assessments.connect() as conn:
            conn.execute("UPDATE run_results SET metrics_json=json_set(metrics_json,'$.summary.completion_ratio',0.123)")
        result = self.service.generate_capacity_assessment("R1")
        self.assertEqual("unavailable", result["status"])
        self.assertIn("hash", result["reason"])
        self.assertEqual([], self.rows())

    def test_snapshot_hash_drift_is_rejected(self):
        self.succeed()
        with self.assessments.connect() as conn:
            conn.execute("UPDATE runs SET snapshot_hash=?", ("f" * 64,))
        self.assertEqual("unavailable", self.service.generate_capacity_assessment("R1")["status"])
        self.assertEqual([], self.rows())

    def test_solution_and_metrics_run_id_drift_is_rejected(self):
        from backend.storage.run_repository import content_hash
        import json
        self.succeed()
        for field in ("solution", "metrics"):
            with self.assessments.connect() as conn:
                raw = conn.execute(f"SELECT {field}_json FROM run_results").fetchone()[0]
                changed = json.loads(raw)
                changed["run_id"] = "OTHER"
                digest, payload = content_hash(changed)
                conn.execute(f"UPDATE run_results SET {field}_json=?,{field}_hash=?", (payload, digest))
            self.assertEqual("unavailable", self.service.generate_capacity_assessment("R1")["status"])
            self.assertEqual([], self.rows())
            # Restore before checking the other payload's independently wrong run_id.
            with self.assessments.connect() as conn:
                conn.execute(f"UPDATE run_results SET {field}_json=?,{field}_hash=?",
                             (raw, content_hash(json.loads(raw))[0]))

    def test_corrupt_snapshot_content_is_rejected(self):
        self.succeed()
        with self.assessments.connect() as conn:
            conn.execute("UPDATE run_input_snapshots SET payload_json=json_set(payload_json,'$.situation.name','changed')")
        self.assertEqual("unavailable", self.service.generate_capacity_assessment("R1")["status"])
        self.assertEqual([], self.rows())

    def test_same_id_with_another_source_run_is_a_conflict(self):
        self.succeed()
        self.service.generate_capacity_assessment("R1")
        with self.assessments.connect() as conn:
            conn.execute("UPDATE assessment_result SET source_run_id='OTHER'")
        saved = self.rows()
        self.assertEqual("conflict", self.service.generate_capacity_assessment("R1")["status"])
        self.assertEqual(saved, self.rows())

    def test_experiment_prefix_is_explicit_and_repeatable(self):
        from backend.services.run_assessment_projection import project_run_capacity_assessment
        self.succeed()
        args = dict(run_id="R1", scenario_label="R0", result_id_prefix="v5cap",
                    run_repository=self.runs, snapshot_repository=self.snapshots,
                    assessment_repository=self.assessments)
        first = project_run_capacity_assessment(**args)
        self.assertTrue(all(row.id.startswith("v5cap:R1:") for row in first))
        self.assertEqual(first, project_run_capacity_assessment(**args))

    def test_missing_or_non_succeeded_run_is_unavailable(self):
        self.assertEqual("unavailable", self.service.generate_capacity_assessment("missing")["status"])
        self.runs.create_queued(snapshot=make_snapshot(), owner_user_id="U1")
        self.assertEqual("unavailable", self.service.generate_capacity_assessment("R1")["status"])

    def test_batch_storage_failure_rolls_back_all_airports(self):
        self.succeed()
        with self.assessments.connect() as conn:
            conn.execute("CREATE TRIGGER fail_second_airport BEFORE INSERT ON assessment_result WHEN NEW.airport_id='A2' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        before = self.canonical()
        self.assertEqual("failed", self.service.generate_capacity_assessment("R1")["status"])
        self.assertEqual([], self.rows())
        self.assertEqual(before, self.canonical())

    def test_provenance_conflict_refuses_overwrite_and_partial_insert(self):
        self.succeed()
        self.service.generate_capacity_assessment("R1")
        with self.assessments.connect() as conn:
            conn.execute("DELETE FROM assessment_result WHERE airport_id='A2'")
            conn.execute("UPDATE assessment_result SET metrics_hash=?", ("f" * 64,))
        saved = self.rows()
        self.assertEqual("conflict", self.service.generate_capacity_assessment("R1")["status"])
        self.assertEqual(saved, self.rows())

    def test_computed_fact_conflict_refuses_overwrite(self):
        self.succeed()
        self.service.generate_capacity_assessment("R1")
        with self.assessments.connect() as conn:
            conn.execute("UPDATE assessment_result SET matrix_value=0.987")
        saved = self.rows()
        self.assertEqual("conflict", self.service.generate_capacity_assessment("R1")["status"])
        self.assertEqual(saved, self.rows())

    def test_partial_identical_results_can_be_completed_atomically(self):
        self.succeed()
        self.service.generate_capacity_assessment("R1")
        with self.assessments.connect() as conn:
            conn.execute("DELETE FROM assessment_result WHERE airport_id='A2'")
        first = self.rows()[0]
        result = self.service.generate_capacity_assessment("R1")
        self.assertEqual("generated", result["status"])
        self.assertEqual(1, result["generated"])
        self.assertEqual(first, self.rows()[0])
