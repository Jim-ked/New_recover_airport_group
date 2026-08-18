from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class DamagePresetFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "frontend/static/js/modules/situations.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "frontend/static/css/situations.css").read_text(encoding="utf-8")

    def test_template_loads_current_situation_module_and_integrated_style(self):
        self.assertIn("css/situations.css", self.html)
        self.assertIn("js/modules/situations.js", self.html)
        self.assertIn(".damage-preset", self.css)
        self.assertNotIn("damage-presets.js", self.html)

    def test_quick_fill_is_ui_only_and_uses_existing_capacity_event_fields(self):
        self.assertIn("ratio: 0.80", self.js)
        self.assertIn("ratio: 0.50", self.js)
        self.assertIn("ratio: 0.20", self.js)
        self.assertIn(".ev-cap", self.js)
        self.assertIn(".ev-closed", self.js)
        self.assertIn("remaining_capacity_per_window", self.js)
        self.assertNotIn("/api/damage-template", self.js)
        self.assertNotIn("DamageProjection", self.js)

    def test_existing_events_are_not_silently_overwritten(self):
        self.assertIn("confirmAction", self.js)
        self.assertIn("预设将替换当前草稿中的事件", self.js)

    def test_unsaved_working_copy_is_not_resolved_against_stale_saved_capacity(self):
        start = self.js.index("async function applyDamagePresetDraft()")
        end = self.js.index("\nfunction renderDamageEditor", start)
        function_body = self.js[start:end]
        self.assertIn("airportItem(airportId).operational_profile.capacity_per_window", function_body)
        self.assertNotIn("apiFetch(", function_body)


if __name__ == "__main__":
    unittest.main()
