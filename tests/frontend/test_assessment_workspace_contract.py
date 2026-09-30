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

    def test_workspace_contains_capacity_and_indicator_evaluation_views(self) -> None:
        for element_id in (
            "assessmentHeatmap",
            "assessmentCurves",
            "assessmentAirportSelect",
            "assessmentRiskTable",
            "assessmentComparisons",
            "assessmentRunSelect",
            "assessmentIndicatorTable",
            "assessmentComposite",
            "assessmentError",
            "assessmentRefreshButton",
        ):
            self.assertIn(f'id="{element_id}"', HTML)
        for message in (
            "暂无评估落库结果",
            "当前机场暂无评估曲线",
            "暂无正式指标评价结果",
            "正式评价输入不完整",
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
            "/api/results/assessment/evaluations",
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
            "function renderAssessmentEvaluation",
        ):
            self.assertIn(token, JS)
        for token in (
            ".assessment-workbench",
            ".assessment-heat-cell",
            ".assessment-risk-badge",
            ".assessment-curves",
            ".assessment-grid-evaluation",
            ".assessment-indicator-table",
            ".assessment-composite",
        ):
            self.assertIn(token, CSS)
        self.assertIn(
            ".assessment-workbench{grid-template-rows:auto minmax(260px,1fr) ",
            CSS,
        )
        self.assertIn(
            "@media(max-width:1180px){.results-page.assessment-active{overflow:auto}.assessment-workbench{grid-template-rows:auto auto auto auto",
            CSS,
        )

    def test_indicator_evaluation_keeps_missing_data_distinct_from_zero(self) -> None:
        render = JS[JS.index("function renderAssessmentEvaluation"):JS.index("function renderAssessmentRisk")]
        self.assertIn("evaluation.status !== 'available'", render)
        self.assertIn("不可用：", render)
        self.assertNotIn("|| 0", render)
        self.assertIn("weight_source", render)
        self.assertIn("snapshot_hash", render)
        self.assertIn("metrics_hash", render)

    def test_formal_run_risk_is_not_mixed_with_legacy_airport_risk(self) -> None:
        render = JS[JS.index("function renderAssessmentRisk"):JS.index("function renderAssessmentComparisons")]
        self.assertIn("if (evaluations.length)", render)
        self.assertIn("return;", render)


if __name__ == "__main__":
    unittest.main()
