from __future__ import annotations
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]

class SituationLayerScopeTests(unittest.TestCase):
    def test_extended_layers_are_read_only_context_sources(self):
        map_js=(ROOT/"frontend/static/js/modules/situation-map.js").read_text(encoding="utf-8")
        html=(ROOT/"frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        self.assertIn("/api/airports", map_js)
        self.assertIn("/api/missions", map_js)
        self.assertIn("export async function setCatalogLayer", map_js)
        self.assertNotIn("method: \"POST\"", map_js)
        self.assertNotIn("method: 'POST'", map_js)
        self.assertIn('id="showAllDamage" type="checkbox" disabled', html)
        self.assertIn("缺少既定 projection endpoint", html)

    def test_map_instance_stays_encapsulated_behind_display_layer_api(self):
        map_js=(ROOT/"frontend/static/js/modules/situation-map.js").read_text(encoding="utf-8")
        panels_js=(ROOT/"frontend/static/js/modules/situation-panels.js").read_text(encoding="utf-8")
        self.assertIn("let map = null", map_js)
        self.assertIn("setCatalogLayer", panels_js)
        self.assertNotIn("__situationLeafletMap", map_js)

if __name__=="__main__":
    unittest.main()
