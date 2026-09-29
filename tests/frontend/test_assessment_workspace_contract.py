from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "frontend/templates/pages/results.html").read_text(encoding="utf-8")
JS = (ROOT / "frontend/static/js/modules/results.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/static/css/results.css").read_text(encoding="utf-8")


class AssessmentWorkspaceContractTests(unittest.TestCase):
    def test_assessment_workspace_is_additive_to_the_three_comparison_modes(self) -> None:
        for mode in ("damage", "multi", "configuration", "assessment"):
            self.assertIn(f'data-workspace="{mode}"', HTML)
        self.assertIn('id="assessmentWorkbench"', HTML)
        self.assertIn("评估结果可视化", HTML)

    def test_workspace_contains_the_four_read_only_views_and_states(self) -> None:
        for element_id in (
            "assessmentHeatmap",
            "assessmentCurves",
            "assessmentAirportSelect",
            "assessmentRiskTable",
            "assessmentComparisons",
            "assessmentError",
            "assessmentRefreshButton",
        ):
            self.assertIn(f'id="{element_id}"', HTML)
        for message in (
            "暂无评估落库结果",
            "当前机场暂无评估曲线",
            "未配置独立风险评估模型或正式专家评分",
            "暂无跨场景评估对比记录",
            "评估数据读取失败",
        ):
            self.assertIn(message, JS)

    def test_run_metrics_are_not_mislabeled_as_capability_or_risk(self) -> None:
        self.assertIn("运行期容量占用率", JS)
        self.assertIn("起降容量可用比例", JS)
        self.assertNotIn("保障支撑度恢复曲线", JS)
        self.assertIn("state.workspace !== 'assessment' && view()?.payload", JS)

    def test_workspace_only_consumes_read_endpoints(self) -> None:
        for endpoint in (
            "/api/results/assessment/overview",
            "/api/results/assessment/curves",
            "/api/results/assessment/comparisons",
        ):
            self.assertIn(endpoint, JS)
        self.assertNotIn("/api/assessment/correct", JS)
        self.assertNotIn("/api/assessment/run", JS)

    def test_visuals_and_styles_are_scoped(self) -> None:
        for token in (
            "function renderAssessmentHeatmap",
            "function renderAssessmentCurves",
            "function renderAssessmentRisk",
            "function renderAssessmentComparisons",
        ):
            self.assertIn(token, JS)
        for token in (
            ".assessment-workbench",
            ".assessment-heat-cell",
            ".assessment-risk-badge",
            ".assessment-curves",
        ):
            self.assertIn(token, CSS)


if __name__ == "__main__":
    unittest.main()
