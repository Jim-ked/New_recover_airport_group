from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.domain.airport import AirportBase
from backend.domain.catalog import AircraftType, ResourceType
from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.domain.situation import Situation
from backend.storage.airport_repository import AirportRepository
from backend.storage.mission_repository import MissionRepository
from backend.storage.run_repository import RunConflictError, RunRepository, RunTransitionError
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository
from tests.algorithm.test_snapshot_adapter import make_snapshot


class RunRepositoryTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "app.sqlite"
        self.repo = RunRepository(self.db)
        self.repo.init_schema()
        self.snapshots = RunSnapshotRepository(self.db)

    def tearDown(self):
        self._td.cleanup()

    def test_create_queued_atomically_persists_snapshot_and_owner_record(self):
        snapshot = make_snapshot(run_id="R1")
        record = self.repo.create_queued(snapshot=snapshot, owner_user_id="U1")
        self.assertEqual("queued", record.status)
        self.assertEqual(snapshot.content_hash, record.snapshot_hash)
        loaded = self.snapshots.get("R1")
        self.assertIsNotNone(loaded)
        self.assertEqual(snapshot.payload_json, loaded.payload_json)

    def test_duplicate_run_is_rejected_without_overwrite(self):
        first = make_snapshot(run_id="R1")
        self.repo.create_queued(snapshot=first, owner_user_id="U1")
        with self.assertRaises(RunConflictError):
            self.repo.create_queued(snapshot=make_snapshot(run_id="R1"), owner_user_id="U2")
        record = self.repo.get("R1")
        self.assertEqual("U1", record.owner_user_id)

    def test_search_history_accepts_optional_owner_scope_without_duplicating_filters(self):
        self.repo.create_queued(snapshot=make_snapshot(run_id="OWN-1"), owner_user_id="U1")
        self.repo.create_queued(snapshot=make_snapshot(run_id="FOREIGN-1"), owner_user_id="U2")

        owned, owned_total = self.repo.search_history(
            owner_user_id="U1", run_id_query="1", limit=10, offset=0,
        )
        global_rows, global_total = self.repo.search_history(
            owner_user_id=None, run_id_query="1", limit=10, offset=0,
        )

        self.assertEqual(1, owned_total)
        self.assertEqual(["OWN-1"], [row.run_id for row in owned])
        self.assertEqual(2, global_total)
        self.assertEqual({"OWN-1", "FOREIGN-1"}, {row.run_id for row in global_rows})

    def test_cancel_has_no_external_cancelling_status(self):
        self.repo.create_queued(snapshot=make_snapshot(run_id="R1"), owner_user_id="U1")
        queued_cancel = self.repo.request_cancel("R1")
        self.assertEqual("cancelled", queued_cancel.status)
        self.assertTrue(queued_cancel.cancel_requested)

        self.repo.create_queued(snapshot=make_snapshot(run_id="R2"), owner_user_id="U1")
        self.repo.claim_running("R2")
        running_cancel = self.repo.request_cancel("R2")
        self.assertEqual("running", running_cancel.status)
        self.assertTrue(running_cancel.cancel_requested)
        with self.assertRaisesRegex(RunTransitionError, "cancel-requested"):
            self.repo.save_success("R2", solution={"run_id": "R2"}, metrics={"run_id": "R2"})
        self.assertEqual("cancelled", self.repo.mark_cancelled("R2").status)

    def test_events_are_monotonic_and_incrementally_readable(self):
        self.repo.create_queued(snapshot=make_snapshot(run_id="R1"), owner_user_id="U1")
        e1 = self.repo.append_event(
            "R1", level="INFO", stage="data_preparation", event="stage_start",
            message="prepare", payload={"x": 1},
        )
        e2 = self.repo.append_event(
            "R1", level="INFO", stage="data_preparation", event="stage_end",
            message="prepared", payload={},
        )
        self.assertEqual((1, 2), (e1.seq, e2.seq))
        rows = self.repo.list_events("R1", after_seq=1)
        self.assertEqual([2], [x.seq for x in rows])
        self.assertEqual("prepared", rows[0].message)

    def test_success_result_is_insert_only_and_terminal(self):
        self.repo.create_queued(snapshot=make_snapshot(run_id="R1"), owner_user_id="U1")
        self.repo.claim_running("R1")
        record = self.repo.save_success(
            "R1",
            solution={"run_id": "R1", "selected_cluster": [], "sortie_chains": [{"path_id": "P"}]},
            metrics={"schema_version": "metrics.v1", "run_id": "R1"},
        )
        self.assertEqual("succeeded", record.status)
        self.assertIsNotNone(record.solution_hash)
        self.assertIsNotNone(record.metrics_hash)
        payloads = self.repo.get_result_payloads("R1")
        self.assertEqual("R1", payloads[0]["run_id"])
        with self.assertRaises(RunTransitionError):
            self.repo.save_success("R1", solution={"run_id": "R1"}, metrics={"run_id": "R1"})
        with self.assertRaises(RunTransitionError):
            self.repo.mark_failed("R1", message="too late")

    def _create_succeeded(self, run_id: str):
        self.repo.create_queued(snapshot=make_snapshot(run_id=run_id), owner_user_id="U1")
        self.repo.claim_running(run_id)
        return self.repo.save_success(
            run_id,
            solution={"run_id": run_id, "selected_cluster": [], "sortie_chains": []},
            metrics={"schema_version": "metrics.v1", "run_id": run_id},
        )

    def test_delete_terminal_removes_each_terminal_run_closure_and_preserves_shared_data(self):
        airports = AirportRepository(self.db)
        airports.save_airport(AirportBase.from_mapping({
            "airport_id": "A1", "airport_name": "Shared Airport",
            "facility_type": "medium_airport", "role": "joint",
            "longitude": 118.8, "latitude": 31.7, "scheduled_service": True,
            "runway_count": 0, "max_runway_length_m": None, "runways": [],
        }))
        airports.save_aircraft_type(AircraftType.from_mapping({
            "aircraft_type_id": "fighter", "name": "Shared Aircraft Type",
        }))
        airports.save_resource_type(ResourceType.from_mapping({
            "resource_type_id": "fuel", "name": "Shared Fuel",
            "category": "fuel", "unit": "t",
        }))
        missions = MissionRepository(self.db)
        missions.save(Mission(
            mission_id="M1", name="Shared Mission", longitude=120.0, latitude=32.0,
            window_start_slot=1, window_end_slot=3,
            aircraft_requirements=(MissionAircraftRequirement("fighter", 1, 1),),
        ))
        situations = SituationRepository(self.db)
        situations.save_situation(
            Situation.create(situation_id="S1", name="Shared Situation"),
            owner_user_id="U1",
        )

        self._create_succeeded("R-OTHER")
        self.repo.append_event(
            "R-OTHER", level="INFO", stage="persistence", event="run_succeeded",
            message="other run remains", payload={},
        )

        for run_id, terminal_status in (
            ("R-SUCCEEDED", "succeeded"),
            ("R-FAILED", "failed"),
            ("R-CANCELLED", "cancelled"),
        ):
            with self.subTest(status=terminal_status):
                if terminal_status == "succeeded":
                    record = self._create_succeeded(run_id)
                else:
                    self.repo.create_queued(
                        snapshot=make_snapshot(run_id=run_id), owner_user_id="U1"
                    )
                    if terminal_status == "failed":
                        self.repo.claim_running(run_id)
                        record = self.repo.mark_failed(run_id, message="expected failure")
                    else:
                        record = self.repo.request_cancel(run_id)
                self.repo.append_event(
                    run_id, level="INFO", stage="persistence",
                    event=f"run_{terminal_status}", message=terminal_status, payload={},
                )
                with self.repo.connect() as conn:
                    conn.execute(
                        "INSERT INTO assessment_result "
                        "(id,airport_id,scenario_type,curves,decline_rate,recovery_slope,source_run_id) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (f"AR-{run_id}", "A1", "run", "[]", 0, 0, run_id),
                    )
                    conn.execute(
                        """INSERT INTO assessment_run_evaluation
                           (run_id,snapshot_hash,metrics_hash,indicator_set_id,indicator_set_version,
                            status,calculation_version,weight_source,weight_trace_json,support_index,
                            overall_membership_json,weighted_composite,risk_score,risk_level,risk_status)
                           VALUES (?,?,?,?,?,'available','test.v1','ahp','{}',0.5,'{}',0.5,0.5,3,'available')""",
                        (
                            run_id, record.snapshot_hash, record.metrics_hash or "0" * 64,
                            "INDSET-RUN-ASSESSMENT-V1", "run-assessment.v1",
                        ),
                    )
                    conn.execute(
                        """INSERT INTO assessment_indicator_value
                           (run_id,indicator_id,code,name,raw_value,normalized_value,weight,
                            weight_source,calculation_type,evidence_json)
                           VALUES (?,?,?,?,0.5,0.5,0.25,'ahp','direct','{}')""",
                        (
                            run_id,
                            "IND:run_assessment:formal_metrics:task_completion_rate",
                            "task_completion_rate", "任务履行率",
                        ),
                    )

                self.assertTrue(self.repo.delete_terminal(run_id))

                with self.repo.connect() as conn:
                    for table in (
                        "runs", "run_events", "run_results", "run_input_snapshots",
                        "assessment_result", "assessment_run_evaluation",
                        "assessment_indicator_value",
                    ):
                        column = "source_run_id" if table == "assessment_result" else "run_id"
                        count = conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (run_id,)
                        ).fetchone()[0]
                        self.assertEqual(0, count, table)

        self.assertEqual("Shared Situation", situations.get_situation("S1").name)
        self.assertEqual("Shared Airport", airports.get_airport("A1").airport_name)
        self.assertEqual("Shared Aircraft Type", airports.get_aircraft_type("fighter").name)
        self.assertEqual("Shared Fuel", airports.list_resource_types()[0].name)
        self.assertEqual("Shared Mission", missions.get("M1").name)
        self.assertEqual("succeeded", self.repo.get("R-OTHER").status)
        self.assertEqual(1, len(self.repo.list_events("R-OTHER")))
        self.assertIsNotNone(self.repo.get_result_payloads("R-OTHER"))

    def test_delete_terminal_rejects_queued_and_running_runs(self):
        self.repo.create_queued(snapshot=make_snapshot(run_id="RQ"), owner_user_id="U1")
        self.repo.create_queued(snapshot=make_snapshot(run_id="RR"), owner_user_id="U1")
        self.repo.claim_running("RR")

        for run_id in ("RQ", "RR"):
            with self.subTest(run_id=run_id):
                with self.assertRaisesRegex(RunTransitionError, "active Run"):
                    self.repo.delete_terminal(run_id)
                self.assertIsNotNone(self.repo.get(run_id))

    def test_delete_terminal_rolls_back_if_any_dependency_delete_fails(self):
        self.repo.create_queued(snapshot=make_snapshot(run_id="R1"), owner_user_id="U1")
        self.repo.request_cancel("R1")
        self.repo.append_event(
            "R1", level="INFO", stage="persistence", event="run_cancelled",
            message="cancelled", payload={},
        )
        with self.repo.connect() as conn:
            conn.execute(
                "CREATE TRIGGER fail_run_delete BEFORE DELETE ON runs "
                "BEGIN SELECT RAISE(ABORT, 'forced delete failure'); END"
            )

        with self.assertRaisesRegex(sqlite3.DatabaseError, "forced delete failure"):
            self.repo.delete_terminal("R1")

        with self.repo.connect() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM runs WHERE run_id='R1'").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id='R1'").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM run_input_snapshots WHERE run_id='R1'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
