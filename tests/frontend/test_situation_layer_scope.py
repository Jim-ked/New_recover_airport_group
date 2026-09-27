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
        self.assertNotIn('id="showAllDamage"', html)
        self.assertNotIn("缺少既定 projection endpoint", html)

    def test_layer_panel_separates_current_reference_and_display_controls(self):
        html=(ROOT/"frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        panels_js=(ROOT/"frontend/static/js/modules/situation-panels.js").read_text(encoding="utf-8")
        state_js=(ROOT/"frontend/static/js/modules/situation-state.js").read_text(encoding="utf-8")
        for text in ("当前情境", "当前机场与任务", "始终显示", "辅助参考", "其他基础机场",
                     "其他基础任务", "显示标注", "损毁事件配置标识", "对象名称标签"):
            self.assertIn(text, html)
        self.assertIn('id="showDamageConfig" type="checkbox" checked', html)
        self.assertIn('id="showObjectLabels" type="checkbox" checked', html)
        self.assertIn("setMapDisplayOption", panels_js)
        self.assertIn("showDamageConfig: true", state_js)
        self.assertIn("showObjectLabels: true", state_js)

    def test_display_controls_redraw_without_business_mutation(self):
        map_js=(ROOT/"frontend/static/js/modules/situation-map.js").read_text(encoding="utf-8")
        self.assertIn("export function setMapDisplayOption", map_js)
        self.assertIn("state.mode === 'damage'", map_js)
        self.assertNotIn("markDirty", map_js)
        self.assertNotIn("canonicalize", map_js)

    def test_map_instance_stays_encapsulated_behind_display_layer_api(self):
        map_js=(ROOT/"frontend/static/js/modules/situation-map.js").read_text(encoding="utf-8")
        panels_js=(ROOT/"frontend/static/js/modules/situation-panels.js").read_text(encoding="utf-8")
        self.assertIn("let map = null", map_js)
        self.assertIn("setCatalogLayer", panels_js)
        self.assertIn("setMapDisplayOption", panels_js)
        self.assertNotIn("__situationLeafletMap", map_js)

if __name__=="__main__":
    unittest.main()
