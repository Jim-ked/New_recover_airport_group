from __future__ import annotations
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]

class SituationLayerScopeTests(unittest.TestCase):
    def test_extended_layers_are_read_only_context_sources(self):
        js=(ROOT/"frontend/static/js/modules/situation-display-layers.js").read_text(encoding="utf-8")
        self.assertIn("/api/airports", js)
        self.assertIn("/api/missions", js)
        self.assertIn("/api/situations", js)
        self.assertNotIn("method: \"POST\"", js)
        self.assertNotIn("method: 'POST'", js)

    def test_map_instance_is_exposed_for_display_layers(self):
        js=(ROOT/"frontend/static/js/situation-map-bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("__situationLeafletMap", js)

if __name__=="__main__":
    unittest.main()
