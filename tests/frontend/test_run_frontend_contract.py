from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_JS = (ROOT / "frontend/static/js/modules/run.js").read_text(encoding="utf-8")
API_JS = (ROOT / "frontend/static/js/modules/api-client.js").read_text(encoding="utf-8")
RUN_HTML = (ROOT / "frontend/templates/pages/run.html").read_text(encoding="utf-8")
BASE_HTML = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")


class RunFrontendContractTests(unittest.TestCase):
    def test_uses_only_canonical_run_and_situation_endpoints(self):
        required = (
            "/api/situations?limit=500",
            "/api/runs/validate",
            "/api/runs",
        )
        for endpoint in required:
            self.assertIn(endpoint, RUN_JS)
        self.assertIn("/api/situations/${encodeURIComponent(id)}", RUN_JS)
        self.assertIn("/events?after_seq=", RUN_JS)
        self.assertIn("/cancel", RUN_JS)

        forbidden = (
            "/api/runtime",
            "/api/run/poll",
            "/api/scenes",
            "scene_file",
            "run_params_path",
            "od_distances",
            "result_root",
        )
        for token in forbidden:
            self.assertNotIn(token, RUN_JS)

    def test_frontend_does_not_recreate_business_result_formulas(self):
        forbidden = (
            "completion_ratio",
            "shortfall",
            "unmet",
            "best_run",
            "R1-R0",
            "R2-R1",
            "remaining_ratio_initial",
            "departure_hhi",
        )
        for token in forbidden:
            self.assertNotIn(token, RUN_JS)

    def test_structured_events_drive_stage_display_without_message_parsing(self):
        self.assertIn("event.stage", RUN_JS)
        self.assertIn("event.event", RUN_JS)
        self.assertNotIn("algorithm_progress", RUN_JS)
        self.assertIn("阶段 ${info.activeIndex + 1} / ${STAGE_GROUPS.length}", RUN_JS)
        self.assertIn("candidate_generation", RUN_JS)
        self.assertIn("quick_evaluation", RUN_JS)
        self.assertIn("候选搜索与快速评估", RUN_JS)
        self.assertNotRegex(RUN_JS, r"message\.(?:startsWith|includes|match|search)\(")
        self.assertNotRegex(RUN_JS, r"new\s+RegExp\([^\n]*message")

    def test_cluster_and_core_airport_bounds_match_frozen_domain(self):
        self.assertRegex(RUN_JS, r"size\s*<\s*1\s*\|\|\s*size\s*>\s*8")
        self.assertIn("checked.length > 2", RUN_JS)
        self.assertIn('max="8"', RUN_HTML)

    def test_internal_algorithm_seed_and_fake_mip_default_are_not_exposed(self):
        self.assertNotIn("algorithm_seed", RUN_HTML)
        self.assertNotIn("algorithm_seed", RUN_JS)
        mip_match = re.search(r'id="mipTimeLimit"[^>]*', RUN_HTML)
        self.assertIsNotNone(mip_match)
        self.assertNotIn("value=", mip_match.group(0))
        self.assertNotIn('value="120"', RUN_HTML)

    def test_custom_alpha_is_conditional(self):
        self.assertIn("if (mode === 'custom')", RUN_JS)
        self.assertIn("config.alpha = [", RUN_JS)
        self.assertNotRegex(RUN_JS, r"alpha\s*:\s*\[")

    def test_validation_uses_backend_check_code_not_invented_names(self):
        self.assertIn("check.code || '校验项'", RUN_JS)
        self.assertNotIn("check.check_id", RUN_JS)
        self.assertNotIn("check.name ||", RUN_JS)

    def test_submit_is_bound_to_server_validated_input_fingerprint(self):
        self.assertIn("state.validation?.validated_input_hash", RUN_JS)
        self.assertIn("body.expected_input_hash = validatedInputHash", RUN_JS)
        self.assertIn("validatedInputHash.length !== 64", RUN_JS)

    def test_history_log_inspection_is_stable_across_polling(self):
        self.assertIn("inspectingRunId", RUN_JS)
        self.assertIn("returnToLiveRun", RUN_JS)
        self.assertIn("if (state.inspectingRunId)", RUN_JS)
        self.assertIn("返回当前运行", RUN_HTML)

    def test_successful_history_run_navigates_to_single_run_page(self):
        self.assertIn("window.location.assign(`/runs/${encodeURIComponent(run.run_id)}`)", RUN_JS)
        self.assertNotIn("resultButton.disabled = true", RUN_JS)
        self.assertNotIn("Single Run 页面将在下一切片接入", RUN_JS)

    def test_failed_history_run_has_snapshot_retry_and_log_tools(self):
        self.assertIn("/retry", RUN_JS)
        self.assertIn("retryFailedRun", RUN_JS)
        self.assertIn("不可变冻结输入", RUN_JS)
        for label in ("自动滚动", "回到最新", "复制", "导出日志"):
            self.assertIn(label, RUN_HTML)

    def test_mutations_send_csrf_from_cookie(self):
        self.assertIn("csrftoken", API_JS)
        self.assertIn("X-CSRF-Token", API_JS)
        self.assertIn("credentials: 'same-origin'", API_JS)

    def test_shell_has_only_four_primary_business_nav_labels(self):
        for label in ("情境构建", "指标管理", "算法运行", "结果分析"):
            self.assertIn(label, BASE_HTML)
        self.assertNotIn("张指挥员", BASE_HTML)

    def test_no_placeholder_run_ids_or_fake_runtime_logs(self):
        for token in ("RUN-2026", "RUN-001", "RUN_001", "正在执行模拟退火", "候选解 128"):
            self.assertNotIn(token, RUN_HTML)
            self.assertNotIn(token, RUN_JS)


if __name__ == "__main__":
    unittest.main()
