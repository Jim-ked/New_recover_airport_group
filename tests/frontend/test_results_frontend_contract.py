from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "frontend/static/js/modules/results.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/templates/pages/results.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/static/css/results.css").read_text(encoding="utf-8")


def extract_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    body_start = source.index(") {", start) + 2
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
    def test_workspace_has_three_independent_user_facing_modes(self):
        for mode, label in (("damage", "损毁与组群"), ("multi", "多损毁"), ("configuration", "方案配置")):
            self.assertIn(f'data-workspace="{mode}"', HTML)
            self.assertIn(label, HTML)
            self.assertIn(f"{mode}: createWorkspaceState()", JS)
        self.assertNotIn('data-workspace="exploratory"', HTML)
        self.assertNotIn("跨条件探索", HTML)
        self.assertNotIn("exploratory-comparison", JS)

    def test_damage_mode_keeps_both_stage_summaries_and_one_active_focus(self):
        self.assertIn('id="damageStageSummary"', HTML)
        self.assertIn('data-damage-stage="damage"', HTML)
        self.assertIn('data-damage-stage="cluster"', HTML)
        render = extract_function(JS, "renderDamageStageSummary")
        self.assertIn("payload.comparisons", render)
        self.assertIn(".stage-facts", render)
        self.assertIn("R0→R1", HTML)
        self.assertIn("R1→R2", HTML)
        self.assertIn("损毁影响", HTML)
        self.assertIn("组群调整", HTML)
        self.assertIn("activeDamageStage", JS)

    def test_multi_keeps_all_run_overview_and_focus_pair(self):
        self.assertIn('id="multiOverview"', HTML)
        self.assertIn('id="focusBaselineRun"', HTML)
        self.assertIn('id="focusComparisonRun"', HTML)
        render = extract_function(JS, "renderMultiOverview")
        self.assertIn("payload.run_ids", render)
        self.assertIn("run_summaries", render)
        self.assertIn("objective", render)
        self.assertIn("multi-overview-grid", render)
        request = extract_function(JS, "requestComparison")
        self.assertIn("baseline_run_id", request)
        self.assertIn("comparison_run_id", request)

    def test_run_identity_uses_short_mode_names_and_keeps_full_id_in_title(self):
        identity = extract_function(JS, "runIdentity")
        for token in ("payload?.roles", "D${index}", "基准方案", "方案${alternativeIndex + 2}"):
            self.assertIn(token, identity)
        self.assertIn("damage_scenario?.name", identity)
        self.assertIn("cluster_enabled", identity)
        condition = extract_function(JS, "renderCondition")
        self.assertIn('class="run-identity"', condition)
        self.assertIn('title="${esc(id)}"', condition)
        self.assertIn("selectionLabel", condition)
        self.assertIn("refs.focusBaseline.title", condition)
        self.assertIn("refs.focusComparison.title", condition)

    def test_damage_stage_summary_prioritizes_changes_and_preserves_display_states(self):
        summary = extract_function(JS, "stageSummary")
        compact = extract_function(JS, "compactStageFacts")
        for token in ("fulfilled_sorties_total", "unmet_sorties_total", "scheduled_sorties_total", "objective_components?.J"):
            self.assertIn(token, compact)
        self.assertIn("compactStageFacts", summary)
        self.assertIn("stage-metrics", summary)
        self.assertIn("stage-heading", HTML)
        script = f"""
{extract_function(JS, "number")}
{compact}
const base = {{
  values: {{
    fulfilled_sorties_total: {{delta: 0}},
    unmet_sorties_total: {{delta: 0}},
    scheduled_sorties_total: {{delta: 0}},
  }},
  objective_components: {{J: {{recorded: true, definition_comparable: true, delta: 0}}}},
}};
const changed = structuredClone(base);
changed.values.scheduled_sorties_total.delta = 18;
changed.objective_components.J.delta = 133.141;
const missing = structuredClone(base);
missing.values.fulfilled_sorties_total.delta = null;
missing.objective_components.J = {{recorded: true, definition_comparable: false, delta: null}};
const unrecorded = structuredClone(base);
unrecorded.objective_components.J = {{recorded: false, definition_comparable: true, delta: null}};
process.stdout.write(JSON.stringify({{
  zero: compactStageFacts(base),
  changed: compactStageFacts(changed),
  missing: compactStageFacts(missing),
  unrecorded: compactStageFacts(unrecorded),
}}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        facts = json.loads(result.stdout)
        self.assertIn("履行、缺口、出动均未变化", [item["text"] for item in facts["zero"]])
        self.assertEqual("出动 +18", facts["changed"][0]["text"])
        self.assertEqual("J +133.14", facts["changed"][1]["text"])
        self.assertNotIn("履行、缺口、出动均未变化", [item["text"] for item in facts["missing"]])
        self.assertIn("履行 未记录", [item["text"] for item in facts["missing"]])
        self.assertIn("J 不可比", [item["text"] for item in facts["missing"]])
        self.assertIn("J 未记录", [item["text"] for item in facts["unrecorded"]])

    def test_configuration_mode_renders_only_key_configuration_differences(self):
        self.assertIn('id="configurationDifferences"', HTML)
        render = extract_function(JS, "renderConfigurationDifferences")
        self.assertIn("key_configuration_differences", render)
        for field in ("cluster_enabled", "cluster_size", "core_airports", "preference_mode", "alpha"):
            self.assertIn(field, JS)

    def test_one_chart_switches_event_and_observation_without_duplicate_charts(self):
        self.assertEqual(1, HTML.count('id="resultsChart"'))
        for token in ('data-series-kind="departures"', 'data-series-kind="returns"', 'data-observation="trend"', 'data-observation="delta"', 'data-observation="cumulative"', 'id="resultsTimeNavigation"'):
            self.assertIn(token, HTML)
        chart = extract_function(JS, "renderChart")
        self.assertIn("currentFocus()", chart)
        self.assertIn("focus_comparison", extract_function(JS, "currentFocus"))
        self.assertIn("cumulative", extract_function(JS, "timelineSeries"))
        self.assertIn("visibleWindowCount", chart)
        self.assertNotIn("run_ids.slice(0,", JS)

    def test_chart_legend_uses_short_labels_independent_from_condition_identity(self):
        legend = extract_function(JS, "legendLabel")
        for token in ("R0 基准", "R1 损毁", "R2 组群", "D${index}", "基准"):
            self.assertIn(token, legend)
        timeline = extract_function(JS, "timelineSeries")
        self.assertIn("legendLabel", timeline)
        self.assertNotIn("runLabel", timeline)

    def test_precise_result_table_includes_solver_and_objective_components(self):
        self.assertIn('id="resultsValueComparison"', HTML)
        render = extract_function(JS, "renderValueComparison")
        for token in ("任务需求", "实际履行", "任务缺口", "额外调度", "F1", "F2", "F3", "J"):
            self.assertIn(token, render)
        self.assertIn("solverStatusSummary", render)
        self.assertNotIn("非最优终止时为已取得可行值", render)
        self.assertIn("definition_comparable", render)
        self.assertIn("未记录", JS)

    def test_solver_status_is_short_with_detail_in_title(self):
        status = extract_function(JS, "solverStatusSummary")
        script = f"""
{extract_function(JS, "number")}
{status}
process.stdout.write(JSON.stringify([
  solverStatusSummary({{solver_status:'optimal', gap:0}}),
  solverStatusSummary({{solver_status:'gaplimit', gap:0.0041}}),
  solverStatusSummary({{solver_status:'timelimit', gap:null}}),
  solverStatusSummary({{}}),
]));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        values = json.loads(result.stdout)
        self.assertEqual("已证最优", values[0]["text"])
        self.assertEqual("可行解，未证最优", values[1]["text"])
        self.assertIn("Gap", values[1]["detail"])
        self.assertEqual("可行解，未证最优", values[2]["text"])
        self.assertEqual("求解状态未记录", values[3]["text"])

    def test_chart_subtitle_is_contextual_not_a_permanent_defensive_explanation(self):
        self.assertNotIn("完整时间范围累计，不受可视窗口影响", HTML + JS)
        chart = extract_function(JS, "renderChart")
        self.assertIn("refs.chartSubtitle", chart)
        self.assertIn("完整正式时间范围", chart)

    def test_lower_area_has_exactly_three_summary_themes_and_no_object_drilldown(self):
        for theme, label in (("tasks", "任务与机型"), ("airports", "机场与协同"), ("assurance", "运行保障")):
            self.assertIn(f'data-analysis-theme="{theme}"', HTML)
            self.assertIn(label, HTML)
        self.assertNotIn("对象级下钻", HTML)
        self.assertNotIn("object-comparison", JS)
        self.assertIn("renderAssuranceTheme", extract_function(JS, "renderTheme"))

    def test_task_theme_adds_one_inline_positive_negative_contribution_view(self):
        render = extract_function(JS, "renderTaskTheme")
        self.assertIn("structural_changes?.tasks", render)
        self.assertIn("differenceBar", render)
        self.assertIn("difference-bar", CSS)
        self.assertNotIn('id="resultsChart2"', HTML)

    def test_conclusions_are_backend_bound_and_limited_to_two(self):
        render = extract_function(JS, "renderConclusions")
        self.assertIn("slice(0, 2)", render)
        self.assertIn("focus?.conclusions", render)
        self.assertNotIn("因为", render)

    def test_export_preserves_current_mode_and_focus_relation(self):
        export_body = extract_function(JS, "exportRequestBody")
        for token in ("damage_comparison", "scenario_comparison", "configuration_comparison"):
            self.assertIn(token, export_body)
        self.assertIn("baseline_run_id", export_body)
        self.assertIn("comparison_run_id", export_body)
        self.assertIn("/api/results/export-file", JS)
        for fmt in ("pdf", "csv", "json", "xml"):
            self.assertIn(f'data-export-format="{fmt}"', HTML)

    def test_missing_zero_and_not_comparable_are_distinct(self):
        function = extract_function(JS, "displayValue")
        script = f"""
{function}
process.stdout.write(JSON.stringify([
  displayValue(null), displayValue(0), displayValue(4, {{ comparable:false }}),
  displayValue(null, {{ notApplicable:true }})
]));
"""
        result = subprocess.run(["node", "--input-type=module", "--eval", script], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(["未记录", "0", "不可直接比较", "不适用"], json.loads(result.stdout))

    def test_responsive_geometry_keeps_primary_analysis_in_first_view(self):
        self.assertIn("grid-template-columns:minmax(0,1.65fr) minmax(300px,.75fr)", CSS)
        self.assertIn("@media(max-width:1366px)", CSS)
        self.assertIn("@media(max-height:800px)", CSS)
        self.assertIn("@media(max-width:1100px)", CSS)
        self.assertIn("overflow-x:auto", CSS)
        self.assertIn("max-height", CSS)

    def test_workspace_persistence_never_serializes_payload(self):
        saved = extract_function(JS, "buildSessionState")
        self.assertIn("selection", saved)
        self.assertIn("activeDamageStage", saved)
        self.assertIn("observation", saved)
        self.assertNotIn("payload", saved)
        self.assertIn("sessionStorage", JS)


if __name__ == "__main__":
    unittest.main()
