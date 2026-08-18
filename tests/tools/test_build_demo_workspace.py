from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.storage.airport_repository import AirportRepository
from backend.storage.run_repository import RunRepository, RunRepositoryError
from tests.algorithm.test_snapshot_adapter import make_snapshot
from tools.dev_validation.build_demo_workspace import (
    MISSIONS,
    ApiFailure,
    DEFAULT_DB_PATH,
    DEMO_DESCRIPTION,
    DEMO_SITUATION_NAME,
    DemoCollisionError,
    WorkspaceInspection,
    _require_comparison_payload,
    assert_run_integrity,
    find_demo_situation,
    identify_standard_run_roles,
    inspect_workspace,
    main,
    parse_args,
    prepare_missions,
    require_demo_apply_safe,
    result_row,
    run_config,
    run_matrix,
)


class _MissionApi:
    user_id = "U1"

    def __init__(self, missions=()):
        self.missions = {
            row["mission_id"]: deepcopy(row)
            for row in missions
        }
        self.revisions = {mission_id: 1 for mission_id in self.missions}
        self.actions: list[tuple[str, str]] = []

    def request(self, method, path, *, body=None, expected=(200,)):
        mission_id = path.rsplit("/", 1)[-1]
        if method == "GET":
            if mission_id not in self.missions:
                raise ApiFailure(method, path, 404, {"error": "not found"})
            return {
                "mission": deepcopy(self.missions[mission_id]),
                "metadata": {"revision": self.revisions[mission_id]},
            }
        if method == "POST" and path == "/api/missions":
            mission = deepcopy(body["mission"])
            mission_id = mission["mission_id"]
            self.missions[mission_id] = mission
            self.revisions[mission_id] = 1
            self.actions.append(("create", mission_id))
            return {"mission": mission, "metadata": {"revision": 1}}
        if method == "PUT":
            mission = deepcopy(body["mission"])
            self.missions[mission_id] = mission
            self.revisions[mission_id] += 1
            self.actions.append(("update", mission_id))
            return {
                "mission": mission,
                "metadata": {"revision": self.revisions[mission_id]},
            }
        raise AssertionError(f"unexpected request: {method} {path}")


def _demo_detail(missions, *, owner="U1", marked=True):
    return {
        "owner_user_id": owner,
        "situation": {
            "situation_id": "ST002",
            "name": DEMO_SITUATION_NAME,
            "description": DEMO_DESCRIPTION if marked else "普通用户情境",
            "missions": deepcopy(list(missions)),
        },
    }


class DemoWorkspaceToolTests(unittest.TestCase):
    @staticmethod
    def _standard_run_rows():
        rows = []
        for label, damage_id, cluster_enabled in run_matrix():
            run_id = f"RUN-{label}"
            rows.append({
                "run_id": run_id,
                "status": "succeeded",
                "situation_id": "ST002",
                "owner_user_id": "U1",
                "snapshot": {
                    "run_id": run_id,
                    "situation": {"situation_id": "ST002"},
                    "run_config": run_config(
                        damage_scenario_id=damage_id,
                        cluster_enabled=cluster_enabled,
                        core_airport_id="AP179",
                    ),
                },
            })
        return rows

    @staticmethod
    def _comparison_payload(**extra):
        payload = {
            "run_summaries": {"RUN-A": {"scheduled_sorties_total": 1}},
            "timeline": {"windows": [0]},
            "airports": {"AP001": {}},
            "tasks": {"M001": {}},
            "aircraft": {"fighter": {}},
            "resources": {"category_min_remaining_ratio": {}},
            "scheme": {"RUN-A": {}},
            "labels": {"airports": {"AP001": "Airport"}},
        }
        payload.update(extra)
        return payload

    @staticmethod
    def _integrity_payloads(
        *, run_id: str = "RUN-demo", situation_id: str = "ST002"
    ) -> dict[str, object]:
        return {
            "label": "R0",
            "run_id": run_id,
            "situation_id": situation_id,
            "detail": {"run_id": run_id, "status": "succeeded"},
            "snapshot": {
                "run_id": run_id,
                "situation": {"situation_id": situation_id},
            },
            "solution": {"run_id": run_id, "sortie_chains": []},
            "metrics": {
                "run_id": run_id,
                "summary": {
                    "scheduled_sorties_total": 0,
                    "returned_sorties_total": 0,
                },
                "airports": {},
                "tasks": {},
                "aircraft": {},
            },
            "runtime": {"run_id": run_id, "frames": []},
            "events": {"events": [{"event": "run_succeeded"}]},
        }

    def test_run_integrity_accepts_nested_snapshot_situation_id(self) -> None:
        assert_run_integrity(**self._integrity_payloads())

    def test_run_integrity_rejects_snapshot_run_id_mismatch(self) -> None:
        payloads = self._integrity_payloads()
        payloads["snapshot"] = {
            "run_id": "RUN-wrong",
            "situation": {"situation_id": "ST002"},
        }
        with self.assertRaises(RuntimeError) as raised:
            assert_run_integrity(**payloads)
        self.assertIn("expected='RUN-demo', actual='RUN-wrong'", str(raised.exception))

    def test_run_integrity_rejects_nested_snapshot_situation_id_mismatch(self) -> None:
        payloads = self._integrity_payloads()
        payloads["snapshot"] = {
            "run_id": "RUN-demo",
            "situation": {"situation_id": "ST-wrong"},
        }
        with self.assertRaises(RuntimeError) as raised:
            assert_run_integrity(**payloads)
        self.assertIn("expected='ST002', actual='ST-wrong'", str(raised.exception))

    def test_existing_run_roles_come_from_frozen_config_not_row_order(self) -> None:
        rows = list(reversed(self._standard_run_rows()))

        roles = identify_standard_run_roles(
            rows,
            situation_id="ST002",
            owner_user_id="U1",
            core_airport_id="AP179",
        )

        self.assertEqual(
            [label for label, _damage, _cluster in run_matrix()],
            list(roles),
        )
        self.assertEqual("RUN-R0", roles["R0"])
        self.assertEqual("RUN-LOW-R2", roles["LOW-R2"])

    def test_existing_run_roles_reject_duplicate_role(self) -> None:
        rows = self._standard_run_rows()
        rows[-1]["snapshot"]["run_config"] = deepcopy(rows[-2]["snapshot"]["run_config"])

        with self.assertRaisesRegex(RuntimeError, "role HIGH-R1 is duplicated"):
            identify_standard_run_roles(
                rows,
                situation_id="ST002",
                owner_user_id="U1",
                core_airport_id="AP179",
            )

    def test_existing_run_roles_require_exactly_seven_canonical_configs(self) -> None:
        rows = self._standard_run_rows()[:-1]
        with self.assertRaisesRegex(RuntimeError, "requires exactly 7 Runs; found 6"):
            identify_standard_run_roles(
                rows,
                situation_id="ST002",
                owner_user_id="U1",
                core_airport_id="AP179",
            )

        rows = self._standard_run_rows()
        rows[1]["snapshot"]["run_config"]["algorithm_seed"] = 99
        with self.assertRaisesRegex(RuntimeError, "run_config mismatch"):
            identify_standard_run_roles(
                rows,
                situation_id="ST002",
                owner_user_id="U1",
                core_airport_id="AP179",
            )

    def test_existing_run_roles_reject_non_succeeded_or_foreign_run(self) -> None:
        rows = self._standard_run_rows()
        rows[0]["status"] = "failed"
        with self.assertRaisesRegex(RuntimeError, "is not succeeded"):
            identify_standard_run_roles(
                rows,
                situation_id="ST002",
                owner_user_id="U1",
                core_airport_id="AP179",
            )

        rows = self._standard_run_rows()
        rows[0]["owner_user_id"] = "OTHER"
        with self.assertRaisesRegex(RuntimeError, "another owner"):
            identify_standard_run_roles(
                rows,
                situation_id="ST002",
                owner_user_id="U1",
                core_airport_id="AP179",
            )

    def test_comparison_payload_contract_is_mode_specific(self) -> None:
        configuration = self._comparison_payload(
            baseline_run_id="RUN-A",
            configurations={"RUN-A": {}},
            summary_deltas_vs_baseline={"RUN-A": {}},
        )
        _require_comparison_payload(
            "LOW configuration", configuration, mode="configuration"
        )
        self.assertNotIn("difference_overview", configuration)

        damage = self._comparison_payload(difference_overview={"peak_sorties": {}})
        _require_comparison_payload("LOW damage", damage, mode="damage")
        _require_comparison_payload("multi", damage, mode="multi_scenario")

    def test_comparison_payload_rejects_missing_mode_specific_delta_block(self) -> None:
        configuration = self._comparison_payload(
            baseline_run_id="RUN-A",
            configurations={"RUN-A": {}},
        )
        with self.assertRaisesRegex(RuntimeError, "summary_deltas_vs_baseline"):
            _require_comparison_payload(
                "LOW configuration", configuration, mode="configuration"
            )

        with self.assertRaisesRegex(RuntimeError, "difference_overview"):
            _require_comparison_payload(
                "LOW damage", self._comparison_payload(), mode="damage"
            )

    def test_result_summary_reads_canonical_resource_ratio_detail(self) -> None:
        row = result_row({
            "metrics": {
                "summary": {},
                "collaboration": {},
                "resources": {
                    "category_min_remaining_ratio": {
                        "fuel": {"ratio": 0.625},
                    },
                },
            },
        })
        self.assertEqual(0.625, row["fuel"])
        self.assertIsNone(row["material"])

    def test_prepare_missions_creates_missing_standard_templates(self) -> None:
        api = _MissionApi()
        prepare_missions(api, rebuild=False, demo_detail=None)
        self.assertEqual(
            {row["mission_id"] for row in MISSIONS},
            set(api.missions),
        )
        self.assertEqual(3, len(api.actions))
        self.assertTrue(all(action == "create" for action, _mission_id in api.actions))

    def test_rebuild_updates_old_mission_proven_by_demo_snapshot(self) -> None:
        old = deepcopy(MISSIONS[0])
        old["longitude"] = 121.25
        existing = [old, deepcopy(MISSIONS[1]), deepcopy(MISSIONS[2])]
        api = _MissionApi(existing)

        prepare_missions(api, rebuild=True, demo_detail=_demo_detail(existing))

        self.assertEqual([("update", "M001")], api.actions)
        self.assertEqual(MISSIONS[0], api.missions["M001"])

    def test_current_demo_missions_are_semantically_reused(self) -> None:
        existing = deepcopy(list(MISSIONS))
        for mission in existing:
            mission["aircraft_requirements"].reverse()
        api = _MissionApi(existing)

        prepare_missions(api, rebuild=True, demo_detail=_demo_detail(existing))

        self.assertEqual([], api.actions)

    def test_same_id_ordinary_user_mission_is_not_overwritten(self) -> None:
        ordinary = deepcopy(MISSIONS[0])
        ordinary["name"] = "用户自定义任务"
        api = _MissionApi([ordinary])

        with self.assertRaisesRegex(RuntimeError, "mission M001 collision"):
            prepare_missions(api, rebuild=True, demo_detail=None)

        self.assertEqual([], api.actions)

    def test_legacy_mission_migrates_only_during_rebuild(self) -> None:
        old = deepcopy(MISSIONS[0])
        old["window_start_slot"] = 14
        existing = [old, deepcopy(MISSIONS[1]), deepcopy(MISSIONS[2])]
        detail = _demo_detail(existing)
        api = _MissionApi(existing)

        with self.assertRaisesRegex(RuntimeError, "mission M001 collision"):
            prepare_missions(api, rebuild=False, demo_detail=detail)
        self.assertEqual([], api.actions)

        prepare_missions(api, rebuild=True, demo_detail=detail)
        self.assertEqual([("update", "M001")], api.actions)

    def test_unproven_same_id_mission_reports_compact_collision_diff(self) -> None:
        existing = deepcopy(MISSIONS[0])
        existing["name"] = "无法证明来源的任务"
        unrelated_snapshot = deepcopy(MISSIONS[0])
        api = _MissionApi([existing])

        with self.assertRaises(RuntimeError) as raised:
            prepare_missions(
                api,
                rebuild=True,
                demo_detail=_demo_detail([unrelated_snapshot]),
            )

        message = str(raised.exception)
        self.assertIn("mission M001 collision:", message)
        self.assertIn("existing =", message)
        self.assertIn("desired  =", message)
        self.assertEqual([], api.actions)

    def test_no_apply_flag_is_a_read_only_rebuild_plan(self) -> None:
        args = parse_args([])
        self.assertFalse(args.apply_default_db)
        self.assertEqual("rebuild", args.mode)

    def test_verify_existing_is_an_explicit_read_only_mode(self) -> None:
        args = parse_args(["--verify-existing"])
        self.assertFalse(args.apply_default_db)
        self.assertEqual("verify", args.mode)

    @patch("tools.dev_validation.build_demo_workspace.build_application")
    @patch("tools.dev_validation.build_demo_workspace.backup_default_database")
    @patch("tools.dev_validation.build_demo_workspace.execute_run_batch")
    @patch("tools.dev_validation.build_demo_workspace.verify_existing_workspace")
    @patch("tools.dev_validation.build_demo_workspace.print_plan")
    @patch("tools.dev_validation.build_demo_workspace.inspect_workspace")
    @patch("tools.dev_validation.build_demo_workspace.validate_static_definition")
    @patch("tools.dev_validation.build_demo_workspace.resolve_airport_ids")
    @patch("tools.dev_validation.build_demo_workspace.AppSettings.from_environment")
    def test_verify_existing_returns_before_backup_application_or_solver_capable_flow(
        self,
        settings_from_environment,
        resolve_airport_ids,
        _validate_static_definition,
        inspect_workspace_mock,
        _print_plan,
        verify_existing_workspace_mock,
        execute_run_batch,
        backup_default_database,
        build_application,
    ) -> None:
        settings_from_environment.return_value = SimpleNamespace(db_path=DEFAULT_DB_PATH)
        resolve_airport_ids.return_value = {"nanjing": "AP179"}
        inspection = WorkspaceInspection(
            database_path=DEFAULT_DB_PATH,
            airport_count=6,
            situation_count=2,
            run_count=7,
            demo_situation_count=1,
            demo_situation_id="ST002",
            demo_owner_user_id="U1",
            demo_run_count=7,
            demo_collision_count=0,
            demo_collision_ids=(),
        )
        inspect_workspace_mock.return_value = inspection

        self.assertEqual(0, main(["--verify-existing"]))

        verify_existing_workspace_mock.assert_called_once_with(
            DEFAULT_DB_PATH,
            inspection=inspection,
            core_airport_id="AP179",
        )
        backup_default_database.assert_not_called()
        build_application.assert_not_called()
        execute_run_batch.assert_not_called()

    def test_dry_run_inspection_does_not_modify_database(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "airport_group.sqlite3"
            AirportRepository(db_path).init_schema()
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()

            plan = inspect_workspace(db_path)

            after = hashlib.sha256(db_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(0, plan.demo_situation_count)
        self.assertEqual(0, plan.demo_run_count)

    def test_existing_demo_situation_is_reused_by_name_and_marker(self) -> None:
        rows = [
            {"situation_id": "ST001", "name": "普通情境"},
            {
                "situation_id": "ST002",
                "name": DEMO_SITUATION_NAME,
                "description": DEMO_DESCRIPTION,
            },
        ]
        self.assertEqual("ST002", find_demo_situation(rows)["situation_id"])
        self.assertIsNone(find_demo_situation(rows[:1]))

    def test_inspection_reuses_an_existing_demo_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "airport_group.sqlite3"
            AirportRepository(db_path).init_schema()
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    "INSERT INTO situations "
                    "(situation_id,name,description,content_hash,owner_user_id) "
                    "VALUES (?,?,?,?,?)",
                    ("ST002", DEMO_SITUATION_NAME, DEMO_DESCRIPTION, "a" * 64, "U1"),
                )
                connection.commit()
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()

            plan = inspect_workspace(db_path)

            after = hashlib.sha256(db_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual("ST002", plan.demo_situation_id)
        self.assertEqual(1, plan.demo_situation_count)
        self.assertEqual(0, plan.demo_collision_count)

    def test_same_name_without_marker_is_a_read_only_collision(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "airport_group.sqlite3"
            AirportRepository(db_path).init_schema()
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    "INSERT INTO situations "
                    "(situation_id,name,description,content_hash,owner_user_id) "
                    "VALUES (?,?,?,?,?)",
                    ("ST002", DEMO_SITUATION_NAME, None, "a" * 64, "U1"),
                )
                connection.commit()
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()

            plan = inspect_workspace(db_path)

            after = hashlib.sha256(db_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertIsNone(plan.demo_situation_id)
        self.assertEqual(0, plan.demo_situation_count)
        self.assertEqual(1, plan.demo_collision_count)
        self.assertEqual(("ST002",), plan.demo_collision_ids)
        with self.assertRaises(DemoCollisionError):
            require_demo_apply_safe(plan)
        with self.assertRaises(DemoCollisionError):
            find_demo_situation([
                {"situation_id": "ST002", "name": DEMO_SITUATION_NAME, "description": None}
            ])

    def test_repeated_marker_lookup_reuses_one_stable_situation_id(self) -> None:
        rows = [{
            "situation_id": "ST002",
            "name": DEMO_SITUATION_NAME,
            "description": DEMO_DESCRIPTION,
        }]
        first = find_demo_situation(rows)
        second = find_demo_situation(rows)
        self.assertEqual("ST002", first["situation_id"])
        self.assertEqual(first["situation_id"], second["situation_id"])
        self.assertEqual(1, len(rows))

    def test_duplicate_demo_names_are_rejected(self) -> None:
        rows = [
            {"situation_id": "ST002", "name": DEMO_SITUATION_NAME, "description": DEMO_DESCRIPTION},
            {"situation_id": "ST003", "name": DEMO_SITUATION_NAME, "description": DEMO_DESCRIPTION},
        ]
        with self.assertRaisesRegex(RuntimeError, "multiple standard demo Situations"):
            find_demo_situation(rows)

    def test_terminal_batch_cleanup_is_exact_and_preserves_other_runs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "runs.sqlite3"
            repo = RunRepository(db_path)
            repo.init_schema()
            owned = make_snapshot(run_id="RUN-demo", situation_id="ST002")
            other = make_snapshot(run_id="RUN-other", situation_id="ST001")
            repo.create_queued(snapshot=owned, owner_user_id="U1")
            repo.create_queued(snapshot=other, owner_user_id="U2")
            repo.mark_failed("RUN-demo", message="demo cleanup fixture")
            repo.mark_failed("RUN-other", message="other fixture")

            removed = repo.delete_terminal_batch(
                run_ids=["RUN-demo"], owner_user_id="U1", situation_id="ST002"
            )

            self.assertEqual(1, removed)
            self.assertIsNone(repo.get("RUN-demo"))
            self.assertIsNotNone(repo.get("RUN-other"))

    def test_cleanup_refuses_active_or_out_of_scope_runs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "runs.sqlite3"
            repo = RunRepository(db_path)
            repo.init_schema()
            repo.create_queued(
                snapshot=make_snapshot(run_id="RUN-active", situation_id="ST002"),
                owner_user_id="U1",
            )
            with self.assertRaises(RunRepositoryError):
                repo.delete_terminal_batch(
                    run_ids=["RUN-active"], owner_user_id="U1", situation_id="ST002"
                )


if __name__ == "__main__":
    unittest.main()
