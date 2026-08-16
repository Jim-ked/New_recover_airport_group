from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "frontend/static/js/modules/single-run.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/templates/pages/single_run.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/static/css/single-run.css").read_text(encoding="utf-8")
UI = (ROOT / "backend/web/flask_ui.py").read_text(encoding="utf-8")


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
        for label in ("任务调度时序", "组群与航链结构", "资源余量时序", "全机场承接", "任务调度结构", "机型投入结构", "方案关注"):
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

    def test_frontend_does_not_restore_legacy_success_metrics_or_comparison_formulas(self):
        for token in ("completion_ratio", "shortfall", "unmet", "best_run", "R1-R0", "R2-R1"):
            self.assertNotIn(token, JS)
            self.assertNotIn(token, HTML)

    def test_concentration_is_raw_hhi_without_frontend_grade(self):
        self.assertIn("departure_hhi", JS)
        for grade in ("高集中", "中集中", "低集中", "concentration_grade"):
            self.assertNotIn(grade, JS)

    def test_attention_rules_remain_explicitly_unconfigured(self):
        self.assertIn("未配置自动关注规则", HTML)
        self.assertIn("不根据资源比例、集中度或峰值自行生成阈值告警", HTML)
        for token in ("<20%", "threshold", "告警阈值"):
            self.assertNotIn(token, JS)

    def test_all_airports_tasks_and_aircraft_have_scrollable_full_tables(self):
        self.assertIn("Object.entries(state.metrics.airports", JS)
        self.assertIn("Object.entries(state.metrics.tasks", JS)
        self.assertIn("Object.entries(state.metrics.aircraft", JS)
        self.assertIn("overflow:auto", CSS)

    def test_spatial_layout_uses_snapshot_coordinates_and_solution_complete_chains(self):
        self.assertIn("非比例示意", HTML)
        self.assertIn("完整 GIS 在运行态势页展示", JS)
        self.assertIn("state.situation?.airports", JS)
        self.assertIn("state.situation?.missions", JS)
        self.assertIn("state.solution.sortie_chains", JS)
        for field in ("origin_airport_id", "mission_id", "return_airport_id", "path_id"):
            self.assertIn(field, JS)
        self.assertNotIn("operations", JS)

    def test_unified_detail_dock_has_all_frozen_tabs(self):
        for label in ("机场承接", "任务调度", "机型投入", "资源保障", "技术信息"):
            self.assertIn(label, HTML)
        self.assertIn("detail-dock", HTML)
        self.assertIn("position:fixed", CSS)

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
