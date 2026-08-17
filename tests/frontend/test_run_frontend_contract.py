from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_JS = (ROOT / "frontend/static/js/modules/run.js").read_text(encoding="utf-8")
API_JS = (ROOT / "frontend/static/js/modules/api-client.js").read_text(encoding="utf-8")
RUN_HTML = (ROOT / "frontend/templates/pages/run.html").read_text(encoding="utf-8")
RUN_CSS = (ROOT / "frontend/static/css/run.css").read_text(encoding="utf-8")
BASE_HTML = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")


def extract_async_function(source: str, name: str) -> str:
    start = source.index(f"async function {name}(")
    body_start = source.index("{", start)
    depth = 0
    for index in range(body_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"Unable to extract async function {name}")


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
        self.assertIn("event.payload?.algorithm_progress", RUN_JS)
        self.assertIn("Math.round(rawProgress * 100)", RUN_JS)
        self.assertEqual(5, len(re.findall(r"key: '[^']+', label: '[^']+'", RUN_JS.split("const PREFERENCE_LABELS", 1)[0])))
        self.assertIn("candidate_generation", RUN_JS)
        self.assertIn("quick_evaluation", RUN_JS)
        self.assertIn("candidate_generation_and_quick_evaluation_interleaved", RUN_JS)
        self.assertIn("当前阶段 2–3 / 5", RUN_JS)
        self.assertIn("候选搜索 / 快速评估", RUN_JS)
        self.assertNotRegex(RUN_JS, r"message\.(?:startsWith|includes|match|search)\(")
        self.assertNotRegex(RUN_JS, r"new\s+RegExp\([^\n]*message")

    def test_cluster_and_core_airport_bounds_match_frozen_domain(self):
        self.assertRegex(RUN_JS, r"size\s*<\s*1\s*\|\|\s*size\s*>\s*8")
        self.assertIn("checked.length > 2", RUN_JS)
        self.assertIn("size < config.core_airports.length", RUN_JS)
        self.assertIn('max="8"', RUN_HTML)

    def test_cluster_switch_and_fold_have_independent_non_destructive_state(self):
        self.assertIn("clusterFoldOpen", RUN_JS)
        self.assertIn("clusterHasBeenEnabled", RUN_JS)
        self.assertIn('id="clusterFold"', RUN_HTML)
        self.assertIn('id="clusterSummary"', RUN_HTML)
        cluster_controls = RUN_JS.split("function updateClusterControls()", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("clusterSize.value = ''", cluster_controls)
        self.assertNotIn("input.checked = false", cluster_controls)
        self.assertNotIn("$('advancedFold').classList.remove('open')", RUN_JS)

    def test_fixed_current_run_skeleton_exists_without_a_run(self):
        self.assertNotIn('id="currentEmpty"', RUN_HTML)
        self.assertIn('id="currentContent"', RUN_HTML)
        self.assertNotIn("refs.currentContent.classList.toggle('hidden'", RUN_JS)
        self.assertIn("renderStages(run, events)", RUN_JS)
        self.assertIn("if (!run) return;", RUN_JS.split("function renderCurrentActions(run)", 1)[1].split("\nfunction ", 1)[0])
        render_current = RUN_JS.split("function renderCurrentRun()", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("if (!run)", render_current)
        self.assertEqual(6, len(re.findall(r'class="meta-row"', RUN_HTML)))
        self.assertEqual(5, len(re.findall(r'class="stage pending"', RUN_HTML)))
        for label in ("Run ID", "情境", "损毁场景", "开始时间", "已运行时间", "当前状态"):
            self.assertIn(label, RUN_HTML)
        self.assertIn("尚未开始运行", RUN_HTML)

    def test_terminal_activity_and_failure_stage_use_real_run_state(self):
        self.assertIn("run?.run_config?.cluster_enabled === false", RUN_JS)
        self.assertIn("terminalFailureStageIndex(events)", RUN_JS)
        self.assertIn("group.key === event.stage", RUN_JS)
        for label in (
            "运行任务已进入队列，等待 Worker。",
            "运行完成，结果已持久化",
            "运行失败",
            "运行已取消",
        ):
            self.assertIn(label, RUN_JS)

    def test_internal_algorithm_seed_is_hidden_and_mip_default_is_real(self):
        self.assertNotIn("algorithm_seed", RUN_HTML)
        self.assertNotIn("algorithm_seed", RUN_JS)
        mip_match = re.search(r'id="mipTimeLimit"[^>]*', RUN_HTML)
        self.assertIsNotNone(mip_match)
        self.assertIn('value="120"', mip_match.group(0))

    def test_custom_alpha_is_conditional(self):
        self.assertIn("if (mode === 'custom')", RUN_JS)
        self.assertIn("config.alpha = [", RUN_JS)
        self.assertNotRegex(RUN_JS, r"alpha\s*:\s*\[")

    def test_validation_uses_backend_check_code_not_invented_names(self):
        self.assertIn("VALIDATION_LABELS[check.code]", RUN_JS)
        self.assertIn("result.input_summary", RUN_JS)
        self.assertNotIn("check.check_id", RUN_JS)
        self.assertNotIn("check.name ||", RUN_JS)

    def test_submit_is_bound_to_server_validated_input_fingerprint(self):
        self.assertIn("state.validation?.validated_input_hash", RUN_JS)
        self.assertIn("body.expected_input_hash = validatedInputHash", RUN_JS)
        self.assertIn("validatedInputHash.length !== 64", RUN_JS)

    def test_history_log_inspection_is_stable_across_polling(self):
        self.assertIn("inspectRunId", RUN_JS)
        self.assertIn("activeRunId", RUN_JS)
        self.assertIn("lastSubmittedRunId", RUN_JS)
        self.assertIn("returnToLiveRun", RUN_JS)
        self.assertIn("if (state.inspectRunId)", RUN_JS)
        self.assertIn("返回当前运行", RUN_HTML)

    def test_current_run_terminal_catchup_and_queue_cancel_use_existing_api(self):
        self.assertIn("transitionedToTerminal", RUN_JS)
        self.assertIn("refreshActiveEvents({ force: true })", RUN_JS)
        self.assertIn("if (state.activeRunId !== runId) break", RUN_JS)
        self.assertIn("cancelQueuedRun", RUN_JS)
        self.assertIn("取消排队", RUN_JS)

    def test_refresh_runs_null_safe_terminal_transition(self):
        refresh_runs = extract_async_function(RUN_JS, "refreshRuns")
        self.assertIn("const transitionedToTerminal = Boolean(", refresh_runs)
        self.assertIn("previous\n      && state.activeRun", refresh_runs)
        self.assertNotIn("previous?.run_id === state.activeRun?.run_id", refresh_runs)

        script = f"""
const state = {{
  runs: [],
  activeRun: null,
  activeRunId: null,
  lastSubmittedRunId: null,
  inspectRunId: null,
  inspectRun: null,
}};
let listedRuns = [];
const forcedCalls = [];
const apiFetch = async () => ({{ items: listedRuns }});
const setActiveRun = (run) => {{ state.activeRun = run; state.activeRunId = run?.run_id || null; }};
const refreshActiveEvents = async (options = {{}}) => {{ forcedCalls.push(Boolean(options.force)); }};
const renderQueue = () => {{}};
const renderHistory = () => {{}};
const renderCurrentRun = () => {{}};
const handleError = (error) => {{ throw error; }};

{refresh_runs}

await refreshRuns();
const emptyTransition = forcedCalls.length > 0;
await refreshRuns();
const repeatedEmptyTransition = forcedCalls.length > 0;

state.activeRun = {{ run_id: 'R1', status: 'running' }};
state.activeRunId = 'R1';
listedRuns = [{{ run_id: 'R1', status: 'succeeded' }}];
await refreshRuns();

console.log(JSON.stringify({{
  emptyTransition,
  repeatedEmptyTransition,
  terminalTransition: forcedCalls.includes(true),
}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertFalse(result["emptyTransition"])
        self.assertFalse(result["repeatedEmptyTransition"])
        self.assertTrue(result["terminalTransition"])

    def test_runtime_layout_is_content_driven(self):
        self.assertIn("grid-template-rows: auto auto minmax(250px, 1fr)", RUN_CSS)
        self.assertNotRegex(RUN_CSS, r"(?:38|20)vh")
        self.assertRegex(RUN_CSS, r"\.runpage\s*\{[^}]*overflow-x:\s*hidden;")
        self.assertRegex(RUN_CSS, r"\.runtime\s*\{[^}]*overflow:\s*hidden;")
        self.assertRegex(RUN_CSS, r"\.current-pane\s*\{[^}]*height:\s*2[3-8]\dpx;")

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
