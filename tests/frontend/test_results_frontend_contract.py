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
    def test_explicit_view_state_authority_keeps_workspace_shell_stable(self):
        for state_name in ("ZERO_RESULTS", "NO_COMPARABLE_SET", "READY_TO_SELECT", "HAS_COMPARISON"):
            self.assertIn(state_name, JS)
        self.assertIn("function deriveViewState", JS)
        self.assertIn("function renderViewState", JS)
        self.assertIn("function renderWorkbenchState", JS)
        for element_id in ("resultsMain", "resultsBottom", "resultsMetrics", "resultsConditionActions", "resultsExport"):
            self.assertIn(f'id="{element_id}"', HTML)
        self.assertNotIn('id="resultsMain" class="results-main hidden"', HTML)
        self.assertNotIn('id="resultsBottom" class="results-bottom hidden"', HTML)
        render = extract_function(JS, "renderViewState")
        self.assertNotIn("refs.main.classList.toggle('hidden'", render)
        self.assertNotIn("refs.bottom.classList.toggle('hidden'", render)

    def test_view_state_priority_uses_data_not_dom_state(self):
        derive = extract_function(JS, "deriveViewState")
        current = extract_function(JS, "currentCandidateState")
        script = f"""
const VIEW_STATE = {{ LOADING:'LOADING', ERROR:'ERROR', ZERO_RESULTS:'ZERO_RESULTS', NO_COMPARABLE_SET:'NO_COMPARABLE_SET', READY_TO_SELECT:'READY_TO_SELECT', HAS_COMPARISON:'HAS_COMPARISON' }};
const state = {{ workspace:'damage', runs:[], runsStatus:'loading', payload:null, candidates:{{damage:{{status:'idle',items:[]}}}} }};
{current}
{derive}
const values = [];
values.push(deriveViewState());
state.runsStatus = 'error'; values.push(deriveViewState());
state.runsStatus = 'ready'; values.push(deriveViewState());
state.runs = [{{run_id:'R1'}}]; state.candidates.damage.status = 'ready'; values.push(deriveViewState());
state.candidates.damage.items = [{{id:'candidate'}}]; values.push(deriveViewState());
state.payload = {{roles:{{}}}}; values.push(deriveViewState());
process.stdout.write(JSON.stringify(values));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            ["LOADING", "ERROR", "ZERO_RESULTS", "NO_COMPARABLE_SET", "READY_TO_SELECT", "HAS_COMPARISON"],
            json.loads(completed.stdout),
        )

    def test_view_capabilities_show_only_actions_meaningful_to_each_state(self):
        capabilities = extract_function(JS, "viewCapabilities")
        script = f"""
const VIEW_STATE = {{ LOADING:'LOADING', ERROR:'ERROR', ZERO_RESULTS:'ZERO_RESULTS', NO_COMPARABLE_SET:'NO_COMPARABLE_SET', READY_TO_SELECT:'READY_TO_SELECT', HAS_COMPARISON:'HAS_COMPARISON' }};
{capabilities}
const states = Object.values(VIEW_STATE);
const values = Object.fromEntries(states.map((name) => [name, viewCapabilities(name, true)]));
process.stdout.write(JSON.stringify(values));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        values = json.loads(completed.stdout)
        self.assertEqual(
            {"run": True, "rules": False, "select": False, "change": False, "export": False, "retry": False, "chart": False},
            values["ZERO_RESULTS"],
        )
        self.assertEqual(
            {"run": True, "rules": True, "select": False, "change": False, "export": False, "retry": False, "chart": False},
            values["NO_COMPARABLE_SET"],
        )
        self.assertTrue(values["READY_TO_SELECT"]["select"])
        self.assertFalse(values["READY_TO_SELECT"]["chart"])
        self.assertTrue(values["HAS_COMPARISON"]["change"])
        self.assertTrue(values["HAS_COMPARISON"]["export"])
        self.assertTrue(values["HAS_COMPARISON"]["chart"])
        self.assertTrue(values["ERROR"]["retry"])
        self.assertFalse(values["LOADING"]["chart"])

    def test_zero_results_short_circuits_candidate_loading_and_keeps_run_cta(self):
        load_initial = extract_function(JS, "loadInitial")
        self.assertIn("state.runs.length === 0", load_initial)
        self.assertLess(load_initial.index("state.runs.length === 0"), load_initial.index("ensureWorkspaceCandidates"))
        self.assertIn('id="resultsRunLink"', HTML)
        self.assertIn('href="/run"', HTML)
        self.assertNotIn("results-state-icon", JS)

    def test_empty_candidate_error_and_ready_states_are_distinct(self):
        self.assertIn("runsStatus: 'loading'", JS)
        self.assertIn("runsStatus === 'error'", JS)
        self.assertIn("candidate.status === 'error'", JS)
        self.assertIn("candidate.items.length === 0", JS)
        self.assertIn("openRules", JS)
        self.assertNotIn("renderEmpty();openOverlay()", JS.replace(" ", ""))

    def test_bottom_secondary_control_is_scoped_to_airports_and_export_to_payload(self):
        self.assertIn('id="resultsAirportValueControls"', HTML)
        render_bottom = extract_function(JS, "renderBottom")
        self.assertIn("deriveViewState() !== VIEW_STATE.HAS_COMPARISON", render_bottom)
        self.assertIn("state.bottomMode !== 'airports'", render_bottom)
        self.assertIn("state.payload=payload", JS.replace(" ", ""))
        self.assertIn("!state.payload", JS)

    def test_non_comparison_states_fill_the_stable_panels_without_data_controls(self):
        render = extract_function(JS, "renderWorkbenchState")
        for text in (
            "当前暂无可用比较结果",
            "请先完成相应的算法运行",
            "选择比较条件后显示时序比较",
            "等待比较结果",
            "等待有效比较条件",
            "暂无比较结果",
            "暂时无法读取结果数据",
        ):
            self.assertIn(text, render)
        self.assertIn("refs.legend.innerHTML=''", render.replace(" ", ""))
        self.assertNotIn("<svg", render)

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
        self.assertIn("/api/results/damage-candidates", JS)
        self.assertIn("r0_run_id", extract_function(JS, "renderDamageOverlay"))
        self.assertIn("r1_run_id", extract_function(JS, "renderDamageOverlay"))
        self.assertIn("r2_run_id", extract_function(JS, "renderDamageOverlay"))
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
        self.assertIn("尚未选择比较条件", JS)
        self.assertIn("选择比较条件", HTML)

    def test_default_workspace_uses_a_real_backend_approved_damage_candidate(self):
        for token in (
            "defaultDamageCandidate",
            "autoApplyDefaultComparison",
            "await ensureWorkspaceCandidates('damage')",
            "requestComparison('damage',selection)",
        ):
            self.assertIn(token, JS)
        self.assertNotIn("payload={", extract_function(JS, "autoApplyDefaultComparison").replace(" ", ""))

    def test_multi_and_configuration_workspaces_auto_apply_backend_approved_defaults(self):
        discover = extract_function(JS, "discoverComparableCandidates")
        auto = extract_function(JS, "autoApplyDefaultComparison")
        for token in (
            "rankedBaseRuns",
            "loadComparable(run.run_id,mode)",
            "items.length>=3",
        ):
            self.assertIn(token, discover.replace(" ", ""))
        for token in (
            "autoApplyWorkspaceDefault('damage')",
            "autoApplyWorkspaceDefault('multi')",
            "autoApplyWorkspaceDefault('configuration')",
        ):
            self.assertIn(token, auto.replace(" ", ""))
        self.assertIn("requestComparison('multi',selection)", JS.replace(" ", ""))
        self.assertIn("requestComparison('configuration',selection)", JS.replace(" ", ""))
        self.assertIn("damageView.selection?.r1_run_id", JS)
        self.assertIn("damageView.selection?.r2_run_id", JS)
        self.assertNotIn("RUN-00fa756e", JS)

    def test_default_base_ranking_follows_the_current_damage_workspace_without_ids(self):
        helpers = "\n".join(extract_function(JS, name) for name in (
            "runSituationId", "runCreatedAt", "rankedBaseRuns",
        ))
        script = f"""
const runs = [
  {{run_id:'HIGH-R2',created_at:'2026-08-19T10:03:00Z',situation:{{situation_id:'ST002'}},run_config:{{damage_scenario_id:'HIGH',cluster_enabled:true}}}},
  {{run_id:'LOW-R1',created_at:'2026-08-19T10:02:00Z',situation:{{situation_id:'ST002'}},run_config:{{damage_scenario_id:'LOW',cluster_enabled:false}}}},
  {{run_id:'R0',created_at:'2026-08-19T10:01:00Z',situation:{{situation_id:'ST002'}},run_config:{{damage_scenario_id:null,cluster_enabled:false}}}},
  {{run_id:'OTHER',created_at:'2026-08-19T10:04:00Z',situation:{{situation_id:'ST003'}},run_config:{{damage_scenario_id:null,cluster_enabled:false}}}},
];
const state = {{runs,runById:new Map(runs.map((run)=>[run.run_id,run])),workspaceStates:{{damage:{{selection:{{r0_run_id:'R0',r1_run_id:'LOW-R1',r2_run_id:'HIGH-R2'}}}}}}}};
{helpers}
process.stdout.write(JSON.stringify({{
  multi: rankedBaseRuns('multi').map((run)=>run.run_id),
  configuration: rankedBaseRuns('configuration').map((run)=>run.run_id),
}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        ranked = json.loads(completed.stdout)
        self.assertEqual("R0", ranked["multi"][0])
        self.assertEqual("LOW-R1", ranked["configuration"][0])

    def test_compact_empty_state_keeps_run_entry_point(self):
        self.assertIn('当前暂无可用比较结果', JS)
        self.assertIn('请先完成相应的算法运行', JS)
        self.assertIn('id="resultsRunLink"', HTML)

    def test_workspace_state_and_export_stay_scoped_to_current_comparison(self):
        self.assertIn("const requestWorkspace=state.workspace", JS)
        self.assertGreaterEqual(JS.count("if(state.workspace!==requestWorkspace)return"), 2)
        self.assertGreaterEqual(JS.count("const requestDraft=state.draft"), 2)
        self.assertGreaterEqual(JS.count("state.draft!==requestDraft"), 2)
        for token in (
            "workspaceStates", "payload", "draft", "baseRunId", "selectedRunIds",
            "chartMode", "chartObjectId", "seriesKind", "bottomMode", "airportValue",
        ):
            self.assertIn(token, JS)
        self.assertIn("permissions?.includes('results.export')", JS)
        self.assertIn("/api/results/export-file", JS)
        switch = extract_function(JS, "setWorkspace")
        self.assertNotIn("state.payload=null", switch.replace(" ", ""))
        self.assertNotIn("openOverlay()", switch)

    def test_workspace_state_round_trip_preserves_each_comparison_and_ui_selection(self):
        capture = extract_function(JS, "captureWorkspaceState")
        activate = extract_function(JS, "activateWorkspaceState")
        script = f"""
const blank = () => ({{payload:null,selection:null,draft:{{name:'blank'}},chartMode:'all',chartObjectId:null,seriesKind:'departures',bottomMode:'airports',airportValue:'sorties'}});
const state = {{
  workspace:'damage', payload:{{id:'D'}}, selection:{{r0_run_id:'R0'}}, draft:{{name:'damage'}},
  chartMode:'mission', chartObjectId:'M1', seriesKind:'returns', bottomMode:'scheme', airportValue:'share',
  workspaceStates:{{damage:blank(),multi:blank(),configuration:blank()}},
}};
{capture}
{activate}
captureWorkspaceState();
state.workspace='multi'; activateWorkspaceState('multi');
state.payload={{id:'M'}}; state.selection={{run_ids:['A','B']}}; state.draft={{name:'multi'}};
state.chartMode='airport'; state.chartObjectId='A1'; state.bottomMode='resources';
captureWorkspaceState();
state.workspace='damage'; activateWorkspaceState('damage');
process.stdout.write(JSON.stringify({{payload:state.payload,selection:state.selection,draft:state.draft,chartMode:state.chartMode,chartObjectId:state.chartObjectId,seriesKind:state.seriesKind,bottomMode:state.bottomMode,airportValue:state.airportValue,multi:state.workspaceStates.multi}}));
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
        self.assertEqual("D", result["payload"]["id"])
        self.assertEqual("damage", result["draft"]["name"])
        self.assertEqual("mission", result["chartMode"])
        self.assertEqual("M1", result["chartObjectId"])
        self.assertEqual("returns", result["seriesKind"])
        self.assertEqual("scheme", result["bottomMode"])
        self.assertEqual("share", result["airportValue"])
        self.assertEqual("M", result["multi"]["payload"]["id"])
        self.assertEqual("resources", result["multi"]["bottomMode"])

    def test_session_storage_is_user_scoped_and_never_persists_comparison_payload(self):
        self.assertIn("sessionStorage", JS)
        self.assertIn("user_id", JS)
        self.assertIn("results.workspace", JS)
        saved = extract_function(JS, "buildSessionState")
        self.assertIn("selection", saved)
        self.assertIn("chartMode", saved)
        self.assertIn("bottomMode", saved)
        self.assertNotIn("payload", saved)
        self.assertIn("restoreSavedComparisons", JS)
        self.assertIn("/api/me", JS)

    def test_bottom_tabs_keep_full_airport_resource_and_scheme_views(self):
        for label in ("全机场承接", "资源变化", "方案结构", "出动架次", "承接占比"):
            self.assertIn(label, HTML)
        self.assertNotIn("累计承接占比", HTML + JS)
        self.assertIn("Object.keys(state.payload.airports", JS)

    def test_metric_strip_uses_only_unified_canonical_run_summaries(self):
        for label in ("任务数", "需求架次", "已调度架次", "参与机场", "资源最低余量"):
            self.assertIn(label, JS)
        for field in (
            "mission_count",
            "required_sorties_total",
            "scheduled_sorties_total",
            "participating_airport_count",
            "minimum_resource_remaining",
        ):
            self.assertIn(field, JS)
        for forbidden in ("需求内已执行", "未执行", "额外出动", "完成率"):
            self.assertNotIn(forbidden, JS + HTML)
        self.assertIn("run_summaries", JS)
        self.assertIn("difference_overview", JS)
        self.assertIn("summary_deltas_vs_baseline", JS)
        self.assertIn("row.departure_share", JS)
        self.assertIn("objective_comparable", JS)

    def test_frozen_labels_drive_airport_mission_and_aircraft_display(self):
        self.assertIn("state.payload.labels", JS)
        self.assertIn("labelFor", JS)
        self.assertIn("airports", extract_function(JS, "labelFor"))
        self.assertIn("missions", extract_function(JS, "labelFor"))
        self.assertIn("aircraft", extract_function(JS, "labelFor"))
        self.assertIn("airportDisplayLabel", JS)
        self.assertIn("shortRunId", JS)
        self.assertNotIn("`${label} · ${id}`", extract_function(JS, "labelFor"))

    def test_visual_geometry_keeps_main_compare_and_overlay(self):
        self.assertIn("grid-template-columns:minmax(0,3fr) minmax(280px,1fr)", CSS)
        self.assertIn("overflow:auto", CSS)
        self.assertIn("overflow:hidden", CSS)
        self.assertIn("grid-template-rows", CSS)
        self.assertIn("position:fixed", CSS)
        self.assertIn("width:min(390px", CSS)
        self.assertIn("results-chart-tooltip", CSS)
        self.assertIn("results-hover-line", JS)


if __name__ == "__main__":
    unittest.main()
