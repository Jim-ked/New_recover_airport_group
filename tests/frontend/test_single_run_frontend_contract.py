from __future__ import annotations

import unittest
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "frontend/static/js/modules/single-run.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/templates/pages/single_run.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/static/css/single-run.css").read_text(encoding="utf-8")
UI = (ROOT / "backend/web/flask_ui.py").read_text(encoding="utf-8")


def extract_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    body_start = source.index("{", start)
    depth = 0
    for index in range(body_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"Unable to extract function {name}")


class SingleRunFrontendContractTests(unittest.TestCase):
    def test_page_reads_only_canonical_run_facts(self):
        required = (
            "/api/runs/${encodeURIComponent(state.runId)}",
            "/situation`",
            "/solution`",
            "/metrics`",
        )
        for token in required:
            self.assertIn(token, JS)
        for forbidden in ("/api/results/summary", "/api/runtime", "/api/scenes", "scene_file", "result_root"):
            self.assertNotIn(forbidden, JS)

    def test_single_run_rejects_non_succeeded_run(self):
        self.assertIn("run.status !== 'succeeded'", JS)
        self.assertIn("单次运行仪表盘仅支持成功 Run", JS)

    def test_five_summary_cards_and_frozen_body_structure_exist(self):
        for label in ("最终组群", "任务规模", "出动情况", "机场协同", "资源保障"):
            self.assertIn(label, HTML)
        for label in ("任务调度时序", "组群任务流结构", "资源余量时序", "全机场承接", "任务调度结构", "机型投入结构", "技术信息"):
            self.assertIn(label, HTML)

    def test_timeline_modes_do_not_use_top_n(self):
        for mode in ('data-mode="all"', 'data-mode="airport"', 'data-mode="mission"', 'data-mode="aircraft"'):
            self.assertIn(mode, HTML)
        self.assertIn("timelineKeys", JS)
        self.assertNotIn("topN", JS)
        self.assertNotIn("Top N", JS)
        self.assertNotIn("TopN", JS)
        self.assertNotRegex(JS, r"\.slice\(\s*0\s*,\s*\d+")

    def test_resource_timeline_consumes_backend_category_series(self):
        self.assertIn("category_min_remaining_ratio_timeline", JS)
        self.assertIn("category_min_remaining_ratio", JS)
        self.assertNotIn("remaining_ratio_initial.reduce", JS)
        self.assertNotIn("resource_types.reduce", JS)

    def test_single_run_uses_only_canonical_task_count_required_and_scheduled_facts(self):
        for label in ("`任务 ${integer", "`需求 ${integer", "调度 ${integer"):
            self.assertIn(label, JS + HTML)
        for field in ("mission_count", "required_sorties_total", "scheduled_sorties_total"):
            self.assertIn(field, JS)
        for forbidden in ("需求内已执行", "未执行", "额外出动", "任务完成率"):
            self.assertNotIn(forbidden, JS + HTML)
        for token in ("best_run", "R1-R0", "R2-R1"):
            self.assertNotIn(token, JS)
            self.assertNotIn(token, HTML)

    def test_single_run_rejects_timeline_length_mismatch_without_padding_zero(self):
        validate = extract_function(JS, "validateTimelineSeries")
        script = f"""
{validate}
const valid = validateTimelineSeries([0, 1], [{{label:'出动', values:[0, 2]}}]);
const mismatch = validateTimelineSeries([0, 1], [{{label:'出动', values:[1]}}]);
process.stdout.write(JSON.stringify({{valid, mismatch}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)
        self.assertTrue(result["valid"]["ok"])
        self.assertEqual(0, result["valid"]["series"][0]["values"][0])
        self.assertFalse(result["mismatch"]["ok"])
        self.assertEqual("时序长度与时间窗不一致。", result["mismatch"]["message"])
        self.assertNotIn("values.push(0)", JS)

    def test_concentration_is_raw_hhi_without_frontend_grade(self):
        self.assertIn("departure_hhi", JS)
        for grade in ("高集中", "中集中", "低集中", "concentration_grade"):
            self.assertNotIn(grade, JS)

    def test_no_frontend_threshold_alert_is_invented(self):
        self.assertNotIn("方案关注", HTML)
        for token in ("<20%", "threshold", "告警阈值"):
            self.assertNotIn(token, JS)

    def test_all_airports_tasks_and_aircraft_have_scrollable_full_tables(self):
        self.assertIn("Object.entries(state.metrics.airports", JS)
        self.assertIn("Object.entries(state.metrics.tasks", JS)
        self.assertIn("Object.entries(state.metrics.aircraft", JS)
        self.assertIn("overflow:auto", CSS)

    def test_structure_flow_uses_three_columns_aggregated_sorties_and_hover(self):
        self.assertIn("组群任务流结构", HTML)
        self.assertIn("state.solution.sortie_chains", JS)
        for field in ("origin_airport_id", "mission_id", "return_airport_id", "sorties"):
            self.assertIn(field, JS)
        for token in (
            "aggregateTaskFlows",
            "flow-edge",
            "flow-node flow-${kind}",
            "appendNode('origin'",
            "appendNode('mission'",
            "appendNode('return'",
            "Math.sqrt",
            "pointerenter",
            "pointermove",
            "click",
        ):
            self.assertIn(token, JS)
        structure = extract_function(JS, "renderSpatial")
        self.assertNotIn("longitude", structure)
        self.assertNotIn("latitude", structure)
        self.assertNotIn("operations", JS)
        aggregate = extract_function(JS, "aggregateTaskFlows")
        script = f"""
{aggregate}
const flows=aggregateTaskFlows([
  {{origin_airport_id:'AP001',mission_id:'M1',return_airport_id:'AP002',aircraft_type:'fighter',sorties:2}},
  {{origin_airport_id:'AP001',mission_id:'M1',return_airport_id:'AP002',aircraft_type:'bomber',sorties:3}},
]);
process.stdout.write(JSON.stringify({{outbound:flows.outbound[0].sorties,inbound:flows.inbound[0].sorties,aircraft:[...flows.outbound[0].aircraft]}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        result = json.loads(completed.stdout)
        self.assertEqual(5, result["outbound"])
        self.assertEqual(5, result["inbound"])
        self.assertEqual([["fighter", 2], ["bomber", 3]], result["aircraft"])

    def test_timeline_has_nearest_window_guide_points_and_tooltip(self):
        for token in ("nearestWindowIndex", "chart-hover-line", "chart-hover-point", "pointermove", "出动", "返航"):
            self.assertIn(token, JS + CSS)
        nearest = extract_function(JS, "nearestWindowIndex")
        script = f"""
{nearest}
process.stdout.write(JSON.stringify([
  nearestWindowIndex(10, {{left:0,width:100}}, 5, 10, 100),
  nearestWindowIndex(50, {{left:0,width:100}}, 5, 10, 100),
  nearestWindowIndex(90, {{left:0,width:100}}, 5, 10, 100),
]));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual([0, 2, 4], json.loads(completed.stdout))

    def test_unified_detail_dock_has_all_frozen_tabs(self):
        for label in ("机场承接", "任务调度", "机型投入", "资源保障", "技术信息"):
            self.assertIn(label, HTML)
        self.assertIn("detail-dock", HTML)
        self.assertIn("position:fixed", CSS)

    def test_auxiliary_and_bottom_workspaces_show_one_panel_at_a_time(self):
        for token in (
            'id="singleAuxTabs"', 'data-aux-mode="spatial"', 'data-aux-mode="resource"',
            'id="singleBottomTabs"', 'data-bottom-mode="airports"',
            'data-bottom-mode="missions"', 'data-bottom-mode="aircraft"',
            'data-bottom-mode="technical"',
        ):
            self.assertIn(token, HTML)
        self.assertIn("singleAuxMode", JS)
        self.assertIn("singleBottomMode", JS)
        self.assertIn("overflow:hidden", CSS)
        self.assertIn("single-bottom-view", CSS)

    def test_ui_route_is_run_id_addressable(self):
        self.assertIn('@bp.get("/runs/<run_id>")', UI)
        self.assertIn('render_template(', UI)
        self.assertIn('"pages/single_run.html"', UI)

    def test_gis_runtime_button_routes_to_real_runtime_page(self):
        self.assertIn('id="openRuntimeButton"', HTML)
        self.assertNotIn("GIS Runtime 将在下一切片接入", HTML)
        self.assertIn('window.location.href = `/runs/${encodeURIComponent(state.runId)}/runtime`', JS)


if __name__ == "__main__":
    unittest.main()
