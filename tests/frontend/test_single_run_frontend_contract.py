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
    def test_explainability_layout_has_three_levels_without_duplicate_sortie_cards(self):
        for label in ("结果概览", "任务与调度", "保障状态"):
            self.assertIn(label, HTML)
        for field in (
            "required_sorties_total", "fulfilled_sorties_total", "unmet_sorties_total",
            "additional_sorties_total", "participating_airport_count",
        ):
            self.assertIn(field, JS)
        self.assertNotIn("出动情况", HTML)

    def test_task_aircraft_matrix_exposes_fulfilled_shortfall_and_additional(self):
        for token in (
            'id="taskFulfillmentMatrix"', "renderTaskFulfillmentMatrix",
            "required_by_aircraft", "fulfilled_by_aircraft", "unmet_by_aircraft",
            "additional_by_aircraft", "by_origin_airport",
        ):
            self.assertIn(token, HTML + JS)

    def test_support_drilldown_uses_specific_airport_resource_and_aircraft(self):
        for token in (
            'id="supportAirportSelect"', 'id="supportResourceSelect"',
            'id="supportAircraftSelect"', 'id="capacityHeatmap"',
            'id="resourceInventoryChart"', 'id="aircraftTurnaroundChart"',
            "damage_adjusted_base_boundary", "permanent_loss", "replenishment_actual",
            "consumed_increment", "available_before_departure", "ready_releases", "in_use",
        ):
            self.assertIn(token, HTML + JS)
        self.assertIn("动态最低包络", HTML + JS)

    def test_complete_chain_table_keeps_real_business_tuple(self):
        for token in (
            'id="chainTableBody"', "renderChainTable", "origin_airport_id", "mission_id",
            "return_airport_id", "aircraft_type", "depart_window", "return_window",
            "ready_window", "sorties",
        ):
            self.assertIn(token, HTML + JS)
        self.assertNotIn("aggregateTaskFlows", JS)

    def test_solver_quality_distinguishes_recorded_bounds_and_objective_components(self):
        for token in (
            'id="solverFacts"', "best_bound", "gap", "solve_time_s", "cluster_lp_objective",
            "f1", "f2", "f3", "未记录", "可行目标（下界）", "对偶界（上界）",
            "不是五维综合评价", "当前调度结果缺口，并非独立证明的物理最小缺口",
        ):
            self.assertIn(token, HTML + JS)

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

    def test_three_level_body_uses_distinct_business_questions(self):
        for label in (
            "结果概览", "组群与参与机场", "求解事实", "任务×机型需求满足",
            "出动与返航时序", "完整航链", "逐窗容量利用率", "库存阶梯与事件", "航空器周转",
        ):
            self.assertIn(label, HTML)

    def test_timeline_modes_do_not_use_top_n(self):
        for mode in ('data-mode="all"', 'data-mode="airport"', 'data-mode="mission"', 'data-mode="aircraft"'):
            self.assertIn(mode, HTML)
        self.assertIn("timelineKeys", JS)
        self.assertNotIn("topN", JS)
        self.assertNotIn("Top N", JS)
        self.assertNotIn("TopN", JS)
        self.assertNotRegex(JS, r"\.slice\(\s*0\s*,\s*\d+")

    def test_resource_chart_uses_specific_resource_series_not_category_envelope(self):
        for field in ("remaining", "replenishment_actual", "consumed_increment", "permanent_loss"):
            self.assertIn(field, JS)
        self.assertNotIn("category_min_remaining_ratio_timeline", JS)
        self.assertIn("动态最低包络", HTML)

    def test_single_run_uses_canonical_fulfilled_unmet_and_additional_facts(self):
        for field in (
            "required_sorties_total", "fulfilled_sorties_total", "unmet_sorties_total",
            "additional_sorties_total", "required_by_aircraft", "fulfilled_by_aircraft",
            "unmet_by_aircraft", "additional_by_aircraft",
        ):
            self.assertIn(field, JS)
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

    def test_object_views_keep_full_scrollable_data_without_topn(self):
        self.assertIn("Object.entries(tasks)", JS)
        self.assertIn("Object.keys(state.metrics.airports", JS)
        self.assertIn("Object.keys(state.metrics.aircraft_inventory", JS)
        self.assertIn("overflow:auto", CSS)

    def test_chain_matrix_uses_complete_solution_rows_without_false_aggregation(self):
        self.assertIn("完整航链", HTML)
        self.assertIn("state.solution.sortie_chains", JS)
        for field in (
            "origin_airport_id", "mission_id", "return_airport_id", "aircraft_type",
            "depart_window", "return_window", "ready_window", "sorties",
        ):
            self.assertIn(field, JS)
        self.assertIn("renderChainTable", JS)
        self.assertNotIn("aggregateTaskFlows", JS)
        self.assertNotIn("operations", JS)

    def test_timeline_has_nearest_window_guide_and_tooltip(self):
        for token in ("nearestWindowIndex", "chart-hover-line", "chart-hover-tooltip", "pointermove", "出动", "返航"):
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

    def test_linked_support_selectors_replace_the_old_generic_detail_dock(self):
        for element_id in ("supportAirportSelect", "supportResourceSelect", "supportAircraftSelect"):
            self.assertIn(f'id="{element_id}"', HTML)
        self.assertNotIn("detail-dock", HTML)

    def test_scheduling_and_support_panels_have_stable_regions(self):
        for token in (
            'id="taskFulfillmentMatrix"', 'id="timelineChart"', 'id="chainTableBody"',
            'id="capacityHeatmap"', 'id="resourceInventoryChart"', 'id="aircraftTurnaroundChart"',
        ):
            self.assertIn(token, HTML)
        self.assertIn("overflow:hidden", CSS)

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
