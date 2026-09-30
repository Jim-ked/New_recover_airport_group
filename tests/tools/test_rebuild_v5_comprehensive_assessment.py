from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.dev_validation.rebuild_v5_comprehensive_assessment import (
    ComprehensiveAssessmentRebuildError,
    rebuild_v5_comprehensive_assessments,
)


ROOT = Path(__file__).resolve().parents[2]
V5_DATABASE = (
    ROOT
    / "runtime"
    / "v5-rebuild-20260930"
    / "db"
    / "jiangsu-six-airport-v5-inventory-repaired.sqlite3"
)
MANIFEST = ROOT / "resources" / "experiments" / "jiangsu_six_airport_v5.json"


def formal_state(database: Path) -> dict[str, object]:
    with closing(sqlite3.connect(database)) as connection:
        runs = connection.execute(
            """SELECT r.run_id,r.status,rr.solution_hash,rr.metrics_hash
               FROM runs r JOIN run_results rr ON rr.run_id=r.run_id
               ORDER BY r.run_id"""
        ).fetchall()
        return {
            "runs": runs,
            "capacity_results": connection.execute(
                "SELECT COUNT(*) FROM assessment_result"
            ).fetchone()[0],
            "comparisons": connection.execute(
                "SELECT COUNT(*) FROM assessment_comparison"
            ).fetchone()[0],
        }


@unittest.skipUnless(V5_DATABASE.exists(), "repaired V5 database is an external acceptance asset")
class RebuildV5ComprehensiveAssessmentTests(unittest.TestCase):
    def test_rebuild_rejects_missing_formal_weight_source_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "v5.sqlite3"
            shutil.copy2(V5_DATABASE, database)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """UPDATE indicator_nodes SET weight=NULL,weight_source=NULL
                       WHERE indicator_set_id='INDSET-RUN-ASSESSMENT-V1' AND level=3"""
                )
                connection.execute("DELETE FROM assessment_run_evaluation")
                connection.commit()

            with self.assertRaisesRegex(
                ComprehensiveAssessmentRebuildError, "weights are unavailable"
            ):
                rebuild_v5_comprehensive_assessments(
                    database=database,
                    manifest=MANIFEST,
                    backup_dir=root / "backups",
                )
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    0,
                    connection.execute(
                        "SELECT COUNT(*) FROM assessment_run_evaluation"
                    ).fetchone()[0],
                )

    def test_rebuild_is_traceable_idempotent_and_preserves_formal_run_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "v5.sqlite3"
            shutil.copy2(V5_DATABASE, database)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("DELETE FROM assessment_run_evaluation")
                connection.commit()
            before = formal_state(database)

            first = rebuild_v5_comprehensive_assessments(
                database=database,
                manifest=MANIFEST,
                backup_dir=root / "backups",
            )
            second = rebuild_v5_comprehensive_assessments(
                database=database,
                manifest=MANIFEST,
                backup_dir=root / "backups",
            )
            after = formal_state(database)

            self.assertEqual(before, after)
            self.assertEqual(11, first["evaluation_count"])
            self.assertEqual(44, first["indicator_value_count"])
            self.assertEqual(11, second["evaluation_count"])
            self.assertEqual(44, second["indicator_value_count"])
            self.assertEqual({"available"}, {row["status"] for row in second["runs"]})
            self.assertEqual("ahp", second["weight_provenance"]["method"])
            self.assertEqual(1, second["weight_provenance"]["revision"])
            self.assertRegex(
                second["weight_provenance"]["config_hash"], r"^[0-9a-f]{64}$"
            )
            self.assertEqual(66, second["preserved"]["capacity_results"])
            self.assertEqual(9, second["preserved"]["comparisons"])
            self.assertTrue(Path(first["backup"]["path"]).is_file())
            self.assertTrue(Path(second["backup"]["path"]).is_file())
            self.assertNotEqual(first["backup"]["path"], second["backup"]["path"])
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    11,
                    connection.execute(
                        "SELECT COUNT(*) FROM assessment_run_evaluation"
                    ).fetchone()[0],
                )
                self.assertEqual(
                    44,
                    connection.execute(
                        "SELECT COUNT(*) FROM assessment_indicator_value"
                    ).fetchone()[0],
                )


if __name__ == "__main__":
    unittest.main()
