from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "frontend/static/js/modules/results.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/templates/pages/results.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/static/css/results.css").read_text(encoding="utf-8")
UI = (ROOT / "backend/web/flask_ui.py").read_text(encoding="utf-8")
FLASK_RESULTS = (ROOT / "backend/web/flask_results.py").read_text(encoding="utf-8")
BASE = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")


class ResultsFrontendContractTests(unittest.TestCase):
    def test_three_workspaces_and_overlay_are_real(self):
        for label in ("损毁影响与优化效果", "多场景比较", "方案配置比较", "修改比较条件"):
            self.assertIn(label, HTML)
        self.assertIn('@bp.get("/results")', UI)
        self.assertIn("ui_v1.results_page", BASE)

    def test_frontend_consumes_only_canonical_comparison_endpoints(self):
        for endpoint in (
            "/api/results/damage-candidates",
            "/api/results/damage-comparison",
            "/api/results/scenario-comparison",
            "/api/results/config-comparison",
            "/api/results/comparable-runs",
        ):
            self.assertIn(endpoint, JS)
        for forbidden in ("/api/results/summary", "/api/results/compare", "/api/results/run_detail", "/api/scenes", "scene_file"):
            self.assertNotIn(forbidden, JS)

    def test_damage_roles_are_selected_from_backend_approved_triples(self):
        self.assertIn("damageCandidates", JS)
        self.assertIn("后端已验证的 R0 / R1 / R2 组合", JS)
        self.assertIn('@bp.get("/results/damage-candidates")', FLASK_RESULTS)
        self.assertNotIn("findBest", JS)
        self.assertNotIn("autoBest", JS)

    def test_main_chart_supports_all_airport_mission_aircraft_without_topn(self):
        for mode in ('data-chart-mode="all"', 'data-chart-mode="airport"', 'data-chart-mode="mission"', 'data-chart-mode="aircraft"'):
            self.assertIn(mode, HTML)
        self.assertIn("by_airport", JS)
        self.assertIn("by_mission", JS)
        self.assertIn("by_aircraft", JS)
        for forbidden in ("TopN", "topN", "slice(0,", "其他机场"):
            self.assertNotIn(forbidden, JS)

    def test_bottom_tabs_keep_full_airport_resource_and_scheme_views(self):
        for label in ("全机场承接", "资源变化", "方案结构", "出动架次", "累计承接占比"):
            self.assertIn(label, HTML)
        self.assertIn("Object.keys(state.payload.airports", JS)

    def test_no_completion_shortfall_or_frontend_r0_delta_formula(self):
        for forbidden in ("completion_ratio", "shortfall", "unmet", "R1-R0", "R2-R1"):
            self.assertNotIn(forbidden, JS)
        self.assertIn("difference_overview", JS)
        self.assertIn("summary_deltas_vs_baseline", JS)
        self.assertIn("row.departure_share", JS)
        self.assertNotIn("scheduled_sorties_total||{})?.[role]", JS)

    def test_visual_geometry_keeps_main_compare_and_overlay(self):
        self.assertIn("grid-template-columns:minmax(0,1.65fr) minmax(320px,390px)", CSS)
        self.assertIn("overflow:auto", CSS)
        self.assertIn("position:fixed", CSS)
        self.assertIn("width:min(500px", CSS)
        self.assertIn("results-chart-tooltip", CSS)
        self.assertIn("results-hover-line", JS)


if __name__ == "__main__":
    unittest.main()
