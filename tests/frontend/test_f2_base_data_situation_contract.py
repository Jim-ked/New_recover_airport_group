from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class F2BaseDataSituationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
        cls.ui = (ROOT / "backend/web/flask_ui.py").read_text(encoding="utf-8")
        cls.bd_html = (ROOT / "frontend/templates/pages/base_data.html").read_text(encoding="utf-8")
        cls.bd_js = (ROOT / "frontend/static/js/modules/base-data.js").read_text(encoding="utf-8")
        cls.sit_html = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        cls.sit_js = (ROOT / "frontend/static/js/modules/situations.js").read_text(encoding="utf-8")

    def test_situation_is_real_primary_navigation_and_base_data_is_secondary(self):
        self.assertIn("ui_v1.situations_page", self.base)
        self.assertIn("/situations", self.ui)
        self.assertIn("/base-data", self.ui)
        self.assertNotIn('href="{{ url_for(\'ui_v1.base_data_page\') }}"', self.base)
        self.assertIn("ui_v1.base_data_page", self.sit_html)

    def test_base_data_uses_current_state_crud_and_replace_import(self):
        for path in (
            "/api/airports", "/api/missions", "/api/aircraft-types", "/api/resource-types",
            "/api/aircraft-resource-requirements", "/api/base-data/import",
        ):
            self.assertIn(path, self.bd_js)
        self.assertIn("expected_revision", self.bd_js)
        self.assertIn("覆盖导入", self.bd_html)
        self.assertIn("不生成可选择的历史版本", self.bd_html)
        self.assertNotIn("/api/scenes", self.bd_js)

    def test_situation_keeps_one_working_copy_and_whole_aggregate_save(self):
        self.assertIn("expected_content_hash", self.sit_js)
        self.assertIn("/api/situations/working-copy/copy-airport", self.sit_js)
        self.assertIn("/api/situations/working-copy/copy-mission", self.sit_js)
        self.assertIn("beforeunload", self.sit_js)
        self.assertIn("markDirty", self.sit_js)
        self.assertNotIn("/api/situations/airports/", self.sit_js)
        self.assertNotIn("/api/situations/missions/", self.sit_js)

    def test_situation_supports_template_history_damage_and_no_fake_validation(self):
        self.assertIn("/api/missions/history", self.sit_js)
        self.assertIn("capacity_damage", self.sit_js)
        self.assertIn("resource_damage", self.sit_js)
        self.assertIn("navigation_delay", self.sit_js)
        self.assertIn("aircraft_damage", self.sit_js)
        self.assertNotIn("情境校验", self.sit_html)
        self.assertIn("跑道/保障要素 target_id 不在前端虚构", self.sit_js)

    def test_situation_map_is_local_leaflet_with_truthful_fallback(self):
        self.assertIn("/static/vendor/leaflet/leaflet.css", self.sit_js)
        self.assertIn("/static/vendor/leaflet/leaflet.js", self.sit_js)
        self.assertIn("无底图坐标视图", self.sit_html)
        self.assertNotIn("https://", self.sit_js)
        self.assertNotIn("http://", self.sit_js)


    def test_situation_applies_edits_through_server_canonical_working_copy(self):
        self.assertIn("/api/situations/working-copy/canonicalize", self.sit_js)
        self.assertIn("canonicalizeWorking", self.sit_js)
        self.assertIn("lockEditorForReadOnly", self.sit_js)
        self.assertIn("airportCandidateRegion", self.sit_js)
        self.assertIn("beginMissionLocationPick", self.sit_js)
        self.assertIn("longitude:num($('sitMissionLon').value)", self.sit_js)
        self.assertNotIn("longitude:Number($('sitMissionLon').value)", self.sit_js)
        self.assertNotIn("value=\"extreme\"", self.sit_js)
        self.assertNotIn("value=\"sustained\"", self.sit_js)

    def test_base_data_does_not_coerce_blank_coordinates_to_zero(self):
        self.assertIn("longitude:num($('edLon').value)", self.bd_js)
        self.assertIn("latitude:num($('edLat').value)", self.bd_js)
        self.assertIn("longitude:num($('edMissionLon').value)", self.bd_js)
        self.assertNotIn("longitude:Number($('edLon').value)", self.bd_js)


    def test_editors_protect_local_drafts_and_support_cross_page_detail(self):
        self.assertIn("editorDirty", self.bd_js)
        self.assertIn("canLeaveEditor", self.bd_js)
        self.assertIn("beforeunload", self.bd_js)
        self.assertIn("panelDraftDirty", self.sit_js)
        self.assertIn("canDiscardPanelDraft", self.sit_js)
        for token in ("cancelSituationInfo", "cancelAirportEdit", "cancelMissionEdit", "cancelDamageEdit"):
            self.assertIn(token, self.sit_js)
        self.assertIn("/base-data?tab=airports&id=", self.sit_js)
        self.assertIn("URLSearchParams(window.location.search)", self.bd_js)

    def test_f2_respects_permission_aware_mutation(self):
        self.assertIn("/api/me", self.bd_js)
        self.assertIn("catalog.write", self.bd_js)
        self.assertIn("/api/me", self.sit_js)
        self.assertIn("situations.write", self.sit_js)


if __name__ == "__main__":
    unittest.main()
