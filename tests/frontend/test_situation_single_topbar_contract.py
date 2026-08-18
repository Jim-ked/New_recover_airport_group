from __future__ import annotations
import pathlib, unittest
ROOT = pathlib.Path(__file__).resolve().parents[2]

class SituationSingleTopbarTests(unittest.TestCase):
    def test_shell_has_page_context_slot_and_situation_has_no_second_header(self):
        base=(ROOT/"frontend/templates/base.html").read_text(encoding="utf-8")
        sit=(ROOT/"frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        self.assertIn("block topbar_context", base)
        self.assertIn("situation-top-actions", sit)
        self.assertNotIn('class="situation-head', sit)

    def test_base_data_is_contextual_lower_left_entry(self):
        base=(ROOT/"frontend/templates/base.html").read_text(encoding="utf-8")
        self.assertIn("sidebar-footer", base)
        self.assertIn("基础数据", base)
        self.assertIn("base_data_active", base)

    def test_situation_inspector_is_narrower_than_old_390px_design(self):
        css=(ROOT/"frontend/static/css/situations.css").read_text(encoding="utf-8")
        compact="".join(css.split())
        self.assertIn(".situation-inspector{top:14px;right:12px;width:340px", compact)
        self.assertIn('data-kind="airport-candidates"]{width:360px', compact)
        self.assertNotIn("390px", css)

if __name__=="__main__": unittest.main()
