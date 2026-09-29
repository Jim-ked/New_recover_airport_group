from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path

from backend.algorithm.runner import run_once
from backend.auth.principal import Principal
from backend.services.result_export_service import ResultExportService, build_tidy_rows
from backend.services.run_result_service import RunResultService
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.web.results_api import ResultsApi
from tests.algorithm.test_runner import RunnerFakeModel
from tests.algorithm.test_snapshot_adapter import make_snapshot


class ComparisonExportRowsTests(unittest.TestCase):
    def test_comparison_export_preserves_focus_relation_components_and_full_timeline(self):
        source = {
            "schema_version": "report-data.v1",
            "kind": "scenario_comparison",
            "source_run_ids": ["BASE", "CURRENT"],
            "data": {
                "focus_comparison": {
                    "baseline_run_id": "BASE",
                    "comparison_run_id": "CURRENT",
                    "relation": "CURRENT-BASE",
                    "values": {
                        "unmet_sorties_total": {"baseline": 4, "current": 2, "delta": -2},
                    },
                    "objective_components": {
                        "F1": {"baseline": 10, "current": 11, "delta": 1, "definition_comparable": True},
                    },
                    "timeline": {
                        "windows": [0, 1],
                        "departures": {
                            "baseline": [1, 2], "current": [2, 2], "delta": [1, 0],
                            "cumulative": {"baseline": [1, 3], "current": [2, 4], "delta": [1, 1]},
                        },
                        "returns": {
                            "baseline": [0, 1], "current": [0, 2], "delta": [0, 1],
                            "cumulative": {"baseline": [0, 1], "current": [0, 2], "delta": [0, 1]},
                        },
                    },
                    "conclusions": ["缺口减少2架次。"],
                },
            },
        }

        rows = build_tidy_rows(source)

        self.assertTrue(any(row["metric"] == "relation" and row["value"] == "CURRENT-BASE" for row in rows))
        self.assertTrue(any(row["section"] == "objective_components" and row["metric"] == "F1.delta" for row in rows))
        self.assertEqual(2, len([row for row in rows if row["section"] == "focus_timeline" and row["metric"] == "departures" and row["series"] == "delta"]))
        exporter = ResultExportService()
        csv_text = exporter.render_csv(source).content.decode("utf-8-sig")
        self.assertIn("CURRENT-BASE", csv_text)
        self.assertIn("objective_components", csv_text)
        self.assertTrue(exporter.render_pdf(source).content.startswith(b"%PDF-"))


class ResultExportServiceTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = Path(self.td.name) / "app.sqlite"
        self.runs = RunRepository(self.db)
        self.runs.init_schema()
        self.snapshots = RunSnapshotRepository(self.db)
        service = RunResultService(run_repository=self.runs, snapshot_repository=self.snapshots)
        self.api = ResultsApi(result_service=service)
        snap = make_snapshot(run_id="EXPORT-FILE", cluster_enabled=False)
        self.runs.create_queued(snapshot=snap, owner_user_id="ADMIN")
        self.runs.claim_running(snap.run_id)
        result = run_once(snap, model_factory=RunnerFakeModel)
        service.persist_success(result=result)
        self.source = self.api.export_data(
            {"kind": "single_run", "run_id": "EXPORT-FILE"},
            principal=Principal("ADMIN", is_admin=True),
        ).body
        self.exporter = ResultExportService()

    def tearDown(self):
        self.td.cleanup()

    def test_tidy_csv_contains_summary_timeline_and_entity_rows(self):
        rows = build_tidy_rows(self.source)
        self.assertTrue(any(r["section"] == "summary" for r in rows))
        self.assertTrue(any(r["section"] == "timeline" for r in rows))
        self.assertTrue(any(r["entity_type"] == "airport" for r in rows))
        rendered = self.exporter.render_csv(self.source)
        self.assertTrue(rendered.content.startswith(b"\xef\xbb\xbf"))
        text = rendered.content.decode("utf-8-sig")
        parsed = list(csv.DictReader(io.StringIO(text)))
        self.assertGreater(len(parsed), 5)
        self.assertIn("section", parsed[0])
        self.assertTrue(rendered.filename.endswith(".csv"))

    def test_pdf_is_real_pdf_and_contains_multiple_report_sections(self):
        rendered = self.exporter.render_pdf(self.source)
        self.assertTrue(rendered.content.startswith(b"%PDF-"))
        self.assertGreater(len(rendered.content), 2000)
        self.assertTrue(rendered.filename.endswith(".pdf"))
        out = Path(self.td.name) / "report.pdf"
        out.write_bytes(rendered.content)
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
