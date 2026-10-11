from __future__ import annotations

import csv
import io
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from backend.algorithm.runner import run_once
from backend.auth.principal import Principal
from backend.services.result_export_service import (
    PDF_SYSTEM_NAME,
    ResultExportService,
    _chart_value_label,
    _stacked_bar_label_array,
    build_pdf_report_model,
    build_tidy_rows,
)
from backend.services.run_result_service import RunResultService
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.web.results_api import ResultsApi
from tests.algorithm.test_runner import RunnerFakeModel
from tests.algorithm.test_snapshot_adapter import make_snapshot


def _rich_comparison_source(kind: str):
    run_ids = ["BASE", "CURRENT"]
    return {
        "schema_version": "report-data.v1",
        "kind": kind,
        "source_run_ids": run_ids,
        "data": {
            "run_ids": run_ids,
            "baseline_run_id": "BASE",
            "comparison_run_id": "CURRENT",
            "summary": {
                "BASE": {"required_sorties_total": 10, "fulfilled_sorties_total": 6, "unmet_sorties_total": 4, "scheduled_sorties_total": 6, "completion_ratio": 0.6},
                "CURRENT": {"required_sorties_total": 10, "fulfilled_sorties_total": 8, "unmet_sorties_total": 2, "scheduled_sorties_total": 8, "completion_ratio": 0.8},
            },
            "timeline": {
                "windows": [0, 1, 2],
                "slot_minutes": 15,
                "departures": {"BASE": [1, 3, 2], "CURRENT": [2, 4, 2]},
                "returns": {"BASE": [0, 2, 4], "CURRENT": [0, 3, 5]},
            },
            "airports": {
                "AP1": {
                    "BASE": {"departures_total": 4, "departure_share": 2 / 3},
                    "CURRENT": {"departures_total": 5, "departure_share": 0.625},
                },
                "AP2": {
                    "BASE": {"departures_total": 2, "departure_share": 1 / 3},
                    "CURRENT": {"departures_total": 3, "departure_share": 0.375},
                },
            },
            "tasks": {
                "M1": {
                    "BASE": {"required_total": 6, "fulfilled_total": 4, "scheduled_total": 4},
                    "CURRENT": {"required_total": 6, "fulfilled_total": 5, "scheduled_total": 5},
                },
                "M2": {
                    "BASE": {"required_total": 4, "fulfilled_total": 2, "scheduled_total": 2},
                    "CURRENT": {"required_total": 4, "fulfilled_total": 3, "scheduled_total": 3},
                },
            },
            "aircraft": {
                "F1": {"BASE": {"scheduled_total": 4}, "CURRENT": {"scheduled_total": 5}},
                "F2": {"BASE": {"scheduled_total": 2}, "CURRENT": {"scheduled_total": 3}},
            },
            "resources": {
                "category_min_remaining_ratio": {
                    "fuel": {"BASE": {"ratio": 0.52}, "CURRENT": {"ratio": 0.41}},
                    "material": {"BASE": {"ratio": 0.76}, "CURRENT": {"ratio": 0.68}},
                    "munition": {"BASE": {"ratio": 0.63}, "CURRENT": {"ratio": 0.57}},
                }
            },
        },
    }


class ComparisonExportRowsTests(unittest.TestCase):
    def test_csv_and_pdf_keep_configuration_time_and_trace_for_each_source(self):
        source = _rich_comparison_source('scenario_comparison')
        source['data']['run_identities'] = {
            'BASE': {'title': '损毁方案', 'situation_title': '情境 · 损毁方案',
                     'subtitle': '出动优先 · 组群开启(K=4) · 10-10 13:42',
                     'short_id': 'R-7193A5B2', 'detail': '完整配置'}
        }
        rows = build_tidy_rows(source)
        identities = {row['metric']: row['value'] for row in rows if row['section'] == 'run_identity'}
        self.assertEqual('R-7193A5B2', identities['short_id'])
        self.assertIn('13:42', identities['subtitle'])
        pdf = build_pdf_report_model(source)
        self.assertEqual(('BASE', '情境 · 损毁方案', source['data']['run_identities']['BASE']['subtitle'], 'R-7193A5B2'), pdf['run_identity_rows'][0])

    def test_comparison_export_preserves_focus_relation_components_and_full_timeline(self):
        source = {
            "schema_version": "report-data.v1",
            "kind": "scenario_comparison",
            "source_run_ids": ["BASE", "CURRENT"],
            "data": {
                "run_ids": ["BASE", "CURRENT"],
                "baseline_run_id": "BASE",
                "comparison_run_id": "CURRENT",
                "summary": {
                    "BASE": {"required_sorties_total": 10, "fulfilled_sorties_total": 6, "unmet_sorties_total": 4, "scheduled_sorties_total": 6},
                    "CURRENT": {"required_sorties_total": 10, "fulfilled_sorties_total": 8, "unmet_sorties_total": 2, "scheduled_sorties_total": 8},
                },
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
                "timeline": {
                    "windows": [0, 1],
                    "slot_minutes": 15,
                    "departures": {"BASE": [1, 2], "CURRENT": [2, 2]},
                    "returns": {"BASE": [0, 1], "CURRENT": [0, 2]},
                },
                "airports": {
                    "AP1": {
                        "BASE": {"departures_total": 3, "departure_share": 1.0},
                        "CURRENT": {"departures_total": 4, "departure_share": 1.0},
                    },
                },
                "tasks": {
                    "M1": {
                        "BASE": {"required_total": 10, "scheduled_total": 6},
                        "CURRENT": {"required_total": 10, "scheduled_total": 8},
                    },
                },
                "conclusions": ["缺口减少2架次。"],
            },
        }

        rows = build_tidy_rows(source)

        self.assertTrue(any(row["metric"] == "relation" and row["value"] == "CURRENT-BASE" for row in rows))
        self.assertTrue(any(row["section"] == "objective_components" and row["metric"] == "F1.delta" for row in rows))
        self.assertEqual(2, len([row for row in rows if row["section"] == "focus_timeline" and row["metric"] == "departures" and row["series"] == "delta"]))

        report = build_pdf_report_model(source)
        self.assertEqual([("基准", "BASE"), ("对照", "CURRENT")], report["runs"])
        self.assertEqual(2, len(report["run_summary_rows"]))
        self.assertEqual(1, len(report["airport_rows"]))
        self.assertEqual(1, len(report["task_rows"]))
        self.assertEqual([0, 1], report["timeline"]["windows"])
        self.assertEqual([1, 2], report["timeline"]["series"][0][1])
        self.assertIn("缺口减少2架次。", report["conclusions"])

        exporter = ResultExportService()
        csv_text = exporter.render_csv(source).content.decode("utf-8-sig")
        self.assertIn("CURRENT-BASE", csv_text)
        self.assertIn("objective_components", csv_text)
        pdf = exporter.render_pdf(source).content
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreaterEqual(len(re.findall(rb"/Type\s*/Page\b", pdf)), 2)

    def test_pdf_model_builds_comparison_charts_from_existing_report_facts(self):
        for kind in ("damage_comparison", "scenario_comparison", "configuration_comparison"):
            with self.subTest(kind=kind):
                report = build_pdf_report_model(_rich_comparison_source(kind))
                chart_ids = {chart["id"] for chart in report["charts"]}
                self.assertIn("departure_timeline", chart_ids)
                self.assertIn("task_fulfillment", chart_ids)
                self.assertIn("airport_departures", chart_ids)
                self.assertIn("aircraft_composition", chart_ids)
                self.assertIn("resource_minimums", chart_ids)
                self.assertTrue(all(chart["categories"] or chart["windows"] for chart in report["charts"]))

    def test_pdf_model_omits_empty_optional_charts(self):
        source = _rich_comparison_source("scenario_comparison")
        source["data"]["aircraft"] = {}
        source["data"]["resources"] = {}

        report = build_pdf_report_model(source)

        chart_ids = {chart["id"] for chart in report["charts"]}
        self.assertNotIn("aircraft_composition", chart_ids)
        self.assertNotIn("resource_minimums", chart_ids)

    def test_departure_chart_trims_zero_only_tail_without_changing_source(self):
        source = _rich_comparison_source("scenario_comparison")
        source["data"]["timeline"]["windows"] = [0, 1, 2, 3, 4]
        source["data"]["timeline"]["departures"] = {
            "BASE": [0, 2, 1, 0, 0],
            "CURRENT": [0, 1, 2, 0, 0],
        }

        report = build_pdf_report_model(source)
        chart = next(item for item in report["charts"] if item["id"] == "departure_timeline")

        self.assertEqual([0, 1, 2, 3], chart["windows"])
        self.assertEqual([0, 2, 1, 0], chart["series"][0]["values"])
        self.assertEqual([0, 2, 1, 0, 0], source["data"]["timeline"]["departures"]["BASE"])

    def test_chart_with_missing_values_is_omitted_instead_of_rendered_as_zero(self):
        source = _rich_comparison_source("scenario_comparison")
        source["data"]["resources"]["category_min_remaining_ratio"]["fuel"]["CURRENT"] = None

        report = build_pdf_report_model(source)

        self.assertNotIn("resource_minimums", {chart["id"] for chart in report["charts"]})

    def test_pdf_model_includes_persisted_assessment_facts_without_recalculation(self):
        source = _rich_comparison_source("scenario_comparison")
        source["assessments"] = {
            "BASE": {
                "run_id": "BASE",
                "status": "available",
                "support_index": 0.72,
                "weighted_composite": 0.72,
                "overall_membership": {"medium": 0.8, "high": 0.2},
                "weight_source": "ahp",
                "indicator_set": {"version": "run-assessment.v1"},
                "risk": {"status": "available", "score": 0.28, "level": 2},
                "indicators": [
                    {"code": "task", "name": "任务履行率", "raw_value": 0.7, "normalized_value": 0.7, "weight": 0.6},
                    {"code": "resource", "name": "资源保障度", "raw_value": 0.75, "normalized_value": 0.75, "weight": 0.4},
                ],
            },
            "CURRENT": {
                "run_id": "CURRENT",
                "status": "available",
                "support_index": 0.84,
                "weighted_composite": 0.84,
                "overall_membership": {"high": 0.9, "medium": 0.1},
                "weight_source": "ahp",
                "indicator_set": {"version": "run-assessment.v1"},
                "risk": {"status": "available", "score": 0.16, "level": 1},
                "indicators": [
                    {"code": "task", "name": "任务履行率", "raw_value": 0.85, "normalized_value": 0.85, "weight": 0.6},
                    {"code": "resource", "name": "资源保障度", "raw_value": 0.82, "normalized_value": 0.82, "weight": 0.4},
                ],
            },
        }

        report = build_pdf_report_model(source)

        self.assertEqual(2, len(report["assessment_summary_rows"]))
        self.assertEqual(4, len(report["assessment_indicator_rows"]))
        self.assertTrue(all(len(row) == 7 for row in report["assessment_summary_rows"]))
        self.assertNotIn("run-assessment.v1", repr(report))
        chart_ids = {chart["id"] for chart in report["charts"]}
        self.assertIn("assessment_composite", chart_ids)
        self.assertIn("assessment_indicators", chart_ids)

    def test_pdf_uses_frozen_airport_short_names_in_tables_titles_and_charts(self):
        source = _rich_comparison_source("configuration_comparison")
        source["data"]["labels"] = {
            "airports": {
                "AP1": "Nanjing Lukou International Airport",
                "AP2": "Sunan Shuofang International Airport",
            },
            "airport_short_names": {"AP1": "NKG", "AP2": "WUX"},
            "missions": {},
            "aircraft": {},
        }

        report = build_pdf_report_model(source)

        self.assertEqual(["NKG", "WUX"], [row[0] for row in report["airport_rows"]])
        airport_chart = next(chart for chart in report["charts"] if chart["id"] == "airport_departures")
        self.assertEqual(["NKG", "WUX"], airport_chart["categories"])

    def test_all_comparison_report_kinds_render_chart_rich_pdfs(self):
        exporter = ResultExportService()
        for kind in ("damage_comparison", "scenario_comparison", "configuration_comparison"):
            with self.subTest(kind=kind):
                rendered = exporter.render_pdf(_rich_comparison_source(kind))
                self.assertTrue(rendered.content.startswith(b"%PDF-"))
                self.assertGreaterEqual(len(re.findall(rb"/Type\s*/Page\b", rendered.content)), 3)


class ResultExportServiceTests(unittest.TestCase):
    def test_pdf_visible_system_name_and_bar_value_label_formats_are_canonical(self):
        self.assertEqual("机场群顽存能力模拟仿真验证系统", PDF_SYSTEM_NAME)
        self.assertEqual("68.96%", _chart_value_label(0.6896, "percent"))
        self.assertEqual("7", _chart_value_label(7.0, "integer"))
        self.assertEqual("2.5", _chart_value_label(2.5, "integer"))
        self.assertEqual("", _chart_value_label(0, "integer"))
        self.assertEqual("", _chart_value_label(None, "percent"))

    def test_stacked_bar_labels_only_show_nonzero_column_totals(self):
        self.assertEqual(
            [["", "", ""], ["", "", ""], ["6", "", "4.5"]],
            _stacked_bar_label_array(
                [[1, 0, 2], [2, 0, 1], [3, 0, 1.5]],
                "integer",
            ),
        )

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
        report = build_pdf_report_model(self.source)
        chart_ids = {chart["id"] for chart in report["charts"]}
        self.assertIn("departure_timeline", chart_ids)
        self.assertIn("task_fulfillment", chart_ids)
        self.assertIn("airport_departures", chart_ids)
        self.assertIn("aircraft_composition", chart_ids)
        self.assertIn("resource_minimum_timeline", chart_ids)
        self.assertIn("aircraft_availability", chart_ids)
        technical = self.source["data"]["metrics"]["technical"]
        objective_index = report["summary_headers"].index("目标函数 J")
        self.assertEqual(technical["objective"], report["run_summary_rows"][0][objective_index])
        self.assertEqual(
            {name: technical[key] for name, key in (("F1", "f1"), ("F2", "f2"), ("F3", "f3"), ("J", "objective"))},
            dict(report["objective_value_rows"]),
        )

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


if __name__ == "__main__":
    unittest.main()
