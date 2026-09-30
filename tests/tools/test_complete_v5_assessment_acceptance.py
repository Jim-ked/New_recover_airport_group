from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.dev_validation.complete_v5_assessment_acceptance import (
    evaluate_v5_assessments,
    execute_security_acceptance,
)
from backend.storage.security_repository import SecurityRepository


ROOT = Path(__file__).resolve().parents[2]
V5_DATABASE = ROOT / "runtime" / "v5-rebuild-20260930" / "db" / "jiangsu-six-airport-v5-rebuilt.sqlite3"
MANIFEST = ROOT / "resources" / "experiments" / "jiangsu_six_airport_v5.json"
EXPECTED = ROOT / "resources" / "experiments" / "jiangsu_six_airport_v5_assessment_index.json"


class V5AssessmentAcceptanceTests(unittest.TestCase):
    def test_frozen_assessment_index_is_complete_and_uses_one_weight_vocabulary(self) -> None:
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        self.assertEqual(11, expected["evaluation_count"])
        self.assertEqual(44, expected["indicator_value_count"])
        self.assertEqual("ahp", expected["indicator_set"]["weight_source"])
        self.assertEqual(0.0, expected["indicator_set"]["ahp_consistency_ratio"])
        self.assertEqual(11, len(expected["runs"]))
        self.assertTrue(all(len(row["values"]) == 4 for row in expected["runs"]))

    @unittest.skipUnless(V5_DATABASE.exists(), "rebuilt V5 database is an external acceptance asset")
    def test_rebuilt_v5_reproduces_all_formal_indicator_results(self) -> None:
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "v5.sqlite3"
            shutil.copy2(V5_DATABASE, database)
            actual = evaluate_v5_assessments(database, MANIFEST)

        self.assertEqual(11, actual["evaluation_count"])
        self.assertEqual(44, actual["indicator_value_count"])
        by_label = {row["label"]: row for row in actual["runs"]}
        for expected_row in expected["runs"]:
            with self.subTest(label=expected_row["label"]):
                actual_row = by_label[expected_row["label"]]
                values = {item["code"]: item["raw_value"] for item in actual_row["indicators"]}
                ordered = [values[code] for code in expected["value_order"]]
                self.assertEqual(expected_row["run_id"], actual_row["run_id"])
                self.assertEqual(expected_row["values"], ordered)
                self.assertAlmostEqual(expected_row["support_index"], actual_row["support_index"], places=6)
                self.assertAlmostEqual(
                    expected_row["weighted_composite"], actual_row["weighted_composite"], places=6
                )
                self.assertEqual(expected_row["risk_level"], actual_row["risk"]["level"])

    @unittest.skipUnless(V5_DATABASE.exists(), "rebuilt V5 database is an external acceptance asset")
    def test_isolated_security_acceptance_records_positive_and_negative_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "v5.sqlite3"
            shutil.copy2(V5_DATABASE, database)
            before = len(SecurityRepository(database).list_backups())
            result = execute_security_acceptance(database, root / "runtime")

            self.assertFalse(result["positive"]["full_backup"]["encrypted"])
            self.assertTrue(Path(result["positive"]["full_backup"]["path"]).is_file())
            self.assertTrue(Path(result["positive"]["csv_report"]["path"]).is_file())
            self.assertTrue(Path(result["positive"]["pdf_report"]["path"]).is_file())
            self.assertEqual("validation", result["positive"]["validation_alert"]["kind"])
            self.assertTrue(all(item["result"] == "PASS" for item in result["negative"].values()))
            self.assertEqual(401, result["negative"]["unauthenticated"]["status"])
            self.assertTrue(
                all(
                    result["negative"][name]["status"] == 422
                    for name in ("encrypted_true", "incremental", "outside_path", "invalid_sqlite")
                )
            )
            backups = SecurityRepository(database).list_backups()
            self.assertEqual(before + 1, len(backups))
            self.assertTrue(all(item.path.startswith("backup_full_") for item in backups))
            self.assertTrue(all(item.encrypted is False for item in backups))


if __name__ == "__main__":
    unittest.main()
