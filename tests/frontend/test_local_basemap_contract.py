from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class LocalBasemapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.basemap = (ROOT / "frontend/static/js/modules/local-basemap.js").read_text(encoding="utf-8")
        cls.situation = (ROOT / "frontend/static/js/modules/situation-map.js").read_text(encoding="utf-8")
        cls.runtime = (ROOT / "frontend/static/js/modules/gis-runtime.js").read_text(encoding="utf-8")
        cls.settings = (ROOT / "backend/settings.py").read_text(encoding="utf-8")
        cls.situation_html = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        cls.runtime_html = (ROOT / "frontend/templates/pages/gis_runtime.html").read_text(encoding="utf-8")

    def test_shared_basemap_contains_all_sources_and_real_native_zoom_contracts(self) -> None:
        for source in ("world", "eastasia", "china_east", "japan", "korea", "taiwan"):
            self.assertIn(f"source: '{source}'", self.basemap)
        for token in (
            "minZoom: 2, maxNativeZoom: 7, maxZoom: 15",
            "minZoom: 8, maxNativeZoom: 11, maxZoom: 15",
            "minZoom: 12, maxNativeZoom: 13, maxZoom: 15",
            "minZoom: 12, maxNativeZoom: 14, maxZoom: 15",
            "minZoom: 12, maxNativeZoom: 15, maxZoom: 15",
        ):
            self.assertIn(token, self.basemap)

    def test_shared_basemap_owns_bounds_panes_and_transparent_tile_fallback(self) -> None:
        for coordinate in (
            "-85.0511287798066", "85.0511287798066", "4.214943141390654", "55.7765730186677",
            "17.97873309555615", "42.5530802889558", "23.88583769986199", "46.55886030311718",
            "32.842673631954305", "38.95940879245423", "22.998851594142913", "25.562265014427506",
        ):
            self.assertIn(coordinate, self.basemap)
        self.assertIn("createPane", self.basemap)
        self.assertIn("zIndex", self.basemap)
        self.assertIn("errorTileUrl", self.basemap)
        self.assertIn("destroyLocalBasemap", self.basemap)
        for source, z_index in (
            ("world", 180), ("eastasia", 190), ("china_east", 200),
            ("japan", 210), ("korea", 220), ("taiwan", 230),
        ):
            start = self.basemap.index(f"source: '{source}'")
            self.assertIn(f"zIndex: {z_index}", self.basemap[start:start + 140])

    def test_both_maps_use_shared_basemap_and_have_no_independent_tile_layer(self) -> None:
        for consumer in (self.situation, self.runtime):
            self.assertIn("./local-basemap.js", consumer)
            self.assertIn("createLocalBasemap", consumer)
            self.assertNotIn("L.tileLayer", consumer)
            self.assertNotIn("globalThis.L.tileLayer", consumer)
            self.assertIn("maxZoom: 15", consumer)

    def test_runtime_keeps_kernel_errors_but_not_regional_tile_errors(self) -> None:
        self.assertIn("try { await initMap(); } catch (error) { showMapError(error); }", self.runtime)
        self.assertIn("Leaflet 本地地图内核尚未装载", self.runtime)
        self.assertNotIn("tileerror", self.runtime)

    def test_templates_inject_source_template_and_no_online_or_legacy_architecture_returns(self) -> None:
        self.assertIn('/tiles/{source}/{z}/{x}/{y}.jpg', self.settings)
        self.assertIn('data-tile-template', self.situation_html)
        self.assertIn('data-tile-template', self.runtime_html)
        combined = "\n".join((self.basemap, self.situation, self.runtime))
        self.assertNotIn("https://", combined)
        self.assertNotIn("http://", combined)
        self.assertNotIn("/api/dispatch", combined)
        self.assertNotIn("/api/scenes", combined)
        self.assertNotIn("Scene", combined)

    def test_shared_basemap_has_no_business_state_or_overlay_responsibilities(self) -> None:
        for forbidden in ("situation-state", "Run state", "airport", "mission", "damage", "route", "dispatch", "Scene"):
            self.assertNotIn(forbidden, self.basemap)


if __name__ == "__main__":
    unittest.main()
