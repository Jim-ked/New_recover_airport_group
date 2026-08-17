from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class DamagePresetFrontendContractTests(unittest.TestCase):
    def test_template_loads_quick_fill_module_and_style(self):
        text = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        self.assertIn("damage-presets.css", text)
        self.assertIn("damage-presets.js", text)

    def test_quick_fill_is_ui_only_and_uses_existing_capacity_event_fields(self):
        text = (ROOT / "frontend/static/js/modules/damage-presets.js").read_text(encoding="utf-8")
        self.assertIn("remainingPercent: 80", text)
        self.assertIn("remainingPercent: 50", text)
        self.assertIn("remainingPercent: 20", text)
        self.assertIn(".ev-cap", text)
        self.assertIn(".ev-closed", text)
        self.assertIn("capacity", text)
        self.assertNotIn("/api/damage-template", text)
        self.assertNotIn("DamageProjection", text)

    def test_existing_events_are_not_silently_overwritten(self):
        text = (ROOT / "frontend/static/js/modules/damage-presets.js").read_text(encoding="utf-8")
        self.assertIn("globalThis.confirm", text)
        self.assertIn("会替换这些尚未应用的事件", text)

    def test_unsaved_working_copy_is_not_resolved_against_stale_saved_capacity(self):
        text = (ROOT / "frontend/static/js/modules/damage-presets.js").read_text(encoding="utf-8")
        self.assertIn("当前 Working Copy 有未保存修改", text)
        self.assertIn("/api/situations/", text)


if __name__ == "__main__":
    unittest.main()
