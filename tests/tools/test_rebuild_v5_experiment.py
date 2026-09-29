from __future__ import annotations

import pathlib
import tempfile
import unittest

from tools.dev_validation.rebuild_v5_experiment import (
    ExperimentDefinitionError,
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
