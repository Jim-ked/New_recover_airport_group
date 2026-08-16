from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class F3IndicatorFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
        cls.html = (ROOT / "frontend/templates/pages/indicators.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "frontend/static/css/indicators.css").read_text(encoding="utf-8")
        cls.js = (ROOT / "frontend/static/js/modules/indicators.js").read_text(encoding="utf-8")
        cls.ui = (ROOT / "backend/web/flask_ui.py").read_text(encoding="utf-8")

    def test_indicator_is_real_primary_navigation_and_page_route(self):
        self.assertIn("ui_v1.indicators_page", self.base)
        self.assertIn('@bp.get("/indicators")', self.ui)
        self.assertIn('active_nav="indicators"', self.ui)
        self.assertIn("js/modules/indicators.js", self.html)

    def test_default_and_user_created_sets_are_both_first_class(self):
        self.assertIn("/api/indicator-sets", self.js)
        self.assertIn("/api/indicator-sets/drafts", self.js)
        self.assertIn("新建指标集", self.html)
        self.assertIn("复制既有已发布指标集", self.html)
        self.assertIn("系统默认 V1.1 保持不变", self.js)
        self.assertIn("s.is_default", self.js)

    def test_three_level_tree_and_l3_only_editor_are_explicit(self):
        for element_id in ("indicatorL1", "indicatorL2", "indicatorL3"):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("新增三级指标", self.html)
        self.assertIn("x.level===3", self.js)
        self.assertIn("x.is_core", self.js)

    def test_expert_scoring_is_server_backed_and_normalized(self):
        self.assertIn("/api/expert-scores/", self.js)
        self.assertIn("/api/experts", self.js)
        self.assertIn("已提交专家均值", self.js)
        self.assertIn("同一二级指标", self.js)
        self.assertIn("status==='submitted'", self.js)

    def test_indicator_page_does_not_present_runtime_metric_values(self):
        self.assertNotIn("/api/runs", self.js)
        self.assertNotIn("/api/results", self.js)
        self.assertIn("不参与算法运行", self.html)

    def test_indicator_page_owns_layout_not_global_shell(self):
        forbidden = [".app-shell", ".topbar{", ".sidebar{", ".nav{"]
        for token in forbidden:
            self.assertNotIn(token, self.css)
        self.assertNotRegex(self.css, r"font-size:\s*(?:[1-9](?:\.\d+)?)px")


if __name__ == "__main__":
    unittest.main()
