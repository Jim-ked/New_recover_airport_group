from __future__ import annotations

import csv
import io
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from pypdf import PdfReader

from backend.algorithm.runner import run_once
from backend.auth.principal import Principal
from backend.services.result_export_service import ResultExportService, build_tidy_rows
from backend.services.run_result_service import RunResultService
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.web.results_api import ResultsApi
from tests.algorithm.test_runner import RunnerFakeModel, fixed_cluster_selector
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

    def _persist(self, snapshot):
        self.runs.create_queued(snapshot=snapshot, owner_user_id="ADMIN")
        self.runs.claim_running(snapshot.run_id)
        kwargs = {"model_factory": RunnerFakeModel}
        if snapshot.to_dict()["run_config"]["cluster_enabled"]:
            kwargs["cluster_selector_fn"] = fixed_cluster_selector
        self.api.results.persist_success(result=run_once(snapshot, **kwargs))

    def _comparison_sources(self):
        from backend.domain.damage import DamageScenario

        scenario = DamageScenario.from_mapping({
            "damage_scenario_id": "DS-EXPORT",
            "name": "Export comparison damage",
            "category": "custom",
            "events": [],
        })
        self._persist(make_snapshot(
            run_id="EXPORT-R0",
            cluster_enabled=False,
            available_scenarios=(scenario,),
        ))
        self._persist(make_snapshot(
            run_id="EXPORT-R1",
            cluster_enabled=False,
            scenario=scenario,
        ))
        self._persist(make_snapshot(
            run_id="EXPORT-R2",
            cluster_enabled=True,
            scenario=scenario,
        ))
        principal = Principal("ADMIN", is_admin=True)
        return {
            "damage_comparison": self.api.export_data({
                "kind": "damage_comparison",
                "r0_run_id": "EXPORT-R0",
                "r1_run_id": "EXPORT-R1",
                "r2_run_id": "EXPORT-R2",
            }, principal=principal).body,
            "scenario_comparison": self.api.export_data({
                "kind": "scenario_comparison",
                "run_ids": ["EXPORT-R0", "EXPORT-R1"],
            }, principal=principal).body,
            "configuration_comparison": self.api.export_data({
                "kind": "configuration_comparison",
                "run_ids": ["EXPORT-R1", "EXPORT-R2"],
                "baseline_run_id": "EXPORT-R1",
            }, principal=principal).body,
        }

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
        self.assertEqual("text/csv; charset=utf-8", rendered.mimetype)
        self.assertTrue(rendered.filename.endswith(".csv"))

    def test_json_preserves_the_complete_canonical_report_source(self):
        rendered = self.exporter.render_json(self.source)

        self.assertEqual("application/json; charset=utf-8", rendered.mimetype)
        self.assertTrue(rendered.filename.endswith(".json"))
        self.assertEqual(self.source, json.loads(rendered.content.decode("utf-8")))
        self.assertIn("\n  \"schema_version\"", rendered.content.decode("utf-8"))

    def test_xml_round_trips_dynamic_keys_special_characters_and_scalar_types(self):
        source = {
            "schema_version": "report-data.v1",
            "kind": "single_run",
            "source_run_ids": ["RUN<&\"1"],
            "data": {
                "123 invalid <key>": {
                    "text": "中文 & < > \"quoted\"",
                    "integer": 7,
                    "float": 2.5,
                    "boolean": True,
                    "nothing": None,
                    "items": [False, None, "值"],
                }
            },
            "rendering": {"status": "source_ready", "supported_formats": ["pdf", "csv", "json", "xml"]},
        }

        rendered = self.exporter.render_xml(source)
        root = ET.fromstring(rendered.content)

        self.assertTrue(rendered.content.startswith(b"<?xml"))
        self.assertEqual("application/xml; charset=utf-8", rendered.mimetype)
        self.assertTrue(rendered.filename.endswith(".xml"))
        self.assertEqual("report_data", root.tag)
        self.assertEqual("report-data.v1", root.attrib["schema_version"])
        self.assertEqual(source, self._decode_xml_object(root))

    @classmethod
    def _decode_xml_object(cls, element):
        return {child.attrib["name"]: cls._decode_xml_value(child) for child in element.findall("field")}

    @classmethod
    def _decode_xml_value(cls, element):
        value_type = element.attrib["type"]
        if value_type == "object":
            return cls._decode_xml_object(element)
        if value_type == "array":
            return [cls._decode_xml_value(item) for item in element.findall("item")]
        if value_type == "null":
            return None
        if value_type == "boolean":
            return element.text == "true"
        if value_type == "integer":
            return int(element.text)
        if value_type == "float":
            return float(element.text)
        return element.text or ""

    def test_pdf_is_real_pdf_and_contains_multiple_report_sections(self):
        rendered = self.exporter.render_pdf(self.source)
        self.assertTrue(rendered.content.startswith(b"%PDF-"))
        self.assertGreater(len(rendered.content), 2000)
        self.assertGreaterEqual(len(re.findall(rb"/Type\s*/Page\b", rendered.content)), 1)
        self.assertIn(b"/FontFile2", rendered.content)
        self.assertIn(b"/ToUnicode", rendered.content)
        self.assertEqual("application/pdf", rendered.mimetype)
        self.assertTrue(rendered.filename.endswith(".pdf"))
        out = Path(self.td.name) / "report.pdf"
        out.write_bytes(rendered.content)
        self.assertTrue(out.exists())

    def test_render_dispatches_all_formats_and_rejects_unknown_format(self):
        expected = {
            "pdf": ("application/pdf", ".pdf"),
            "csv": ("text/csv; charset=utf-8", ".csv"),
            "json": ("application/json; charset=utf-8", ".json"),
            "xml": ("application/xml; charset=utf-8", ".xml"),
        }
        for fmt, (mimetype, extension) in expected.items():
            with self.subTest(fmt=fmt):
                rendered = self.exporter.render(self.source, fmt)
                self.assertEqual(mimetype, rendered.mimetype)
                self.assertTrue(rendered.filename.endswith(extension))
                self.assertTrue(rendered.content)

        with self.assertRaisesRegex(ValueError, "pdf, csv, json or xml"):
            self.exporter.render(self.source, "xlsx")

    def test_configuration_comparison_all_formats_contain_canonical_business_rows(self):
        source = self._comparison_sources()["configuration_comparison"]
        data = source["data"]

        self.assertEqual(["EXPORT-R1", "EXPORT-R2"], source["source_run_ids"])
        self.assertEqual(["EXPORT-R1", "EXPORT-R2"], data["run_ids"])
        self.assertEqual("EXPORT-R1", data["baseline_run_id"])
        self.assertEqual({"EXPORT-R1", "EXPORT-R2"}, set(data["run_summaries"]))
        self.assertTrue(data["run_summaries"]["EXPORT-R1"])
        self.assertIn("A1", data["airports"])
        self.assertIn("EXPORT-R2", data["airports"]["A1"])
        self.assertIn("EXPORT-R2", data["summary_deltas_vs_baseline"])

        parsed_json = json.loads(self.exporter.render_json(source).content.decode("utf-8"))
        self.assertEqual(source, parsed_json)
        self.assertIn("A1", parsed_json["data"]["airports"])

        root = ET.fromstring(self.exporter.render_xml(source).content)
        parsed_xml = self._decode_xml_object(root)
        self.assertEqual(source, parsed_xml)
        self.assertIn("A1", parsed_xml["data"]["airports"])

        csv_text = self.exporter.render_csv(source).content.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        self.assertTrue(any(
            row["section"] == "comparison_objects"
            and row["entity_id"] == "EXPORT-R1"
            and row["series"] == "baseline"
            for row in rows
        ))
        self.assertTrue(any(
            row["section"] == "summary_deltas_vs_baseline"
            and row["entity_id"] == "EXPORT-R2"
            and row["metric"] == "participating_airport_count_delta"
            and row["value"] == "0.0"
            for row in rows
        ))
        self.assertTrue(any(
            row["section"] == "airports"
            and row["entity_id"] == "A1"
            and row["series"] == "EXPORT-R2"
            and row["metric"] == "departures_total"
            for row in rows
        ))

    def test_all_comparison_exports_contain_run_summary_airport_and_pdf_rows(self):
        for kind, source in self._comparison_sources().items():
            with self.subTest(kind=kind):
                csv_text = self.exporter.render_csv(source).content.decode("utf-8-sig")
                csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
                self.assertTrue(any(
                    row["section"] == "comparison_objects"
                    for row in csv_rows
                ))
                self.assertTrue(any(
                    row["section"] == "run_summaries"
                    and row["entity_id"] == source["source_run_ids"][0]
                    for row in csv_rows
                ))
                self.assertTrue(any(
                    row["section"] == "airports"
                    and row["entity_id"] == "A1"
                    and row["metric"] == "departures_total"
                    for row in csv_rows
                ))

                rendered = self.exporter.render_pdf(source)
                reader = PdfReader(io.BytesIO(rendered.content))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                airport_value = source["data"]["airports"]["A1"]
                if kind == "damage_comparison":
                    expected_value = airport_value["departures_total"]["R0"]
                else:
                    first_run_id = source["source_run_ids"][0]
                    expected_value = airport_value[first_run_id]["departures_total"]

                for run_id in source["source_run_ids"]:
                    self.assertIn(run_id, text)
                self.assertIn("A1", text)
                self.assertIn(str(expected_value), text)


if __name__ == "__main__":
    unittest.main()
