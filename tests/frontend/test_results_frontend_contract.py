from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "frontend/static/js/modules/results.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/templates/pages/results.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/static/css/results.css").read_text(encoding="utf-8")
UI = (ROOT / "backend/web/flask_ui.py").read_text(encoding="utf-8")
FLASK_RESULTS = (ROOT / "backend/web/flask_results.py").read_text(encoding="utf-8")
BASE = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")


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

    def test_chart_supports_departures_and_returns_for_every_payload_shape(self):
        self.assertIn('data-series-kind="departures"', HTML)
        self.assertIn('data-series-kind="returns"', HTML)
        self.assertIn("seriesKind: 'departures'", JS)
        self.assertIn("t[state.seriesKind]?.[role]", JS)
        self.assertIn("t[state.seriesKind]?.[id]?.values", JS)
        self.assertIn("t[state.seriesKind]?.[id]", JS)
        self.assertIn("row[id]?.[state.seriesKind]", JS)

    def test_chart_validation_preserves_zero_and_rejects_invalid_series(self):
        normalize = extract_function(JS, "normalizeChartValue")
        validate = extract_function(JS, "validateChartData")
        script = f"""
const reported = [];
console.error = (...args) => reported.push(args);
{normalize}
{validate}
const context = {{ workspace: 'damage', series: 'departures' }};
const valid = validateChartData([0, 1], [{{ id: 'R0', values: [0, 2] }}], context);
const nullValue = validateChartData([0], [{{ id: 'R0', values: [null] }}], context);
const badString = validateChartData([0], [{{ id: 'R0', values: ['bad'] }}], context);
const mismatch = validateChartData([0, 1], [{{ id: 'R0', values: [1] }}], context);
process.stdout.write(JSON.stringify({{
  valid: valid.ok,
  zero: valid.series[0].values[0],
  nullValue: {{ ok: nullValue.ok, message: nullValue.message }},
  badString: {{ ok: badString.ok, message: badString.message }},
  mismatch: {{ ok: mismatch.ok, message: mismatch.message }},
  reports: reported.length,
}}));
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
        self.assertTrue(result["valid"])
        self.assertEqual(0, result["zero"])
        self.assertFalse(result["nullValue"]["ok"])
        self.assertFalse(result["badString"]["ok"])
        self.assertEqual("时序数据无效，无法绘制比较图表。", result["nullValue"]["message"])
        self.assertFalse(result["mismatch"]["ok"])
        self.assertEqual("比较时序轴不一致，无法绘制。", result["mismatch"]["message"])
        self.assertEqual(3, result["reports"])
        self.assertNotIn("Number(v)||0", JS.replace(" ", ""))
        self.assertNotIn("Number(v||0)", JS.replace(" ", ""))

    def test_condition_drawer_search_and_single_run_drilldown_are_present(self):
        self.assertIn('id="resultsRunSearch"', HTML)
        self.assertIn("runMatchesSearch", JS)
        self.assertIn("/runs/${encodeURIComponent(id)}", JS)
        self.assertIn("查看单次结果", JS)
        self.assertIn("尚未选择比较条件", HTML)
        self.assertIn("选择比较条件", HTML)

    def test_workspace_state_and_export_stay_scoped_to_current_comparison(self):
        self.assertIn("const requestWorkspace=state.workspace", JS)
        self.assertGreaterEqual(JS.count("if(state.workspace!==requestWorkspace)return"), 2)
        self.assertGreaterEqual(JS.count("const requestDraft=state.draft"), 2)
        self.assertGreaterEqual(JS.count("state.draft!==requestDraft"), 2)
        self.assertIn("state.seriesKind='departures'", JS)
        self.assertIn("state.draft=createDraft()", JS)
        self.assertIn("permissions?.includes('results.export')", JS)
        self.assertIn("/api/results/export-file", JS)

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
