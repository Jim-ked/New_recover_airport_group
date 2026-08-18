from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class SituationEffectLayoutContractTests(unittest.TestCase):
    def test_map_is_its_own_stacking_context_below_workspaces(self):
        css = (ROOT / "frontend/static/css/situations.css").read_text(encoding="utf-8")
        compact = "".join(css.split())
        self.assertIn("z-index:0", compact)
        self.assertIn("isolation:isolate", compact)
        self.assertIn(".overlay-surface", css)

    def test_primary_map_tools_are_compact_left_tools_and_inspector_is_bounded(self):
        css = (ROOT / "frontend/static/css/situations.css").read_text(encoding="utf-8")
        compact = "".join(css.split())
        self.assertIn(".situation-tools{left:14px;top:14px;width:58px", compact)
        self.assertIn("flex-direction:column", compact)
        self.assertIn('.situation-inspector[data-kind="damage-editor"]{width:360px', compact)
        self.assertNotIn("390px", css)

    def test_situation_actions_follow_current_topbar_overview_and_sidebar_layout(self):
        html = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        self.assertIn("deleteSituationButton", html)
        self.assertIn("overviewEditSituationInfo", html)
        self.assertIn("data-base-data-url", html)
        self.assertNotIn("situationMoreButton", html)

    def test_leaflet_panning_has_world_boundary_and_tile_buffer(self):
        js = (ROOT / "frontend/static/js/modules/situation-map.js").read_text(encoding="utf-8")
        self.assertIn("maxBoundsViscosity: 0.92", js)
        self.assertIn("keepBuffer: 4", js)
        self.assertIn("maxNativeZoom: 7", js)


if __name__ == "__main__":
    unittest.main()
