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
        self.assertIn("formatPercent(rawProgress, { digits: 0 })", RUN_JS)
        self.assertEqual(5, len(re.findall(r"key: '[^']+', label: '[^']+'", RUN_JS.split("const PREFERENCE_LABELS", 1)[0])))
        self.assertIn("candidate_generation", RUN_JS)
        self.assertIn("quick_evaluation", RUN_JS)
        self.assertIn("candidate_generation_and_quick_evaluation_interleaved", RUN_JS)
        self.assertIn("当前阶段 2–3 / 5", RUN_JS)
        self.assertIn("候选搜索 / 快速评估", RUN_JS)
        self.assertNotRegex(RUN_JS, r"message\.(?:startsWith|includes|match|search)\(")
        self.assertNotRegex(RUN_JS, r"new\s+RegExp\([^\n]*message")

    def test_user_visible_run_logs_use_one_structured_chinese_formatter(self):
        formatter_block = RUN_JS[
            RUN_JS.index("const LOG_LEVEL_LABELS"):RUN_JS.index("const PREFERENCE_LABELS")
        ]
        render_events = RUN_JS.split("function renderEvents()", 1)[1].split("\nfunction ", 1)[0]
        log_text = RUN_JS.split("function logText()", 1)[1].split("\nfunction ", 1)[0]
        current_activity = RUN_JS.split("function currentActivity(run, events)", 1)[1].split("\nfunction ", 1)[0]

        self.assertNotIn("event.message", RUN_JS)
        self.assertNotRegex(RUN_JS, r"message\.(?:startsWith|includes|match|search)\(")
        self.assertIn("formatRunLogEvent(event, run)", render_events)
        self.assertIn("formatRunLogEvent(event, run)", log_text)
        self.assertIn("const text = logText();", extract_async_function(RUN_JS, "copyLog"))
        self.assertIn("const text = logText();", RUN_JS.split("function exportLog()", 1)[1].split("\nfunction ", 1)[0])
        self.assertIn("formatRunLogMessage", current_activity)
        self.assertNotIn("latest.message", current_activity)

        script = f"""
const formatDecimal = (value, options = {{}}) => Number(value).toFixed(options.minimumFractionDigits || 0);
const formatPercent = (value, {{digits = 1}} = {{}}) => `${{(Number(value) * 100).toFixed(digits)}}%`;
{formatter_block}

const event = (overrides = {{}}) => ({{
  time: '20:15:21',
  level: 'INFO',
  stage: 'data_preparation',
  event: 'algorithm_stage',
  message: 'THIS ENGLISH MESSAGE MUST NEVER BE SHOWN',
  payload: {{internal_stage: 'prepare'}},
  ...overrides,
}});
const run = (code = null) => ({{failure: code ? {{code}} : null}});

const normal = ['prepare', 'cluster', 'paths', 'model', 'solve', 'solution', 'complete']
  .map((internal_stage) => formatRunLogEvent(event({{
    stage: internal_stage === 'prepare' ? 'data_preparation'
      : ['cluster', 'paths'].includes(internal_stage) ? 'candidate_generation'
      : ['model', 'solve'].includes(internal_stage) ? 'exact_optimization'
      : 'persistence',
    payload: {{internal_stage}},
  }})));
const failures = Object.fromEntries([
  'INFEASIBLE',
  'NO_FEASIBLE_SOLUTION',
  'MODEL_CONFIGURATION_ERROR',
  'ALGORITHM_ERROR',
  'WORKER_ERROR',
  'SNAPSHOT_MISSING',
  'FUTURE_FAILURE',
].map((failure_code) => [failure_code, formatRunLogEvent(event({{
  level: 'ERROR', event: 'run_failed', payload: {{failure_code}},
}}), run())]));
const solverStatuses = Object.fromEntries([
  'unknown', 'userinterrupt', 'nodelimit', 'totalnodelimit', 'stallnodelimit',
  'timelimit', 'memlimit', 'gaplimit', 'sollimit', 'bestsollimit',
  'restartlimit', 'primallimit', 'duallimit', 'optimal', 'infeasible',
  'unbounded', 'inforunbd', 'future_status',
].map((solver_status) => [solver_status, formatRunLogEvent(event({{
  event: 'run_succeeded', stage: 'persistence',
  payload: {{solver_status, gap: 0.0128, solve_time_s: 120}},
}}))]));
console.log(JSON.stringify({{
  normal,
  worker: formatRunLogEvent(event({{event: 'worker_started'}})),
  succeeded: formatRunLogEvent(event({{event: 'run_succeeded', stage: 'persistence'}})),
  cancelled: formatRunLogEvent(event({{level: 'WARNING', event: 'run_cancelled', stage: 'persistence'}})),
  assessmentAvailable: formatRunLogEvent(event({{
    event: 'run_assessment_persisted', stage: 'persistence', payload: {{assessment_status: 'available'}},
  }})),
  assessmentUnavailable: formatRunLogEvent(event({{
    level: 'WARNING', event: 'run_assessment_persisted', stage: 'persistence',
    payload: {{assessment_status: 'unavailable', unavailable_reason: 'english raw reason'}},
  }})),
  assessmentFailed: formatRunLogEvent(event({{
    level: 'WARNING', event: 'run_assessment_failed', stage: 'persistence',
    payload: {{error: 'english raw error'}},
  }})),
  snapshotFromRun: formatRunLogEvent(event({{
    level: 'ERROR', event: 'run_failed', payload: {{}},
  }}), run('SNAPSHOT_MISSING')),
  failures,
  solverStatuses,
  unknownInfo: formatRunLogEvent(event({{event: 'future_event', payload: {{}}}})),
  unknownError: formatRunLogEvent(event({{level: 'ERROR', event: 'future_event', payload: {{}}}})),
  unknownErrorCode: formatRunLogEvent(event({{
    level: 'ERROR', event: 'future_failure_event', payload: {{failure_code: 'FUTURE_EVENT_FAILURE'}},
  }})),
}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)

        normal_text = "\n".join(result["normal"])
        for phrase in (
            "数据准备：正在构建运行输入",
            "候选搜索：应用遗传算法、模拟退火算法和粒子群算法进行候选方案搜索与优化",
            "路径构建：正在生成可行航次路径",
            "模型构建：正在建立调度优化模型",
            "精确求解：正在执行调度方案求解",
            "结果处理：正在校验并整理求解结果",
            "算法求解完成",
        ):
            self.assertIn(phrase, normal_text)
        for algorithm in ("遗传算法", "模拟退火算法", "粒子群算法"):
            self.assertEqual(1, normal_text.count(algorithm))
        self.assertNotIn("遗传算法", "\n".join((result["normal"][0], result["normal"][2])))

        self.assertIn("运行服务已接收任务，开始执行本次运行", result["worker"])
        self.assertIn("运行完成：结果已生成并保存", result["succeeded"])
        self.assertIn("运行已取消", result["cancelled"])
        self.assertIn("指标评估结果已生成并保存", result["assessmentAvailable"])
        self.assertIn("指标评估暂不可用", result["assessmentUnavailable"])
        self.assertIn("指标评估结果保存失败", result["assessmentFailed"])
        self.assertIn("冻结输入不存在或无法读取", result["snapshotFromRun"])

        expected_failures = {
            "INFEASIBLE": "当前约束条件下模型不可行",
            "NO_FEASIBLE_SOLUTION": "未获得可行调度方案",
            "MODEL_CONFIGURATION_ERROR": "模型配置或运行输入不符合求解要求",
            "ALGORITHM_ERROR": "算法求解过程中发生异常",
            "WORKER_ERROR": "运行服务执行过程中发生异常",
            "SNAPSHOT_MISSING": "冻结输入不存在或无法读取",
            "FUTURE_FAILURE": "未识别的错误类型（错误代码：FUTURE_FAILURE）",
        }
        for code, phrase in expected_failures.items():
            self.assertIn(phrase, result["failures"][code])

        expected_statuses = {
            "unknown": "求解状态未知",
            "userinterrupt": "求解已被中断",
            "nodelimit": "达到节点数量限制",
            "totalnodelimit": "达到总节点数量限制",
            "stallnodelimit": "达到停滞节点限制",
            "timelimit": "达到求解时限",
            "memlimit": "达到内存限制",
            "gaplimit": "达到最优性间隙限制",
            "sollimit": "达到可行解数量限制",
            "bestsollimit": "达到改进解数量限制",
            "restartlimit": "达到重启次数限制",
            "primallimit": "达到原始目标限制",
            "duallimit": "达到对偶目标限制",
            "optimal": "已获得最优解",
            "infeasible": "模型不可行",
            "unbounded": "模型无界",
            "inforunbd": "模型不可行或无界",
            "future_status": "求解器返回未识别状态",
        }
        for status, phrase in expected_statuses.items():
            self.assertIn(phrase, result["solverStatuses"][status])
            self.assertNotIn("THIS ENGLISH", result["solverStatuses"][status])
        self.assertIn("最优性间隙：1.28%", result["solverStatuses"]["timelimit"])
        self.assertIn("求解用时：120 秒", result["solverStatuses"]["timelimit"])

        self.assertIn("信息｜", result["worker"])
        self.assertIn("警告｜", result["cancelled"])
        self.assertIn("错误｜", result["failures"]["INFEASIBLE"])
        for text in (
            normal_text,
            result["worker"],
            result["succeeded"],
            result["cancelled"],
            result["assessmentUnavailable"],
            result["assessmentFailed"],
            result["unknownInfo"],
            result["unknownError"],
        ):
            self.assertNotRegex(text, r"\b(?:DEBUG|INFO|WARNING|ERROR)\b")
            self.assertNotIn("THIS ENGLISH", text)
        self.assertIn("运行状态已更新", result["unknownInfo"])
        self.assertIn("运行发生未识别异常", result["unknownError"])
        self.assertIn("未识别的错误类型（错误代码：FUTURE_EVENT_FAILURE）", result["unknownErrorCode"])

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
            "运行服务当前不可用",
            "运行任务已进入队列，等待运行服务接收。",
            "运行完成，结果已保存",
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

    def test_versioned_objective_profile_is_submitted_but_not_presented_as_help_copy(self):
        self.assertIn('data-objective-profile-id="{{ objective_profile.profile_id }}"', RUN_HTML)
        self.assertIn("objective_profile_id: objectiveProfile.id", RUN_JS)
        for internal_copy in (
            "目标参数配置",
            "{{ objective_profile.label }}",
            "研究目标函数 v1",
            "固定15分钟时间粒度",
            "research_objective_v1",
            "preflight",
        ):
            self.assertNotIn(internal_copy, RUN_HTML)
        self.assertNotIn("objectiveProfile.id}", RUN_JS)
        self.assertNotIn("'等待 Worker'", RUN_JS)
        self.assertNotIn("持久化", RUN_JS)
        self.assertIn("求解时限 ${formatDecimal(Number(time))}秒", RUN_JS)

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

    def test_admin_history_identifies_run_owner_without_emphasizing_it_for_operators(self):
        self.assertIn('data-admin-owner-column', RUN_HTML)
        self.assertIn('>提交用户</th>', RUN_HTML)
        self.assertIn("isAdmin: false", RUN_JS)
        self.assertIn("state.isAdmin = account?.is_admin === true", RUN_JS)
        self.assertIn("run.owner_user_id", RUN_JS)
        self.assertIn("column.hidden = !state.isAdmin", RUN_JS)
        self.assertIn("cell.colSpan = state.isAdmin ? 10 : 9", RUN_JS)

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
const refreshWorkerStatus = async () => ({{ connected: false }});
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
        self.assertRegex(
            RUN_CSS,
            r"@media\s*\(max-width:\s*1180px\)\s*\{[^@]*\.runpage\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);",
        )

    def test_successful_history_run_navigates_to_single_run_page(self):
        self.assertIn("window.location.assign(`/runs/${encodeURIComponent(run.run_id)}`)", RUN_JS)
        self.assertNotIn("resultButton.disabled = true", RUN_JS)
        self.assertNotIn("Single Run 页面将在下一切片接入", RUN_JS)

    def test_failed_history_run_has_snapshot_retry_and_log_tools(self):
        self.assertIn("/retry", RUN_JS)
        self.assertIn("retryFailedRun", RUN_JS)
        self.assertIn("使用该失败运行的原始输入创建新运行", RUN_JS)
        for label in ("自动滚动", "回到最新", "复制", "导出日志"):
            self.assertIn(label, RUN_HTML)

    def test_terminal_history_delete_is_permission_gated_confirmed_and_refreshes(self):
        render_history = RUN_JS.split("function renderHistory()", 1)[1].split("\nfunction ", 1)[0]
        delete_run = extract_async_function(RUN_JS, "deleteHistoricalRun")

        self.assertIn("can('runs.delete')", render_history)
        self.assertIn("deleteHistoricalRun(run)", render_history)
        self.assertIn("run.run_id", delete_run)
        self.assertIn("该运行及其结果会被永久删除", delete_run)
        self.assertIn("method: 'DELETE'", delete_run)
        self.assertIn("await refreshRuns()", delete_run)
        self.assertIn("state.inspectRunId === run.run_id", delete_run)
        self.assertIn("state.activeRunId === run.run_id", delete_run)

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
