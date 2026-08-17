from __future__ import annotations
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

class SituationV9LayoutTests(unittest.TestCase):
    def test_topbar_only_has_lifecycle_actions_not_module_title_or_data_button(self):
        text=(ROOT/"frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        start=text.index("{% block topbar_context %}")
        end=text.index("{% endblock %}", start)
        top=text[start:end]
        self.assertIn("newSituationButton", top)
        self.assertIn("saveSituationButton", top)
        self.assertIn("deleteSituationButton", top)
        self.assertNotIn("数据管理", top)
        self.assertNotIn(">情境构建<", top)

    def test_situation_switcher_is_in_bottom_overview_dock(self):
        text=(ROOT/"frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        overview=text[text.index('id="situationOverview"'):]
        self.assertIn('id="situationSelect"', overview)
        self.assertIn('id="overviewCounts"', overview)

    def test_airport_mission_damage_are_left_tools_and_layers_is_not_edit_mode(self):
        text=(ROOT/"frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        self.assertIn('data-mode="airport"', text)
        self.assertIn('data-mode="mission"', text)
        self.assertIn('data-mode="damage"', text)
        self.assertIn('id="layerScopeButton"', text)
        self.assertNotIn('data-mode="layers"', text)

    def test_fit_extent_is_leaflet_control_not_top_toolbar_button(self):
        html=(ROOT/"frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        js=(ROOT/"frontend/static/js/situation-map-bootstrap.js").read_text(encoding="utf-8")
        self.assertIn('class="hidden-control"', html)
        self.assertIn("leaflet-control-fit", js)
        self.assertIn('getElementById("fitSituationButton")', js)

    def test_old_dom_polish_layers_are_not_loaded(self):
        text=(ROOT/"frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        self.assertNotIn("situation-ux.js", text)
        self.assertNotIn("situation-panel-polish.js", text)
        self.assertIn("situation-layout.js", text)
        self.assertIn("situation-display-layers.js", text)

    def test_base_data_and_settings_are_single_sidebar_entries(self):
        text=(ROOT/"frontend/templates/base.html").read_text(encoding="utf-8")
        self.assertEqual(1, text.count("<span>基础数据</span>"))
        self.assertEqual(1, text.count("<span>系统设置</span>"))
        start=text.index('id="accountPopover"')
        end=text.index("</div>", start)
        account=text[start:end]
        self.assertNotIn("系统设置", account)

if __name__=="__main__":
    unittest.main()
