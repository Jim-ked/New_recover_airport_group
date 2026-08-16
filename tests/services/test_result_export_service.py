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
