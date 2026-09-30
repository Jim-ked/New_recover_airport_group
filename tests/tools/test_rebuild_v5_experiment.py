from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest

from tools.dev_validation.rebuild_v5_experiment import (
    ArchiveBundle,
    ExperimentDefinitionError,
    extract_frozen_inventory_facts,
    load_definition,
    prepare_experiment_database,
    project_experiment_assessment,
    validate_experiment_comparisons,
    verify_archive,
)
from backend.storage.assessment_repository import AssessmentRepository


ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "resources" / "experiments" / "jiangsu_six_airport_v5.json"
ARCHIVE = ROOT / "runtime" / "stage4-browser" / "stage4-v5.sqlite"


class RebuildV5ExperimentTests(unittest.TestCase):
    @staticmethod
    def _replace_snapshot_payload(snapshot, payload):
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        from backend.domain.run_snapshot import RunSnapshot
        return RunSnapshot(
            run_id=snapshot.run_id,
            situation_id=snapshot.situation_id,
            content_hash=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
            payload_json=payload_json,
        )

    def test_manifest_freezes_exact_v5_matrix(self) -> None:
        definition = load_definition(MANIFEST)

        self.assertEqual(11, len(definition.runs))
        self.assertEqual(
            {"ST-V5-MOTHER-01", "ST-V5-PRESSURE-01"},
            set(definition.situations),
        )
        self.assertEqual(5, len(definition.damage_scenarios))
        self.assertEqual(
            {"R0", "R1", "R2", "RESOURCE", "CAPACITY", "MUN2", "STORAGE", "P0", "P1", "P2", "P3"},
            {run.label for run in definition.runs},
        )

    @unittest.skipUnless(ARCHIVE.exists(), "V5 archive is an external experiment asset")
    def test_archive_matches_frozen_snapshot_and_run_contract(self) -> None:
        definition = load_definition(MANIFEST)
        bundle = verify_archive(ARCHIVE, definition)

        self.assertEqual(11, len(bundle.snapshots))
        self.assertEqual(
            {run.run_id for run in definition.runs},
            set(bundle.snapshots),
        )
        self.assertTrue(
            all(
                snapshot.to_dict()["schema"] == "run_input_snapshot_v5"
                for snapshot in bundle.snapshots.values()
            )
        )

        facts = extract_frozen_inventory_facts(bundle, definition)
        self.assertEqual(
            {
                "initial_quantity": 220.0,
                "replenishment_capacity_per_window": 15.0,
                "storage_capacity": 232.0,
            },
            facts["ST-V5-MOTHER-01"]["AP179"]["MUN-1"],
        )
        self.assertEqual(
            facts["ST-V5-MOTHER-01"],
            facts["ST-V5-PRESSURE-01"],
        )

    @unittest.skipUnless(ARCHIVE.exists(), "V5 archive is an external experiment asset")
    def test_frozen_inventory_rejects_missing_or_inconsistent_snapshot_facts(self) -> None:
        definition = load_definition(MANIFEST)
        bundle = verify_archive(ARCHIVE, definition)
        snapshots = dict(bundle.snapshots)
        target = snapshots["RUN-ROUND15-R1"]

        missing_payload = target.to_dict()
        del missing_payload["situation"]["airports"][0]["operational_profile"][
            "resource_stocks"
        ][0]["storage_capacity"]
        missing = dict(snapshots)
        missing[target.run_id] = self._replace_snapshot_payload(target, missing_payload)
        with self.assertRaisesRegex(ExperimentDefinitionError, "storage_capacity"):
            extract_frozen_inventory_facts(
                ArchiveBundle(missing, bundle.solutions, bundle.metrics), definition
            )

        changed_payload = target.to_dict()
        stock = changed_payload["situation"]["airports"][0]["operational_profile"][
            "resource_stocks"
        ][0]
        stock["storage_capacity"] = float(stock["storage_capacity"]) + 1.0
        changed_payload["dynamic_inventory"]["storage_capacity"][
            changed_payload["situation"]["airports"][0]["airport"]["airport_id"]
        ][stock["resource_type_id"]] = stock["storage_capacity"]
        inconsistent = dict(snapshots)
        inconsistent[target.run_id] = self._replace_snapshot_payload(
            target, changed_payload
        )
        with self.assertRaisesRegex(ExperimentDefinitionError, "inconsistent"):
            extract_frozen_inventory_facts(
                ArchiveBundle(inconsistent, bundle.solutions, bundle.metrics), definition
            )

    @unittest.skipUnless(ARCHIVE.exists(), "V5 archive is an external experiment asset")
    def test_prepare_uses_independent_database_and_refuses_overwrite(self) -> None:
        definition = load_definition(MANIFEST)
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = pathlib.Path(temp_dir) / "experiment.sqlite3"
            prepared = prepare_experiment_database(
                archive_path=ARCHIVE,
                destination=destination,
                definition=definition,
            )
            self.assertEqual(destination, prepared)
            self.assertEqual(
                11,
                len(
                    verify_archive(
                        prepared,
                        definition,
                        verify_file_hash=False,
                    ).snapshots
                ),
            )
            from contextlib import closing
            with closing(sqlite3.connect(prepared)) as connection:
                airport_row = connection.execute(
                    "SELECT quantity,replenishment_capacity_per_window,storage_capacity "
                    "FROM airport_resource_stocks "
                    "WHERE airport_id='AP179' AND resource_type_id='MUN-1'"
                ).fetchone()
                situation_rows = connection.execute(
                    "SELECT situation_id,quantity,replenishment_capacity_per_window,storage_capacity "
                    "FROM situation_resource_stocks "
                    "WHERE airport_id='AP179' AND resource_type_id='MUN-1' "
                    "ORDER BY situation_id"
                ).fetchall()
            self.assertEqual((220.0, 15.0, 232.0), airport_row)
            self.assertEqual(
                [
                    ("ST-V5-MOTHER-01", 220.0, 15.0, 232.0),
                    ("ST-V5-PRESSURE-01", 220.0, 15.0, 232.0),
                ],
                situation_rows,
            )
            with self.assertRaisesRegex(ExperimentDefinitionError, "refusing to overwrite"):
                prepare_experiment_database(
                    archive_path=ARCHIVE,
                    destination=destination,
                    definition=definition,
                )

    @unittest.skipUnless(ARCHIVE.exists(), "V5 archive is an external experiment asset")
    def test_archive_can_project_traceable_capacity_observations_and_strict_comparisons(self) -> None:
        definition = load_definition(MANIFEST)
        with tempfile.TemporaryDirectory() as temp_dir:
            database = prepare_experiment_database(
                archive_path=ARCHIVE,
                destination=pathlib.Path(temp_dir) / "experiment.sqlite3",
                definition=definition,
            )
            assessment = project_experiment_assessment(database, definition)
            comparisons = validate_experiment_comparisons(database, definition)

            self.assertEqual(66, assessment["result_count"])
            self.assertEqual("unavailable", assessment["risk"]["status"])
            self.assertTrue(all(item["eligible"] for item in comparisons["checks"]))
            rows, total = AssessmentRepository(database).list_results(limit=100)
            self.assertEqual(66, total)
            self.assertTrue(all(row.source_run_id and row.metrics_hash for row in rows))
            self.assertEqual([], AssessmentRepository(database).list_risks())


if __name__ == "__main__":
    unittest.main()
