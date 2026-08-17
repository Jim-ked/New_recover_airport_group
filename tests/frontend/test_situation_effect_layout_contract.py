from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class SituationEffectLayoutContractTests(unittest.TestCase):
    def test_map_is_its_own_stacking_context_below_workspaces(self):
        css = (ROOT / "frontend/static/css/situation-workspace.css").read_text(encoding="utf-8")
        self.assertIn("z-index:0", css)
        self.assertIn("contain:paint", css)
        self.assertIn(".overlay-surface", css)

    def test_primary_map_tools_are_horizontal_and_inspector_is_bounded(self):
        css = (ROOT / "frontend/static/css/situation-workspace.css").read_text(encoding="utf-8")
        self.assertIn("flex-direction:row", css)
        self.assertIn("--inspector-width:clamp(320px,23vw,360px)", css)

    def test_low_frequency_situation_actions_are_under_more(self):
        html = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        self.assertIn("situationMoreButton", html)
        self.assertIn("编辑情境信息", html)
        self.assertIn("基础数据管理", html)
        self.assertIn("删除当前情境", html)

    def test_leaflet_panning_has_world_boundary_and_tile_buffer(self):
        js = (ROOT / "frontend/static/js/situation-map-bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("maxBoundsViscosity: 0.92", js)
        self.assertIn("keepBuffer: 4", js)
        self.assertIn("maxNativeZoom: 7", js)


if __name__ == "__main__":
    unittest.main()
