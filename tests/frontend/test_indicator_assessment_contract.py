from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "frontend/static/js/modules/indicators.js").read_text(encoding="utf-8")


class IndicatorAssessmentContractTests(unittest.TestCase):
    def test_indicator_page_exposes_weight_source_and_calculation_state(self) -> None:
        for token in (
            "weight_source",
            "calculation_status",
            "calculation_type",
            "category",
            "权重来源",
            "计算状态",
            "指标分类",
            "计算口径",
        ):
            self.assertIn(token, JS)

    def test_supported_weight_sources_have_one_product_vocabulary(self) -> None:
        self.assertIn("expert: '专家评分'", JS)
        self.assertIn("ahp: 'AHP'", JS)
        self.assertNotIn("专家权重", JS)
        self.assertNotIn("AHP权重", JS)

    def test_draft_round_trip_preserves_assessment_metadata(self) -> None:
        for token in (
            "category: node?.category ?? null",
            "calculation_type: node?.calculation_type ?? null",
            "weight_source: node?.weight_source ?? null",
            "calculation_status: node?.calculation_status ?? 'unavailable'",
        ):
            self.assertIn(token, JS)


if __name__ == "__main__":
    unittest.main()
