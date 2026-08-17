from __future__ import annotations
import pathlib, unittest
ROOT = pathlib.Path(__file__).resolve().parents[2]

class AirportDataTaxonomyTests(unittest.TestCase):
    def test_base_data_explicitly_separates_basic_and_operational_data(self):
        js=(ROOT/"frontend/static/js/modules/base-data-ux.js").read_text(encoding="utf-8")
        self.assertIn("基础信息", js)
        self.assertIn("运行保障数据", js)
        self.assertIn("可复用运行基线", js)
        self.assertIn("复制到 Situation 后成为独立 Working Copy", js)

    def test_situation_airport_editor_labels_operational_working_copy(self):
        js=(ROOT/"frontend/static/js/modules/situation-panel-polish.js").read_text(encoding="utf-8")
        self.assertIn("运行保障数据", js)
        self.assertIn("当前 Situation Working Copy", js)
        self.assertIn("整备用时（时间窗）", js)

if __name__=="__main__": unittest.main()
