from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "frontend/static/js/modules/gis-runtime.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/templates/pages/gis_runtime.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/static/css/gis-runtime.css").read_text(encoding="utf-8")
UI = (ROOT / "backend/web/flask_ui.py").read_text(encoding="utf-8")
FLASK_RUNS = (ROOT / "backend/web/flask_runs.py").read_text(encoding="utf-8")
SINGLE_HTML = (ROOT / "frontend/templates/pages/single_run.html").read_text(encoding="utf-8")
SINGLE_JS = (ROOT / "frontend/static/js/modules/single-run.js").read_text(encoding="utf-8")
SETTINGS = (ROOT / "backend/settings.py").read_text(encoding="utf-8")


class GisRuntimeFrontendContractTests(unittest.TestCase):
    def test_page_uses_runtime_and_metrics_only_not_legacy_dispatch_or_scene(self):
        self.assertIn('/runtime`', JS)
        self.assertIn('/metrics`', JS)
        for forbidden in ('/api/dispatch', '/api/scenes', '/api/runtime', 'operations', 'scene_file', 'result_root'):
            self.assertNotIn(forbidden, JS)

    def test_runtime_is_read_only_full_map_with_overlay_controls_and_timeline(self):
        for token in ('只读', '图层', '图例', '当前状态', 'runtimeSlider', 'runtimePlayButton'):
            self.assertIn(token, HTML)
        self.assertIn('position:absolute', CSS)
        self.assertIn('runtime-map', HTML)

    def test_layers_legend_and_detail_default_to_collapsed_without_losing_controls(self):
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
        self.assertIn("refs.dock.classList.remove('open')", JS)
        self.assertIn("state.selected = { type: null, id: null }", JS)
        self.assertIn('renderInspector()', JS)
        self.assertIn('height:64px', CSS)

    def test_runtime_labels_use_end_user_wording_not_dev_language(self):
        for token in ('图层', '任务', '航线', '出动', '返航', '视图', '航线范围', '标识', '组群机场', '参与机场', '核心机场', '损毁机场', '只读', '当前时段', '当前状态'):
            self.assertIn(token, HTML)
        self.assertIn('对象详情', JS)
        for dev in ('冻结', '对象可见性', '显示控制', '任务点', '航链', '出动腿', '返航腿', '当前窗', 'READ ONLY', '对象速览', '组选', '冻结事实'):
            self.assertNotIn(dev, HTML)
            self.assertNotIn(dev, JS)

    def test_map_draws_complete_chain_as_outbound_and_return_legs(self):
        for field in ('origin_airport_id', 'mission_id', 'return_airport_id', 'path_id'):
            self.assertIn(field, JS)
        self.assertIn('state.outboundLayers', JS)
        self.assertIn('state.returnLayers', JS)
        self.assertIn("data-layer=\"outbound\"", HTML)
        self.assertIn("data-layer=\"return\"", HTML)

    def test_playback_uses_backend_frames_not_damage_formulas_or_position_interpolation(self):
        self.assertIn('state.runtime.frames', JS)
        self.assertIn('f.damage_events', JS)
        self.assertIn('f.departures_total', JS)
        self.assertIn('f.returns_total', JS)
        for forbidden in ('recovery_duration_slots', 'remaining_capacity_per_window', 'vincenty', 'interpolatePosition'):
            self.assertNotIn(forbidden, JS)

    def test_all_airports_missions_damage_and_role_layers_are_available(self):
        for layer in ('airports', 'selected', 'participating', 'core', 'missions', 'damage', 'routes', 'maintenance'):
            self.assertIn(f'data-layer="{layer}"', HTML)

    def test_unified_detail_dock_reuses_frozen_result_tabs(self):
        for label in ('机场保障', '任务调度', '机型投入', '资源保障', '技术信息'):
            self.assertIn(label, HTML)
        self.assertIn('runtime-detail-dock', HTML)

    def test_runtime_aircraft_status_uses_frozen_chain_boundaries(self):
        for token in (
            'route.depart_window <= window && window < route.return_window',
            'route.return_window <= window && window < route.ready_window',
            'available_after_departure',
            '执行中',
            '整备中',
            '可用航空器',
        ):
            self.assertIn(token, JS)
        self.assertNotIn('出动航段中', JS)
        self.assertNotIn('返航航段中', JS)

    def test_detail_dock_is_a_required_dom_ref_not_business_state(self):
        self.assertIn("dock: $('runtimeDetailDock')", JS)
        self.assertIn("refs.dock.classList.contains('open')", JS)
        self.assertIn("refs.dock.classList.add('open')", JS)
        self.assertNotIn('state.dock', JS)
        self.assertIn('assertRuntimeDom()', JS)

    def test_leaflet_is_local_only_and_missing_kernel_fails_visibly(self):
        self.assertIn('/static/vendor/leaflet/leaflet.js', JS)
        self.assertIn('/static/vendor/leaflet/leaflet.css', JS)
        self.assertIn('/tiles/{z}/{x}/{y}.jpg', SETTINGS)
        self.assertNotIn('https://', JS)
        self.assertIn('Leaflet 本地地图内核尚未装载', JS)
        self.assertIn('地图加载失败；运行结果数据已正常读取', JS)
        self.assertIn('try { await initMap(); } catch (error) { showMapError(error); }', JS)
        self.assertIn("tiles.on('tileerror'", JS)

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
