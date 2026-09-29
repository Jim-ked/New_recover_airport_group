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
    def test_dashboard_has_single_header_and_four_analysis_regions(self):
        for element_id in (
            "timelinePanel", "airportAcceptancePanel", "taskDistributionPanel", "runSupportPanel",
        ):
            self.assertIn(f'id="{element_id}"', HTML)
        self.assertIn("dashboard-grid", HTML)
        self.assertNotIn("结果概览", HTML)
        self.assertNotIn("任务与调度", HTML)
        self.assertNotIn("保障状态", HTML)
        for field in (
            "required_sorties_total", "fulfilled_sorties_total", "unmet_sorties_total",
            "additional_sorties_total", "participating_airport_count",
        ):
            self.assertIn(field, JS)
        self.assertNotIn("出动情况", HTML)

    def test_header_keeps_compact_run_identity_runtime_and_record_entry(self):
        for token in ('id="runBadges"', 'id="openRuntimeButton"', 'id="openRunRecordButton"', "单次运行", "运行态势", "结果记录"):
            self.assertIn(token, HTML)

    def test_header_is_one_line_with_only_situation_context_allowed_to_shrink(self):
        header = HTML[HTML.index('<header class="single-head">'):HTML.index('</header>')]
        for token in (
            'class="single-back"', '<h1>单次运行</h1>', 'id="runBadges"',
            'id="openRunRecordButton"', 'id="openRuntimeButton"',
        ):
            self.assertIn(token, header)
        self.assertLess(header.index('class="single-back"'), header.index('<h1>单次运行</h1>'))
        self.assertLess(header.index('<h1>单次运行</h1>'), header.index('id="runBadges"'))
        self.assertLess(header.index('id="runBadges"'), header.index('id="openRunRecordButton"'))
        render = extract_function(JS, "renderBadges")
        for token in ("single-run-id", "single-context", "single-damage-status", "single-cluster-meta", "single-run-status", "status-dot"):
            self.assertIn(token, render)
        self.assertIn("height:52px", CSS)
        self.assertIn("white-space:nowrap", CSS)
        self.assertIn(".single-context{", CSS)
        self.assertIn("text-overflow:ellipsis", CSS)
        self.assertNotIn("single-badge", render)

    def test_dashboard_uses_independent_vertical_columns(self):
        for token in ('class="dashboard-column dashboard-left"', 'class="dashboard-column dashboard-right"'):
            self.assertIn(token, HTML)
        self.assertIn("grid-template-rows:52px minmax(0,1fr)", CSS)
        self.assertIn(".single-message,.single-loading,.dashboard-grid{grid-row:2", CSS)
        self.assertIn(".dashboard-left{grid-template-rows:minmax(0,1fr) auto", CSS)
        self.assertIn(".dashboard-right{grid-template-rows:minmax(312px,42%) minmax(0,1fr)", CSS)
        self.assertIn("grid-template-rows:minmax(0,1fr)", CSS)

    def test_task_panel_height_is_bounded_for_three_five_and_twenty_tasks(self):
        sizing = extract_function(JS, "taskPanelHeight")
        script = f"""
{sizing}
process.stdout.write(JSON.stringify([taskPanelHeight(3), taskPanelHeight(5), taskPanelHeight(20)]));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        three, five, twenty = json.loads(completed.stdout)
        self.assertEqual(182, three)
        self.assertEqual(258, five)
        self.assertEqual(340, twenty)
        self.assertIn("--task-panel-height", extract_function(JS, "renderTaskDistribution"))
        self.assertIn("height:clamp(180px,var(--task-panel-height),min(340px,40vh))", CSS)
        self.assertIn("overflow:auto", CSS)

    def test_support_uses_one_primary_chart_and_compact_secondary_facts(self):
        for token in (
            'id="supportViews"', 'data-support-view="aircraft"', 'data-support-view="resource"',
            'id="supportSecondarySummary"', "supportView", "renderSupportView",
        ):
            self.assertIn(token, HTML + JS)
        self.assertIn(".support-block{display:none", CSS)
        self.assertIn(".support-block.active{display:grid", CSS)

    def test_support_controls_and_summaries_match_the_active_fact_type(self):
        compact = extract_function(JS, "renderCompactSupport")
        aircraft_facts = extract_function(JS, "aircraftSupportFacts")
        for token in ("available_before_departure", "in_use", "ready_releases"):
            self.assertIn(token, compact)
        self.assertIn("期内再次可用", aircraft_facts)
        for token in ("累计消耗", "实际补给", "期末剩余"):
            self.assertIn(token, JS)
        self.assertIn(".support-block:not(.active)", CSS)

    def test_default_timeline_is_grouped_bars_with_scope_summary(self):
        for token in ('data-view="events"', "renderGroupedBarChart", 'id="timelineSummary"', "累计出动", "需求履行", "出动峰值"):
            self.assertIn(token, HTML + JS)

    def test_event_timeline_supports_total_composition_direction_and_window_navigation(self):
        for token in (
            'id="timelineEventModes"', 'data-event-mode="total"',
            'data-event-mode="composition"', 'id="timelineDirections"',
            'data-direction="departures"', 'data-direction="returns"',
            'id="timelineWindowNav"', 'id="timelineWindowRange"',
            "visibleTimelineRange", "aggregateAircraftTimeline", "renderStackedBarChart",
            "validateCompositionTotals",
        ):
            self.assertIn(token, HTML + JS)
        self.assertNotIn("preserveAspectRatio: 'none'", extract_function(JS, "renderGroupedBarChart"))
        self.assertIn("niceAxisMaximum", extract_function(JS, "renderGroupedBarChart"))

    def test_timeline_axis_keeps_small_headroom_without_coarse_rounding(self):
        axis = extract_function(JS, "niceAxisMaximum")
        script = f"""
{axis}
process.stdout.write(JSON.stringify([niceAxisMaximum(28), niceAxisMaximum(31), niceAxisMaximum(0.8)]));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual([32, 36, 1.2], json.loads(completed.stdout))

    def test_aircraft_composition_is_derived_from_complete_solution_chains(self):
        aggregate = extract_function(JS, "aggregateAircraftTimeline")
        for token in (
            "state.solution.sortie_chains", "origin_airport_id", "mission_id",
            "aircraft_type", "depart_window", "return_window", "sorties",
        ):
            self.assertIn(token, aggregate)
        self.assertIn("逐窗机型构成与正式总量不一致", JS)

    def test_aircraft_composition_groups_departure_and_return_windows_exactly(self):
        aggregate = extract_function(JS, "aggregateAircraftTimeline")
        validate = extract_function(JS, "validateCompositionTotals")
        script = f"""
const state = {{
  metrics: {{time_axis: {{windows: [0, 1, 2]}}}},
  solution: {{sortie_chains: [
    {{origin_airport_id:'A1', mission_id:'M1', return_airport_id:'A2', aircraft_type:'T1', depart_window:0, return_window:2, sorties:3}},
    {{origin_airport_id:'A1', mission_id:'M1', return_airport_id:'A1', aircraft_type:'T2', depart_window:1, return_window:2, sorties:2}},
  ]}},
  timelineMode: 'all', timelineObjectId: null,
}};
function aircraftColor(id) {{ return id === 'T1' ? '#1' : '#2'; }}
{aggregate}
{validate}
const departures = aggregateAircraftTimeline('departures');
const returns = aggregateAircraftTimeline('returns');
process.stdout.write(JSON.stringify({{
  departures: departures.map(row => [row.label, row.values]),
  returns: returns.map(row => [row.label, row.values]),
  departureMatch: validateCompositionTotals(departures, [3, 2, 0]),
  returnMatch: validateCompositionTotals(returns, [0, 0, 5]),
  mismatch: validateCompositionTotals(departures, [3, 1, 0]),
}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        result = json.loads(completed.stdout)
        self.assertEqual([["T1", [3, 0, 0]], ["T2", [0, 2, 0]]], result["departures"])
        self.assertEqual([["T1", [0, 0, 3]], ["T2", [0, 0, 2]]], result["returns"])
        self.assertTrue(result["departureMatch"])
        self.assertTrue(result["returnMatch"])
        self.assertFalse(result["mismatch"])

    def test_long_timeline_keeps_native_windows_and_links_task_heatmap_range(self):
        for token in (
            "timelineWindowStart", "timelineWindowSize", "timelineWindowStep",
            "data-window-nav", "renderTimelineWindowNavigation", "timeline-window-hidden",
        ):
            self.assertIn(token, HTML + JS + CSS)
        self.assertNotIn("downsample", JS.lower())

    def test_airport_acceptance_keeps_all_airports_and_independent_roles(self):
        for token in ('id="airportAcceptanceList"', "renderAirportAcceptance", "is_core", "is_selected", "is_participating", "departure_share", "utilization"):
            self.assertIn(token, HTML + JS)
        self.assertNotRegex(JS, r"Object\.entries\([^\n]+airports[^\n]+\.slice\(")

    def test_airport_acceptance_share_and_collaboration_use_one_selection(self):
        for token in (
            'data-airport-view="acceptance"', 'data-airport-view="share"',
            'data-airport-view="collaboration"', "renderAirportShare",
            "airport-donut", "capacity-pressure", "airport-aircraft-stack",
        ):
            self.assertIn(token, HTML + JS + CSS)
        acceptance = extract_function(JS, "renderAirportAcceptance")
        self.assertIn("aircraftColor", acceptance)
        self.assertIn("airportAircraftDepartures", acceptance)
        self.assertNotRegex(acceptance, r"\.slice\(")

    def test_capacity_occupancy_uses_weighted_formal_window_facts_and_preserves_coverage(self):
        derive = extract_function(JS, "capacityOccupancyFacts")
        script = f"""
{derive}
const complete = capacityOccupancyFacts({{
  available:[10,100,0,10], used_departure:[10,10,0,0], used_arrival:[0,0,0,0],
  used_total:[10,10,0,0], utilization:[1,0.1,null,0]
}}, [20,21,22,23]);
const partial = capacityOccupancyFacts({{
  available:[10,10,0], used_departure:[5,null,0], used_arrival:[0,null,0],
  used_total:[5,null,0], utilization:[0.5,null,null]
}}, [30,31,32]);
const unavailable = capacityOccupancyFacts({{
  available:[0,0], used_departure:[0,0], used_arrival:[0,0],
  used_total:[0,0], utilization:[null,null]
}}, [40,41]);
const missing = capacityOccupancyFacts({{}}, [50,51]);
const recordedZero = capacityOccupancyFacts({{
  available:[8], used_departure:[0], used_arrival:[0], used_total:[0], utilization:[0]
}}, [60]);
process.stdout.write(JSON.stringify({{complete,partial,unavailable,missing,recordedZero}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        result = json.loads(completed.stdout)
        self.assertAlmostEqual(1 / 6, result["complete"]["occupancy"])
        self.assertEqual("recorded", result["complete"]["status"])
        self.assertEqual(1, result["complete"]["peak"])
        self.assertEqual(20, result["complete"]["peakWindow"])
        self.assertEqual(1, result["complete"]["fullWindowCount"])
        self.assertEqual(3, result["complete"]["validWindowCount"])
        self.assertEqual(1, result["complete"]["unavailableWindowCount"])
        self.assertEqual(0, result["complete"]["missingWindowCount"])
        self.assertEqual("partial", result["partial"]["status"])
        self.assertEqual(1, result["partial"]["validWindowCount"])
        self.assertEqual(1, result["partial"]["unavailableWindowCount"])
        self.assertEqual(1, result["partial"]["missingWindowCount"])
        self.assertEqual("not_applicable", result["unavailable"]["status"])
        self.assertIsNone(result["unavailable"]["occupancy"])
        self.assertEqual("missing", result["missing"]["status"])
        self.assertEqual("recorded", result["recordedZero"]["status"])
        self.assertEqual(0, result["recordedZero"]["occupancy"])

    def test_airport_default_capacity_fact_is_occupancy_with_peak_and_coverage_in_tooltip(self):
        acceptance = extract_function(JS, "renderAirportAcceptance")
        for token in (
            "capacityOccupancyFacts", "占用", "运行期容量占用率", "峰值利用率",
            "峰值时间窗", "满负荷时间窗", "有效时间窗", "不可用时间窗", "缺失时间窗",
        ):
            self.assertIn(token, acceptance + JS)
        self.assertNotIn("`承压 ", acceptance)

    def test_task_distribution_uses_aligned_timeline_and_aircraft_composition(self):
        for token in ('id="taskDistributionRows"', "renderTaskDistribution", "departures_timeline", "scheduled_by_aircraft", "fulfilled_by_aircraft", "unmet_by_aircraft", "additional_by_aircraft"):
            self.assertIn(token, HTML + JS)

    def test_task_timeline_does_not_render_missing_values_as_zero(self):
        render = extract_function(JS, "renderTaskDistribution")
        self.assertIn("task-heat-missing", render)
        self.assertIn("出动未记录", render)
        self.assertNotIn("Number(value || 0) / maxTimeline", render)

    def test_support_shows_aircraft_and_specific_resource_together(self):
        for token in ('id="aircraftMiniChart"', 'id="resourceMiniChart"', 'id="supportResourceSelect"', "consumed_increment", "replenishment_actual", "remaining"):
            self.assertIn(token, HTML + JS)

    def test_local_interactions_keep_scope_and_data_semantics_explicit(self):
        for token in (
            "data-mission-id", "data-airport-id", "selectedWindowIndex", "setTimelineFocus",
            "renderCollaborationView", "origin_airport_id", "return_airport_id", "mission_id",
            'id="openResourceDetailButton"', 'id="resourceDetailDialog"', "renderResourceDetail",
        ):
            self.assertIn(token, HTML + JS)

    def test_process_view_requires_recorded_verified_phase_facts(self):
        for token in ('data-view="phases"', "phase_occupancy", "updatePhaseAvailability", "过程事实未记录"):
            self.assertIn(token, HTML + JS)
        self.assertNotIn("mission_arrival_slot", JS)

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

    def test_resource_detail_combines_inventory_and_quantity_events_on_one_axis(self):
        self.assertNotIn('id="resourceDetailModes"', HTML)
        detail = extract_function(JS, "renderResourceDetail")
        for token in (
            "renderResourceProcessChart", "remaining", "consumed_increment",
            "replenishment_actual", "permanent_loss", "storage_capacity",
        ):
            self.assertIn(token, detail + JS)
        process = extract_function(JS, "renderResourceProcessChart")
        self.assertIn("resource-stock-layer", process)
        self.assertIn("resource-event-layer", process)
        self.assertIn("永久损失：未记录", process)

    def test_run_record_integrates_static_spatial_context_without_fake_track(self):
        for token in (
            'id="runRecordTabs"', 'data-record-view="facts"',
            'data-record-view="spatial"', 'id="runSpatialView"',
            "renderRunSpatialView", "sortie_chains", "origin_airport_id",
            "mission_id", "return_airport_id", "静态业务关系，不代表实际飞行航迹",
        ):
            self.assertIn(token, HTML + JS)

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

    def test_full_facts_are_kept_in_one_secondary_record_surface(self):
        for token in ('id="runRecordDialog"', 'id="solverFacts"', 'id="taskFulfillmentMatrix"', 'id="chainTableBody"'):
            self.assertIn(token, HTML)
        self.assertIn("完整航链", HTML)

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

    def test_resource_compact_summary_keeps_zero_and_missing_without_repeating_unit(self):
        facts = extract_function(JS, "resourceSupportFacts")
        script = f"""
function number(value, digits = 2) {{ return Number(value).toFixed(digits); }}
function recorded(value, formatter = String) {{ return value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value)) ? '未记录' : formatter(value); }}
{facts}
const recordedFacts = resourceSupportFacts({{
  consumed_increment:[100,26], replenishment_actual:[0,0], remaining:[80,54]
}});
const missingFacts = resourceSupportFacts({{
  consumed_increment:null, replenishment_actual:[0], remaining:[]
}});
process.stdout.write(JSON.stringify({{recordedFacts,missingFacts}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        result = json.loads(completed.stdout)
        self.assertEqual(
            [["累计消耗", "126.00", ""], ["实际补给", "0.00", ""], ["期末剩余", "54.00", ""]],
            result["recordedFacts"],
        )
        self.assertEqual("未记录", result["missingFacts"][0][1])
        self.assertEqual("0.00", result["missingFacts"][1][1])
        self.assertEqual("未记录", result["missingFacts"][2][1])
        self.assertNotIn("meta.unit", facts)
        self.assertNotIn("withUnit", facts)
        self.assertIn("resourceLabel(state.supportResourceId)", extract_function(JS, "renderCompactSupport"))
        self.assertIn("resourceLabel(state.supportResourceId)", extract_function(JS, "renderSupportView"))

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
