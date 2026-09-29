from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "frontend/static/js/modules/gis-runtime.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/templates/pages/gis_runtime.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/static/css/gis-runtime.css").read_text(encoding="utf-8")
ACTIVITY = (ROOT / "frontend/static/js/modules/runtime-map-activity.js").read_text(encoding="utf-8")
UI = (ROOT / "backend/web/flask_ui.py").read_text(encoding="utf-8")
FLASK_RUNS = (ROOT / "backend/web/flask_runs.py").read_text(encoding="utf-8")
SINGLE_HTML = (ROOT / "frontend/templates/pages/single_run.html").read_text(encoding="utf-8")
SINGLE_JS = (ROOT / "frontend/static/js/modules/single-run.js").read_text(encoding="utf-8")
SETTINGS = (ROOT / "backend/settings.py").read_text(encoding="utf-8")


class GisRuntimeFrontendContractTests(unittest.TestCase):
    def test_page_uses_runtime_projection_not_legacy_dispatch_scene_or_duplicate_metrics(self):
        self.assertIn('/runtime`', JS)
        self.assertNotIn('/metrics`', JS)
        for forbidden in ('/api/dispatch', '/api/scenes', '/api/runtime', 'operations', 'scene_file', 'result_root'):
            self.assertNotIn(forbidden, JS)

    def test_runtime_is_read_only_full_map_with_overlay_controls_and_timeline(self):
        for token in ('只读', '筛选', '图例', '总体运行态势', 'runtimeSlider', 'runtimePlayButton'):
            self.assertIn(token, HTML)
        self.assertIn('position:absolute', CSS)
        self.assertIn('runtime-map', HTML)

    def test_layers_and_legend_default_to_collapsed_without_losing_controls(self):
        for toggle_id, panel_id in (
            ('runtimeLayersToggle', 'runtimeLayerPanel'),
            ('runtimeLegendToggle', 'runtimeLegendPanel'),
        ):
            self.assertIn(f'id="{toggle_id}"', HTML)
            self.assertIn(f'aria-controls="{panel_id}"', HTML)
            self.assertIn('aria-expanded="false"', HTML)
            self.assertIn(f'id="{panel_id}"', HTML)
        self.assertIn('function closeMapPanels()', JS)
        self.assertIn("state.map.on('click'", JS)
        self.assertIn("state.selected = { type: null, id: null }", JS)
        self.assertIn('renderInspector()', JS)
        self.assertIn('height:64px', CSS)

    def test_runtime_labels_use_end_user_wording_not_dev_language(self):
        for token in ('总体态势筛选', '任务', '执行', '出动', '落地', '整备', '运行区域', '全部机场', '只读', '关系'):
            self.assertIn(token, HTML)
        self.assertNotIn('航线', HTML + JS)
        for dev in ('冻结', '对象可见性', '显示控制', '任务点', '航链', '出动腿', '返航腿', '当前窗', 'READ ONLY', '对象速览', '组选', '冻结事实'):
            self.assertNotIn(dev, HTML)
            self.assertNotIn(dev, JS)

    def test_map_draws_aggregated_relations_and_only_drills_into_explicit_paths(self):
        for field in ('origin_airport_id', 'mission_id', 'return_airport_id', 'path_id'):
            self.assertIn(field, JS + ACTIVITY)
        self.assertIn('aggregateRuntimeRelations', JS)
        self.assertIn('relation.pathIds.includes', JS)
        self.assertIn('selectPath(pathButton.dataset.pathId)', JS)
        self.assertIn("state.selected.type === 'path'", JS)
        self.assertIn('drawSelectedPath()', JS)
        self.assertNotIn('buildRuntimePathActivities', JS)
        self.assertNotIn('data-layer="activity"', HTML)
        self.assertNotIn('data-layer="history"', HTML)

    def test_playback_uses_backend_frames_not_damage_formulas_or_position_interpolation(self):
        self.assertIn('state.runtime.frames', JS)
        self.assertIn('currentFrame.damage_events', JS)
        self.assertIn('summarizeRuntimeOverview', JS)
        for forbidden in ('recovery_duration_slots', 'remaining_capacity_per_window', 'vincenty', 'interpolatePosition'):
            self.assertNotIn(forbidden, JS)

    def test_filter_panel_keeps_only_object_visibility_and_display_filters(self):
        for layer in ('airports', 'missions'):
            self.assertIn(f'data-layer="{layer}"', HTML)
        for removed in ('selected', 'participating', 'core', 'damage', 'routes', 'activity', 'history', 'events', 'maintenance'):
            self.assertNotIn(f'data-layer="{removed}"', HTML)
        for control in ('runtimeAirportScope', 'runtimeMissionFilter', 'runtimeAircraftFilter', 'runtimeReturnFilter', 'runtimeResetFilters'):
            self.assertIn(f'id="{control}"', HTML)
        self.assertIn('applyFiltersFromControls()', JS)

    def test_inspector_is_contextual_and_keeps_relation_path_drilldown(self):
        self.assertNotIn('runtimeInspectorModes', HTML + JS)
        self.assertNotIn('runtime-inspector-modes', CSS)
        self.assertNotIn('data-mode=', HTML)
        self.assertNotIn('inspectorMode', JS)
        self.assertIn('id="runtimeInspectorBack"', HTML)
        self.assertIn('clearSelection()', JS)
        self.assertIn('data-path-id=', JS)
        self.assertIn('data-relation-id=', JS)
        self.assertIn('<details class="runtime-breakdown"', JS)
        self.assertNotIn('openDock(', JS)

    def test_current_damage_phase_and_path_semantics_are_rendered_without_aircraft_identity(self):
        for token in ('runtime-damage-state', 'damagePhaseLabel', 'runtimeRoutePhase', '完整业务链', 'Path ID'):
            self.assertIn(token, JS + CSS)
        self.assertIn('非实际飞行轨迹', HTML)
        self.assertNotIn('aircraft_id', JS)
        self.assertIn('构成航次组', JS)

    def test_duplicate_detail_dock_and_open_detail_entry_are_removed(self):
        for token in ('runtimeOpenDetail', 'runtimeDetailDock', 'runtimeDetailTabs', 'runtime-detail-dock', 'openDetailForSelection'):
            self.assertNotIn(token, HTML + JS + CSS)

    def test_runtime_aircraft_status_uses_frozen_chain_boundaries(self):
        for token in (
            'Number(route.depart_window) <= Number(window)',
            'Number(window) < Number(route.return_window)',
            'Number(route.return_window) <= Number(window)',
            'Number(window) < Number(route.ready_window)',
            'available_after_departure',
            '执行中',
            '整备中',
            '可用航空器',
        ):
            self.assertIn(token, JS + ACTIVITY)
        self.assertNotIn('出动航段中', JS)
        self.assertNotIn('返航航段中', JS)

    def test_departure_and_landing_are_mutually_exclusive_aggregate_views(self):
        for view in ('execution', 'departure', 'landing', 'maintenance'):
            self.assertIn(f'data-runtime-view="{view}"', HTML)
        self.assertIn("color: selected ? '#bfe8f7' : '#6f91a3'", JS)
        self.assertIn("relation.view === 'landing'", JS)
        self.assertNotIn('pathEventMarker(', JS)

    def test_layer_visibility_does_not_drive_overall_statistics(self):
        overall = JS[JS.index('function renderOverallInspector'):JS.index('function renderAirportInspector')]
        self.assertIn('const totals = overview()', overall)
        self.assertNotIn('layerEnabled(', overall)
        self.assertNotIn('state.filters', overall)

    def test_runtime_marker_children_have_explicit_block_box_geometry(self):
        self.assertIn('.runtime-airport-marker>span,.runtime-mission-marker>span{display:block;box-sizing:border-box;position:absolute', CSS)
        self.assertIn('.runtime-airport-marker>span{width:14px;height:14px', CSS)
        self.assertIn('.runtime-mission-marker>span{width:14px;height:14px', CSS)
        self.assertIn('iconSize: [28, 28], iconAnchor: [14, 14]', JS)
        self.assertNotIn('.runtime-airport-marker span{width:7px', CSS)
        self.assertNotIn('.runtime-mission-marker span{width:8px', CSS)

    def test_runtime_marker_identity_is_circle_cluster_core_damage_and_selection_only(self):
        for token in (
            '.runtime-airport-marker.participating>span',
            '.runtime-airport-marker.selected-cluster>span',
            '.runtime-airport-marker.core::after',
            '.runtime-airport-marker.selected-cluster::before',
            '.runtime-airport-marker.selected-object>span',
            '.runtime-damage-state.recovering',
            '.runtime-mission-marker>span',
        ):
            self.assertIn(token, CSS)
        self.assertIn("classes.push('ordinary')", JS)
        self.assertIn("classes.push('selected-cluster')", JS)
        self.assertIn("classes.push('core')", JS)
        self.assertIn("damagePhase === 'recovering'", JS)
        self.assertNotIn('runtime-airport-role-badge', JS + CSS)
        self.assertNotIn('background:#d99c35', CSS)
        self.assertNotIn('border-color:#ffe0a6', CSS)
        for role_shape in ('airport-role-civil', 'airport-role-military', 'airport-role-joint'):
            self.assertNotIn(role_shape, CSS)

    def test_overall_inspector_and_timeline_follow_active_view_without_filtering_full_run_totals(self):
        overall = JS[JS.index('function renderOverallInspector'):JS.index('function renderAirportInspector')]
        self.assertIn('summarizeRuntimeView', JS)
        self.assertIn('VIEW_PRIMARY', JS)
        self.assertIn('runtime-contribution-bar', overall)
        self.assertNotIn('state.filters', overall)
        self.assertNotIn('runtimeWindowFacts', HTML + JS)
        self.assertNotIn('runtimeDamageStrip', HTML + JS)

    def test_runtime_relations_use_quadratic_geometry_and_directional_motion_with_cleanup(self):
        self.assertIn('quadraticRelationshipPoints', JS)
        self.assertIn('runtime-relation-motion', JS + CSS)
        self.assertIn('runtime-relation-base', JS + CSS)
        self.assertIn('@keyframes runtime-relation-flow', CSS)
        self.assertIn('stroke-dashoffset:-28', ''.join(CSS.split()))
        self.assertIn('@media (prefers-reduced-motion: reduce)', CSS)
        self.assertIn('clearMapLayers()', JS)

    def test_runtime_dom_contract_no_longer_requires_removed_detail_panel(self):
        self.assertIn('assertRuntimeDom()', JS)
        self.assertNotIn("dock: $('runtimeDetailDock')", JS)

    def test_leaflet_is_local_only_and_missing_kernel_fails_visibly(self):
        self.assertIn('/static/vendor/leaflet/leaflet.js', JS)
        self.assertIn('/static/vendor/leaflet/leaflet.css', JS)
        self.assertIn('/tiles/{source}/{z}/{x}/{y}.jpg', SETTINGS)
        self.assertNotIn('https://', JS)
        self.assertIn('Leaflet 本地地图内核尚未装载', JS)
        self.assertIn('地图加载失败；运行结果数据已正常读取', JS)
        self.assertIn('try { await initMap(); } catch (error) { showMapError(error); }', JS)
        self.assertIn('./local-basemap.js', JS)
        self.assertNotIn("tileerror", JS)

    def test_leaflet_zoom_control_is_disabled_at_map_creation_but_map_navigation_remains(self):
        self.assertIn("L.map(refs.map, { zoomControl: false", JS)
        self.assertNotIn('leaflet-control-zoom', JS + CSS)
        for forbidden in (
            'scrollWheelZoom: false',
            'doubleClickZoom: false',
            'dragging: false',
            'touchZoom: false',
        ):
            self.assertNotIn(forbidden, JS)
        for token in ('fitMap(', "refs.map", "state.map.fitBounds", 'runtimeSlider'):
            self.assertIn(token, JS)

    def test_runtime_routes_are_real_and_single_run_button_is_enabled(self):
        self.assertIn('@bp.get("/runs/<run_id>/runtime")', UI)
        self.assertIn('@bp.get("/runs/<run_id>/runtime")', FLASK_RUNS)
        self.assertIn('id="openRuntimeButton"', SINGLE_HTML)
        self.assertNotIn('GIS Runtime 将在下一切片接入', SINGLE_HTML)
        self.assertIn('window.location.href = `/runs/${encodeURIComponent(state.runId)}/runtime`', SINGLE_JS)


if __name__ == '__main__':
    unittest.main()
