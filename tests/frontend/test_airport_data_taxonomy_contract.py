from __future__ import annotations
import pathlib, unittest
ROOT = pathlib.Path(__file__).resolve().parents[2]

class AirportDataTaxonomyTests(unittest.TestCase):
    def test_base_data_explicitly_separates_basic_and_operational_data(self):
        js=(ROOT/"frontend/static/js/modules/base-data.js").read_text(encoding="utf-8")
        self.assertIn("基础信息", js)
        self.assertIn("运行保障数据", js)
        self.assertIn('data-airport-pane="basic"', js)
        self.assertIn('data-airport-pane="operations"', js)
        self.assertIn("operational_profile", js)
        self.assertNotIn("/api/situations/working-copy/copy-airport", js)

    def test_situation_airport_editor_labels_operational_working_copy(self):
        js=(ROOT/"frontend/static/js/modules/situations.js").read_text(encoding="utf-8")
        self.assertIn("运行保障", js)
        self.assertIn("state.working", js)
        self.assertIn("tau_reset_windows", js)
        self.assertIn("/api/situations/working-copy/copy-airport", js)
        self.assertIn("canonicalizeWorking(candidate)", js)

if __name__=="__main__": unittest.main()
