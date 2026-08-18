from __future__ import annotations
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class SituationWorkspaceFrontendContractTests(unittest.TestCase):
    def test_leaflet_asset_loads_before_current_situation_module(self):
        text = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        navigation = (ROOT / "frontend/static/js/modules/workspace-navigation.js").read_text(encoding="utf-8")
        self.assertIn("vendor/leaflet/leaflet.js", text)
        self.assertIn("js/modules/situations.js", text)
        self.assertLess(navigation.index("await loadWorkspaceAssets"), navigation.index("await import(moduleUrl)"))
        self.assertIn("vendor/leaflet/leaflet.css", text)

    def test_workspace_exposes_two_level_edit_state_and_real_conflict_action(self):
        text = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        for token in (
            "situationSaveState",
            "panelDraftStatus",
            "situationConflictBar",
            "reloadConflict",
            "keepLocalConflict",
        ):
            self.assertIn(token, text)
        state = (ROOT / "frontend/static/js/modules/situation-state.js").read_text(encoding="utf-8")
        self.assertIn("panelDraftDirty", state)

    def test_map_bootstrap_respects_actual_local_tile_zoom_and_moves_zoom_control(self):
        text = (ROOT / "frontend/static/js/modules/situation-map.js").read_text(encoding="utf-8")
        self.assertIn('maxNativeZoom: 7', text)
        self.assertIn("position: 'bottomleft'", text)
        self.assertIn('zoomControl: false', text)

    def test_idle_inspector_is_not_a_permanent_empty_panel(self):
        text = (ROOT / "frontend/static/js/modules/situation-panels.js").read_text(encoding="utf-8")
        self.assertIn("function inspectorHasTask()", text)
        self.assertIn("setInspectorOpen", text)
        self.assertIn("refs.searchResults", text)

    def test_save_does_not_discard_unapplied_form_draft(self):
        text = (ROOT / "frontend/static/js/modules/situations.js").read_text(encoding="utf-8")
        self.assertIn("if(state.panelDraftDirty)", text)
        self.assertIn("请先将右侧表单", text)

    def test_conflict_copy_does_not_claim_automatic_merge(self):
        html = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        js = (ROOT / "frontend/static/js/modules/situations.js").read_text(encoding="utf-8")
        panels = (ROOT / "frontend/static/js/modules/situation-panels.js").read_text(encoding="utf-8")
        self.assertIn("当前本地修改仍保留", html)
        self.assertIn("expected_content_hash", js)
        self.assertIn("showConflict()", js)
        self.assertIn("'keepLocalConflict').addEventListener('click', clearConflict", panels)
        self.assertIn("'reloadConflict').addEventListener('click'", panels)
        self.assertIn("await callbacks.reloadSituation?.()", panels)
        self.assertNotIn("自动合并", html)


if __name__ == "__main__":
    unittest.main()
