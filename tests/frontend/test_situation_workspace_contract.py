from __future__ import annotations
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class SituationWorkspaceFrontendContractTests(unittest.TestCase):
    def test_leaflet_bootstrap_runs_before_existing_situation_module(self):
        text = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        bootstrap = text.index("situation-map-bootstrap.js")
        existing = text.index("js/modules/situations.js")
        ux = text.index("js/modules/situation-ux.js")
        self.assertLess(bootstrap, existing)
        self.assertLess(existing, ux)
        self.assertIn("vendor/leaflet/leaflet.css", text)

    def test_workspace_exposes_two_level_edit_state_and_real_conflict_action(self):
        text = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        for token in (
            "situationSaveState",
            "situationPanelState",
            "situationConflictBar",
            "reloadConflict",
            "keepLocalConflict",
        ):
            self.assertIn(token, text)

    def test_map_bootstrap_respects_actual_local_tile_zoom_and_moves_zoom_control(self):
        text = (ROOT / "frontend/static/js/situation-map-bootstrap.js").read_text(encoding="utf-8")
        self.assertIn('maxNativeZoom: 7', text)
        self.assertIn('position: "bottomleft"', text)
        self.assertIn('zoomControl: false', text)

    def test_idle_inspector_is_not_a_permanent_empty_panel(self):
        text = (ROOT / "frontend/static/js/modules/situation-ux.js").read_text(encoding="utf-8")
        self.assertIn("isIdleSelectWorkspace", text)
        self.assertIn('inspector.classList.add("closed")', text)
        self.assertIn("situationSearchResults", text)

    def test_save_does_not_discard_unapplied_form_draft(self):
        text = (ROOT / "frontend/static/js/modules/situation-ux.js").read_text(encoding="utf-8")
        self.assertIn("stopImmediatePropagation", text)
        self.assertIn("先将右侧修改", text)

    def test_conflict_copy_does_not_claim_automatic_merge(self):
        text = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        self.assertIn("系统不会自动合并两个版本", text)


if __name__ == "__main__":
    unittest.main()
