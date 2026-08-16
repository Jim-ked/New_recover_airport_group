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


class GisRuntimeFrontendContractTests(unittest.TestCase):
    def test_page_uses_runtime_and_metrics_only_not_legacy_dispatch_or_scene(self):
        self.assertIn('/runtime`', JS)
        self.assertIn('/metrics`', JS)
        for forbidden in ('/api/dispatch', '/api/scenes', '/api/runtime', 'operations', 'scene_file', 'result_root'):
            self.assertNotIn(forbidden, JS)

    def test_runtime_is_read_only_full_map_with_overlay_controls_and_timeline(self):
        for token in ('READ ONLY', '显示控制', '图例', '对象速览', 'runtimeSlider', 'runtimePlayButton'):
            self.assertIn(token, HTML)
        self.assertIn('position:absolute', CSS)
        self.assertIn('runtime-map', HTML)

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
        for layer in ('airports', 'selected', 'participating', 'core', 'missions', 'damage', 'routes'):
            self.assertIn(f'data-layer="{layer}"', HTML)

    def test_unified_detail_dock_reuses_frozen_result_tabs(self):
        for label in ('机场承接', '任务调度', '机型投入', '资源保障', '技术信息'):
            self.assertIn(label, HTML)
        self.assertIn('runtime-detail-dock', HTML)

    def test_leaflet_is_local_only_and_missing_kernel_fails_visibly(self):
        self.assertIn('/static/vendor/leaflet/leaflet.js', JS)
        self.assertIn('/static/vendor/leaflet/leaflet.css', JS)
        self.assertNotIn('https://', JS)
        self.assertIn('Leaflet 本地地图内核尚未装载', JS)

    def test_runtime_routes_are_real_and_single_run_button_is_enabled(self):
        self.assertIn('@bp.get("/runs/<run_id>/runtime")', UI)
        self.assertIn('@bp.get("/runs/<run_id>/runtime")', FLASK_RUNS)
        self.assertIn('id="openRuntimeButton"', SINGLE_HTML)
        self.assertNotIn('GIS Runtime 将在下一切片接入', SINGLE_HTML)
        self.assertIn('window.location.href = `/runs/${encodeURIComponent(state.runId)}/runtime`', SINGLE_JS)


if __name__ == '__main__':
    unittest.main()
