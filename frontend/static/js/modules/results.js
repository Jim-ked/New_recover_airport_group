import { apiFetch, apiDownload, saveBlob } from './api-client.js';
import { getAccount } from './account-context.js';
import { formatDecimal } from './number-display.js';

const $ = (id) => document.getElementById(id);
const MODES = ['damage', 'multi', 'configuration'];
const colors = ['#55a8ed', '#ef9a3c', '#70bd61', '#a887d5', '#d7bd55', '#55c7bf'];
const modeLabels = { damage: '损毁与组群', multi: '多损毁', configuration: '方案配置' };
const configLabels = {
  cluster_enabled: '启用组群', cluster_size: '组群规模', core_airports: '核心机场',
  preference_mode: '优化偏好', alpha: '目标权重 α',
};

function createDraft() {
  return { damageIndex: null, baseRunId: null, selectedRunIds: new Set(), comparableRuns: [], loading: false, search: '' };
}
function createWorkspaceState() {
  return {
    payload: null, selection: null, draft: createDraft(), activeDamageStage: 'damage',
    seriesKind: 'departures', observation: 'trend', analysisTheme: 'tasks', windowStart: 0,
  };
}
const state = {
  workspace: 'damage', runs: [], runById: new Map(), damageCandidates: [], canExport: false,
  status: 'loading', error: null,
  workspaceStates: {
    damage: createWorkspaceState(),
    multi: createWorkspaceState(),
    configuration: createWorkspaceState(),
  },
  assessment: { status: 'idle', overview: null, curves: null, comparisons: null, airportId: null, error: null },
};
const refs = {
  page: $('resultsPage'), conditionTitle: $('conditionTitle'), conditionFacts: $('conditionFacts'),
  focusControls: $('focusControls'), focusBaseline: $('focusBaselineRun'), focusComparison: $('focusComparisonRun'),
  runLink: $('resultsRunLink'), change: $('changeConditionButton'), modeSpecific: $('modeSpecific'),
  damageStages: $('damageStageSummary'), multiOverview: $('multiOverview'), configDifferences: $('configurationDifferences'),
  chart: $('resultsChart'), chartTitle: $('resultsChartTitle'), chartSubtitle: $('resultsChartSubtitle'), legend: $('resultsLegend'),
  timeNav: $('resultsTimeNavigation'), timeRange: $('resultsTimeRange'), timeStart: $('resultsTimeStart'),
  valueComparison: $('resultsValueComparison'), focusRelation: $('focusRelation'),
  themeContent: $('resultsThemeContent'), conclusions: $('resultsConclusions'),
  overlay: $('resultsOverlay'), overlayBody: $('resultsOverlayBody'), search: $('resultsRunSearch'),
  apply: $('applyResultsComparison'), error: $('resultsError'),
  exportWrap: $('resultsExport'), exportButton: $('resultsExportButton'), exportMenu: $('resultsExportMenu'),
  comparisonCondition: $('comparisonCondition'), primary: $('resultsPrimary'), analysis: $('resultsAnalysis'),
  assessmentWorkbench: $('assessmentWorkbench'), assessmentError: $('assessmentError'),
  assessmentRefresh: $('assessmentRefreshButton'), assessmentHeatmap: $('assessmentHeatmap'),
  assessmentCurves: $('assessmentCurves'), assessmentCurveLegend: $('assessmentCurveLegend'),
  assessmentAirportSelect: $('assessmentAirportSelect'), assessmentRiskTable: $('assessmentRiskTable'),
  assessmentComparisons: $('assessmentComparisons'),
};

function view() { return state.workspaceStates[state.workspace]; }
function esc(value) { const node = document.createElement('span'); node.textContent = String(value ?? ''); return node.innerHTML; }
function shortRunId(value) { const text = String(value || ''); return text.length > 14 ? `${text.substring(0, 6)}…${text.substring(text.length - 5)}` : text; }
function number(value, digits = 2) { return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }); }
function displayValue(value, { formatter = null, comparable = true, notApplicable = false } = {}) {
  if (notApplicable) return '不适用';
  if (!comparable) return '不可直接比较';
  if (value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value))) return '未记录';
  return formatter ? formatter(value) : String(value);
}
function signed(value, digits = 2) {
  if (value === null || value === undefined) return '未记录';
  const n = Number(value); return `${n > 0 ? '+' : ''}${number(n, digits)}`;
}
function compactStageFacts(focus) {
  const operational = [
    ['履行', focus?.values?.fulfilled_sorties_total?.delta],
    ['缺口', focus?.values?.unmet_sorties_total?.delta],
    ['出动', focus?.values?.scheduled_sorties_total?.delta],
  ];
  const changed = []; const zeroLabels = []; const unavailable = [];
  for (const [label, raw] of operational) {
    if (raw === null || raw === undefined || !Number.isFinite(Number(raw))) {
      unavailable.push({ text: `${label} 未记录`, state: 'missing', title: `${label}差值未记录` });
      continue;
    }
    const value = Number(raw);
    if (value === 0) zeroLabels.push(label);
    else changed.push({ text: `${label} ${value > 0 ? '+' : ''}${number(value, 0)}`, state: 'changed', title: `${label}差值 ${number(value, 0)}` });
  }
  const stable = [];
  if (zeroLabels.length === operational.length) {
    stable.push({ text: '履行、缺口、出动均未变化', state: 'zero', title: '三项正式差值均为 0' });
  } else if (zeroLabels.length) {
    stable.push({ text: `${zeroLabels.join('、')}未变化`, state: 'zero', title: `${zeroLabels.join('、')}正式差值为 0` });
  }

  const objective = focus?.objective_components?.J || {};
  if (!objective.recorded) {
    unavailable.push({ text: 'J 未记录', state: 'missing', title: '综合目标值差值未记录' });
  } else if (objective.definition_comparable === false) {
    unavailable.push({ text: 'J 不可比', state: 'incomparable', title: '目标定义不一致，J 不可直接比较' });
  } else if (objective.delta === null || objective.delta === undefined || !Number.isFinite(Number(objective.delta))) {
    unavailable.push({ text: 'J 未记录', state: 'missing', title: '综合目标值差值未记录' });
  } else if (Number(objective.delta) === 0) {
    stable.push({ text: 'J 未变化', state: 'zero', title: 'J 正式差值为 0.0000' });
  } else {
    const value = Number(objective.delta);
    changed.push({ text: `J ${value > 0 ? '+' : ''}${number(value, 2)}`, state: 'changed', title: `J 完整差值 ${value > 0 ? '+' : ''}${number(value, 4)}` });
  }
  return [...changed, ...stable, ...unavailable];
}
function solverStatusSummary(technical) {
  const status = technical?.solver_status;
  if (!status) return { text: '求解状态未记录', detail: '未记录求解终止状态' };
  if (String(status).toLowerCase() === 'optimal') return { text: '已证最优', detail: '求解终止状态：optimal' };
  const statusLabels = { gaplimit: '达到 Gap 限制', timelimit: '达到时间限制' };
  const label = statusLabels[String(status).toLowerCase()] || String(status);
  const gap = technical?.gap;
  const gapDetail = gap === null || gap === undefined || !Number.isFinite(Number(gap)) ? '' : `；Gap ${number(Number(gap) * 100, 2)}%`;
  return { text: '可行解，未证最优', detail: `求解终止状态：${label}${gapDetail}。页面展示已取得的可行值。` };
}
function runIdentity(runId) {
  const run = state.runById.get(runId) || {};
  const shortId = shortRunId(runId);
  const damageName = run.damage_scenario?.name || (run.run_config?.damage_scenario_id ? run.run_config.damage_scenario_id : '无损毁');
  const ids = view().payload?.run_ids || Object.values(view().payload?.roles || {});
  const index = Math.max(0, ids.indexOf(runId));
  if (state.workspace === 'multi') {
    const primary = `D${index}`;
    return { primary, chips: [`${primary} ${damageName}`], secondary: shortId, context: damageName };
  }
  if (state.workspace === 'configuration') {
    const config = run.run_config || {};
    const cluster = config.cluster_enabled ? `组群${config.cluster_size ? config.cluster_size : ''}` : '未组群';
    const baselineId = view().payload?.baseline_run_id || view().selection?.baseline_run_id;
    const alternatives = ids.filter((id) => id !== baselineId);
    const alternativeIndex = Math.max(0, alternatives.indexOf(runId));
    const primary = runId === baselineId ? '基准方案' : `方案${alternativeIndex + 2}`;
    return { primary, chips: [primary, cluster], secondary: shortId, context: cluster };
  }
  if (state.workspace === 'damage') {
    const role = Object.entries(view().payload?.roles || {}).find((entry) => entry[1] === runId)?.[0];
    const cluster = run.run_config?.cluster_enabled ? '组群' : '未组群';
    const primary = role || shortId;
    return { primary, chips: [primary, damageName, cluster], secondary: shortId, context: `${damageName} · ${cluster}` };
  }
  const situation = run.situation?.name || run.situation_name || run.situation?.situation_id || '';
  return { primary: shortId, chips: [shortId], secondary: situation, context: situation };
}
function selectionLabel(runId) {
  const identity = runIdentity(runId);
  return [identity.primary, identity.context].filter(Boolean).join(' · ');
}

const ASSESSMENT_SCENARIO_LABELS = {
  low: '低强度', medium: '中强度', high: '高强度', extreme: '极重',
  local_persistent: '局部持续', custom: '自定义',
};
const ASSESSMENT_RISK_LABELS = { 1: '1级 低', 2: '2级 较低', 3: '3级 中', 4: '4级 较高', 5: '5级 高' };
function assessmentScenarioLabel(value) { return ASSESSMENT_SCENARIO_LABELS[value] || value || '—'; }
function assessmentAirportName(airportId) { return state.assessment.overview?.airport_names?.[airportId] || airportId || '—'; }
function assessmentComparisonLabel(airportId, scenario) {
  return airportId ? `${assessmentAirportName(airportId)} · ${assessmentScenarioLabel(scenario)}` : assessmentScenarioLabel(scenario);
}
function assessmentHeatColor(value) {
  const v = Math.max(0, Math.min(1, Number(value) || 0));
  const low = [18, 54, 82]; const high = [70, 154, 105];
  const mix = (index) => Math.round(low[index] + (high[index] - low[index]) * v);
  return `rgb(${mix(0)},${mix(1)},${mix(2)})`;
}
function assessmentCellValue(cell) {
  if (cell?.metric_kind === 'capacity_availability_v1') {
    return cell.matrix_value == null ? null : Number(cell.matrix_value);
  }
  return cell?.support_index == null ? null : Number(cell.support_index);
}
function renderAssessmentHeatmap() {
  const overview = state.assessment.overview;
  if (!overview?.airport_ids?.length || !overview?.scenario_types?.length) {
    refs.assessmentHeatmap.innerHTML = '<div class="results-placeholder">暂无评估落库结果</div>'; return;
  }
  const scenarios = overview.scenario_types;
  const header = scenarios.map((scenario) => `<th>${esc(assessmentScenarioLabel(scenario))}</th>`).join('');
  const rows = overview.airport_ids.map((airportId) => {
    const cells = scenarios.map((scenario) => {
      const cell = overview.cells?.[airportId]?.[scenario];
      if (!cell) return '<td class="assessment-heat-cell assessment-heat-empty" title="暂无评估">—</td>';
      const value = assessmentCellValue(cell);
      const isCapacity = cell.metric_kind === 'capacity_availability_v1';
      const display = value == null || !Number.isFinite(value) ? '未记录' : (isCapacity ? `${number(value * 100, 1)}%` : formatDecimal(value, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
      const coverage = isCapacity ? ` · 有效窗 ${cell.effective_windows ?? '未记录'} · 不可用窗 ${cell.unavailable_windows ?? '未记录'}` : '';
      const title = isCapacity
        ? `${assessmentAirportName(airportId)} · ${assessmentScenarioLabel(scenario)} · 运行期容量占用率 ${display}${coverage} · 来源 ${cell.source_run_id || '未记录'}`
        : `${assessmentAirportName(airportId)} · ${assessmentScenarioLabel(scenario)} · 支撑度 ${display} · 衰减率 ${number(cell.decline_rate * 100, 1)}%`;
      return `<td class="assessment-heat-cell" style="background:${assessmentHeatColor(value ?? 0)}" title="${esc(title)}">${esc(display)}</td>`;
    }).join('');
    return `<tr><th title="${esc(airportId)}">${esc(assessmentAirportName(airportId))}</th>${cells}</tr>`;
  }).join('');
  refs.assessmentHeatmap.innerHTML = `<table class="assessment-heat-table"><thead><tr><th>机场</th>${header}</tr></thead><tbody>${rows}</tbody></table>`;
}
function renderAssessmentCurves() {
  const airportIds = state.assessment.overview?.airport_ids || [];
  refs.assessmentAirportSelect.innerHTML = airportIds.map((id) => `<option value="${esc(id)}" ${id === state.assessment.airportId ? 'selected' : ''}>${esc(assessmentAirportName(id))}</option>`).join('');
  const series = state.assessment.curves?.series || [];
  if (!series.length) {
    refs.assessmentCurves.innerHTML = '<div class="results-placeholder">当前机场暂无评估曲线</div>';
    refs.assessmentCurveLegend.innerHTML = ''; return;
  }
  const allSteps = series.flatMap((row) => row.steps || []).map(Number).filter(Number.isFinite);
  const minStep = Math.min(...allSteps); const maxStep = Math.max(...allSteps); const stepSpan = Math.max(1, maxStep - minStep);
  const width = 760; const height = 230; const pad = { left: 42, right: 18, top: 16, bottom: 28 };
  const x = (value) => pad.left + (Number(value) - minStep) / stepSpan * (width - pad.left - pad.right);
  const y = (value) => height - pad.bottom - Math.max(0, Math.min(1, Number(value))) * (height - pad.top - pad.bottom);
  const grid = [0, .5, 1].map((value) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(value)}" y2="${y(value)}" stroke="#29495d"/><text x="${pad.left - 7}" y="${y(value) + 3}" text-anchor="end" fill="#7894a6" font-size="10">${value.toFixed(1)}</text>`).join('');
  const lines = series.map((row, index) => {
    const values = row.values || row.support || [];
    const points = row.steps.map((step, pointIndex) => `${x(step)},${y(values[pointIndex])}`).join(' ');
    return `<polyline points="${points}" fill="none" stroke="${colors[index % colors.length]}" stroke-width="2.4" vector-effect="non-scaling-stroke"/>`;
  }).join('');
  const metricLabel = series[0]?.metric_label || (series[0]?.metric_kind === 'capacity_availability_v1' ? '起降容量可用比例' : '评估指标');
  refs.assessmentCurves.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(metricLabel)}逐窗曲线">${grid}<path d="M${pad.left} ${height - pad.bottom}H${width - pad.right}M${pad.left} ${pad.top}V${height - pad.bottom}" stroke="#48687b"/>${lines}</svg>`;
  refs.assessmentCurveLegend.innerHTML = series.map((row, index) => `<span><i style="background:${colors[index % colors.length]}"></i>${esc(assessmentScenarioLabel(row.scenario_type))}</span>`).join('');
}
function renderAssessmentRisk() {
  const risks = state.assessment.overview?.risks || [];
  if (!risks.length) {
    const reason = state.assessment.overview?.risk_status?.reason || '未配置独立风险评估模型或正式专家评分';
    refs.assessmentRiskTable.innerHTML = `<div class="results-placeholder">${esc(reason)}</div>`; return;
  }
  const rows = risks.map((risk) => `<tr><td title="${esc(risk.airport_id)}">${esc(assessmentAirportName(risk.airport_id))}</td><td><span class="assessment-risk-badge" data-risk-level="${esc(risk.risk_level)}">${esc(ASSESSMENT_RISK_LABELS[risk.risk_level] || risk.risk_level)}</span></td><td>${esc(number(Number(risk.risk_score) * 100, 1))}%</td><td>${esc(risk.updated_at || '—')}</td></tr>`).join('');
  refs.assessmentRiskTable.innerHTML = `<table><thead><tr><th>机场</th><th>风险等级</th><th>评分</th><th>更新时间</th></tr></thead><tbody>${rows}</tbody></table>`;
}
function renderAssessmentComparisons() {
  const items = state.assessment.comparisons?.items || [];
  if (!items.length) { refs.assessmentComparisons.innerHTML = '<div class="results-placeholder">暂无跨场景评估对比记录</div>'; return; }
  const rows = items.map((item) => `<tr><td>${esc(assessmentComparisonLabel(item.base_airport_id, item.base_scenario_type))}</td><td>${esc(assessmentComparisonLabel(item.compared_airport_id, item.compared_scenario_type))}</td><td title="${esc(item.log || '')}">${esc(item.conclusion || '—')}</td><td>${esc(item.created_at || '—')}</td></tr>`).join('');
  refs.assessmentComparisons.innerHTML = `<table><thead><tr><th>基准</th><th>对照</th><th>记录结论</th><th>时间</th></tr></thead><tbody>${rows}</tbody></table>`;
}
function renderAssessmentWorkspace() {
  const active = state.workspace === 'assessment';
  refs.page.classList.toggle('assessment-active', active);
  refs.assessmentWorkbench.classList.toggle('hidden', !active);
  for (const node of [refs.comparisonCondition, refs.modeSpecific, refs.primary, refs.analysis]) node.classList.toggle('hidden', active);
  refs.change.classList.toggle('hidden', active); refs.exportWrap.classList.toggle('hidden', active);
  if (!active) return;
  refs.assessmentError.classList.toggle('hidden', state.assessment.status !== 'error');
  refs.assessmentError.textContent = state.assessment.status === 'error' ? '评估数据读取失败，请稍后重试。' : '';
  renderAssessmentHeatmap(); renderAssessmentCurves(); renderAssessmentRisk(); renderAssessmentComparisons();
}
async function loadAssessmentWorkspace({ force = false } = {}) {
  if (state.assessment.status === 'loading' && !force) return;
  state.assessment.status = 'loading'; state.assessment.error = null; renderAssessmentWorkspace();
  try {
    const [overview, comparisons] = await Promise.all([
      apiFetch('/api/results/assessment/overview'),
      apiFetch('/api/results/assessment/comparisons?limit=50'),
    ]);
    state.assessment.overview = overview; state.assessment.comparisons = comparisons;
    if (!state.assessment.airportId || !overview.airport_ids.includes(state.assessment.airportId)) state.assessment.airportId = overview.airport_ids[0] || null;
    state.assessment.curves = state.assessment.airportId ? await apiFetch(`/api/results/assessment/curves?airport_id=${encodeURIComponent(state.assessment.airportId)}`) : null;
    state.assessment.status = 'ready';
  } catch (error) { state.assessment.status = 'error'; state.assessment.error = error; }
  renderAssessmentWorkspace();
}
function legendLabel(runId) {
  const payload = view().payload || {};
  const role = Object.entries(payload.roles || {}).find((entry) => entry[1] === runId)?.[0];
  if (state.workspace === 'damage') return ({ R0: 'R0 基准', R1: 'R1 损毁', R2: 'R2 组群' })[role] || role || shortRunId(runId);
  const ids = payload.run_ids || [];
  const index = Math.max(0, ids.indexOf(runId));
  if (state.workspace === 'multi') return `D${index}`;
  if (state.workspace === 'configuration') {
    if (runId === payload.baseline_run_id) return '基准';
    const alternativeIndex = ids.filter((id) => id !== payload.baseline_run_id).indexOf(runId);
    return `方案${alternativeIndex + 2}`;
  }
  return shortRunId(runId);
}
function runMatches(run, query) {
  if (!query) return true;
  const text = JSON.stringify(run || {}).toLowerCase(); return text.includes(query.toLowerCase());
}

function buildSessionState() {
  return {
    activeWorkspace: state.workspace,
    workspaces: Object.fromEntries(MODES.map((mode) => {
      const item = state.workspaceStates[mode];
      return [mode, {
        selection: item.selection, activeDamageStage: item.activeDamageStage,
        seriesKind: item.seriesKind, observation: item.observation,
        analysisTheme: item.analysisTheme, windowStart: item.windowStart,
      }];
    })),
  };
}
function storageKey() { return 'results.workspace.v2'; }
function persistSessionState() { try { sessionStorage.setItem(storageKey(), JSON.stringify(buildSessionState())); } catch (_) { /* optional */ } }
function restoreSessionState() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey()) || 'null');
    if (!saved || typeof saved !== 'object') return;
    if (MODES.includes(saved.activeWorkspace)) state.workspace = saved.activeWorkspace;
    for (const mode of MODES) {
      const source = saved.workspaces?.[mode]; const target = state.workspaceStates[mode];
      if (!source || !target) continue;
      target.selection = source.selection || null;
      if (['damage', 'cluster'].includes(source.activeDamageStage)) target.activeDamageStage = source.activeDamageStage;
      if (['departures', 'returns'].includes(source.seriesKind)) target.seriesKind = source.seriesKind;
      if (['trend', 'delta', 'cumulative'].includes(source.observation)) target.observation = source.observation;
      if (['tasks', 'airports', 'assurance'].includes(source.analysisTheme)) target.analysisTheme = source.analysisTheme;
      target.windowStart = Number.isInteger(source.windowStart) ? source.windowStart : 0;
    }
  } catch (_) { /* stale state is ignored */ }
}

async function loadComparable(baseRunId, mode) {
  const query = new URLSearchParams({ base_run_id: baseRunId, mode });
  const response = await apiFetch(`/api/results/comparable-runs?${query.toString()}`);
  return response.items || [];
}
async function discoverCandidates(mode) {
  const apiMode = mode === 'multi' ? 'multi_scenario' : 'configuration';
  for (const run of state.runs) {
    const items = await loadComparable(run.run_id, apiMode);
    if (items.length) return { baseRunId: run.run_id, items };
  }
  return null;
}
async function requestComparison(mode, selection) {
  if (mode === 'damage') return apiFetch('/api/results/damage-comparison', { method: 'POST', body: selection });
  const body = {
    run_ids: selection.run_ids,
    baseline_run_id: selection.baseline_run_id,
    comparison_run_id: selection.comparison_run_id,
  };
  return apiFetch(mode === 'multi' ? '/api/results/scenario-comparison' : '/api/results/config-comparison', { method: 'POST', body });
}
async function restoreComparison(mode) {
  const item = state.workspaceStates[mode];
  if (!item.selection) return false;
  try { item.payload = await requestComparison(mode, item.selection); return true; }
  catch (_) { item.payload = null; item.selection = null; return false; }
}
async function applyDefault(mode) {
  const item = state.workspaceStates[mode];
  if (item.payload || item.selection) return;
  if (mode === 'damage') {
    if (!state.damageCandidates.length) return;
    const row = state.damageCandidates[0];
    item.selection = { r0_run_id: row.r0_run_id, r1_run_id: row.r1_run_id, r2_run_id: row.r2_run_id };
  } else {
    const candidate = await discoverCandidates(mode); if (!candidate) return;
    const max = mode === 'multi' ? 6 : 5;
    const runIds = [candidate.baseRunId, ...candidate.items.map((row) => row.run_id)];
    const unique = [...new Set(runIds)]; while (unique.length > max) unique.pop();
    item.selection = {
      run_ids: unique, baseline_run_id: candidate.baseRunId,
      comparison_run_id: unique.find((id) => id !== candidate.baseRunId),
    };
  }
  item.payload = await requestComparison(mode, item.selection);
}
async function loadInitial() {
  refs.page.setAttribute('aria-busy', 'true');
  try {
    const result = await apiFetch('/api/runs?status=succeeded&limit=500');
    state.runs = result.items || []; state.runById = new Map(state.runs.map((run) => [run.run_id, run]));
    state.damageCandidates = (await apiFetch('/api/results/damage-candidates')).items || [];
    await Promise.all(MODES.map((mode) => restoreComparison(mode)));
    await applyDefault(state.workspace);
    state.status = 'ready'; state.error = null;
  } catch (error) { console.error(error); state.status = 'error'; state.error = error; }
  refs.page.setAttribute('aria-busy', 'false'); render();
}

function currentFocus() {
  const payload = view().payload; if (!payload) return null;
  if (state.workspace === 'damage') return payload.comparisons?.[view().activeDamageStage] || payload.focus_comparison;
  return payload.focus_comparison;
}
function renderCondition() {
  refs.conditionTitle.textContent = modeLabels[state.workspace];
  const payload = view().payload;
  refs.runLink.classList.toggle('hidden', state.runs.length > 0);
  if (!payload) {
    refs.conditionFacts.textContent = state.status === 'error' ? '结果读取失败，请重试。' : state.runs.length ? '请选择符合当前模式的Run。' : '当前没有成功Run。';
    refs.focusControls.classList.add('hidden'); return;
  }
  const ids = payload.run_ids || Object.values(payload.roles || {});
  refs.conditionFacts.innerHTML = ids.map((id) => {
    const identity = runIdentity(id);
    const chips = identity.chips.map((chip, index) => `<span class="identity-chip${index === 0 ? ' primary' : ''}">${esc(chip)}</span>`).join('');
    return `<a class="run-identity" href="/runs/${encodeURIComponent(id)}" title="${esc(id)}">${chips}<small>${esc(identity.secondary)}</small></a>`;
  }).join('<i class="identity-separator" aria-hidden="true"></i>');
  refs.focusControls.classList.toggle('hidden', state.workspace === 'damage');
  if (state.workspace !== 'damage') {
    const options = ids.map((id) => `<option value="${esc(id)}" title="${esc(id)}">${esc(selectionLabel(id))}</option>`).join('');
    refs.focusBaseline.innerHTML = options; refs.focusComparison.innerHTML = options;
    refs.focusBaseline.value = payload.baseline_run_id; refs.focusComparison.value = payload.comparison_run_id;
    refs.focusBaseline.title = `${selectionLabel(payload.baseline_run_id)} · ${payload.baseline_run_id}`;
    refs.focusComparison.title = `${selectionLabel(payload.comparison_run_id)} · ${payload.comparison_run_id}`;
  }
}
function stageSummary(stage) {
  const focus = view().payload?.comparisons?.[stage];
  if (!focus) return '<span class="stage-metrics">等待数据</span>';
  const facts = compactStageFacts(focus);
  return `<span class="stage-metrics">${facts.map((item) => `<i class="${item.state}" title="${esc(item.title)}">${esc(item.text)}</i>`).join('')}</span>`;
}
function renderDamageStageSummary() {
  const payload = view().payload || {};
  refs.damageStages.querySelector('[data-damage-stage="damage"] .stage-facts').innerHTML = stageSummary('damage');
  refs.damageStages.querySelector('[data-damage-stage="cluster"] .stage-facts').innerHTML = stageSummary('cluster');
  refs.damageStages.querySelectorAll('[data-damage-stage]').forEach((button) => button.classList.toggle('active', button.dataset.damageStage === view().activeDamageStage));
  return payload.comparisons;
}
function renderMultiOverview() {
  const payload = view().payload || {}; const solver = payload.solver_comparison?.by_run || {};
  const rows = (payload.run_ids || []).map((id) => {
    const summary = payload.run_summaries?.[id] || {}; const technical = solver[id] || {};
    return `<article title="${esc(id)}"><strong>${esc(selectionLabel(id))}</strong><span><i>履行 <b>${displayValue(summary.fulfilled_sorties_total)}</b></i><i>缺口 <b>${displayValue(summary.unmet_sorties_total)}</b></i><i>出动 <b>${displayValue(summary.scheduled_sorties_total)}</b></i><i>J <b>${displayValue(technical.objective, { formatter: (v) => number(v, 4) })}</b></i></span></article>`;
  }).join('');
  refs.multiOverview.innerHTML = `<div class="multi-overview-grid">${rows}</div>`;
}
function configText(value) {
  if (Array.isArray(value)) return value.length ? value.join('、') : '无';
  if (typeof value === 'boolean') return value ? '是' : '否';
  return value === null || value === undefined ? '不适用' : String(value);
}
function renderConfigurationDifferences() {
  const differences = view().payload?.key_configuration_differences || {};
  const rows = Object.entries(differences).map(([field, values]) => `<div><span>${esc(configLabels[field] || field)}</span><b>${esc(configText(values.baseline))}</b><i>→</i><b>${esc(configText(values.current))}</b></div>`).join('');
  refs.configDifferences.innerHTML = rows || '<p>当前重点对照未记录主要配置差异。</p>';
}
function renderModeSpecific() {
  const has = Boolean(view().payload); refs.modeSpecific.classList.toggle('hidden', !has);
  refs.damageStages.classList.toggle('hidden', state.workspace !== 'damage');
  refs.multiOverview.classList.toggle('hidden', state.workspace !== 'multi');
  refs.configDifferences.classList.toggle('hidden', state.workspace !== 'configuration');
  if (!has) return;
  if (state.workspace === 'damage') renderDamageStageSummary();
  if (state.workspace === 'multi') renderMultiOverview();
  if (state.workspace === 'configuration') renderConfigurationDifferences();
}

function visibleWindowCount(total) {
  const width = refs.chart.clientWidth || 900;
  return Math.max(12, Math.min(total, Math.floor(width / 34)));
}
function timelineSeries() {
  const payload = view().payload; const focus = currentFocus(); if (!payload || !focus) return [];
  const kind = view().seriesKind; const observation = view().observation;
  if (observation === 'delta') return [{ id: 'delta', label: `${legendLabel(focus.baseline_run_id)} → ${legendLabel(focus.comparison_run_id)}`, values: focus.timeline[kind].delta, color: '#ef9a3c' }];
  if (observation === 'cumulative') return [
    { id: focus.baseline_run_id, label: legendLabel(focus.baseline_run_id), values: focus.timeline[kind].cumulative.baseline, color: colors[0] },
    { id: focus.comparison_run_id, label: legendLabel(focus.comparison_run_id), values: focus.timeline[kind].cumulative.current, color: colors[1] },
  ];
  const ids = payload.run_ids || Object.values(payload.roles || {});
  return ids.map((id, index) => {
    let values;
    if (state.workspace === 'damage') { const role = Object.entries(payload.roles).find((entry) => entry[1] === id)?.[0]; values = payload.timeline?.[kind]?.[role]; }
    else if (state.workspace === 'configuration') values = payload.timeline?.[kind]?.[id]?.values;
    else values = payload.timeline?.[kind]?.[id];
    return { id, label: legendLabel(id), values, color: colors[index % colors.length], muted: ![focus.baseline_run_id, focus.comparison_run_id].includes(id) };
  });
}
function renderChart() {
  const payload = view().payload; const focus = currentFocus();
  if (!payload || !focus) { refs.chart.innerHTML = '<div class="results-placeholder">请选择可比较Run</div>'; refs.legend.innerHTML = ''; refs.timeNav.classList.add('hidden'); return; }
  const windows = focus.timeline.windows || []; const series = timelineSeries();
  const visible = visibleWindowCount(windows.length); const maxStart = Math.max(0, windows.length - visible);
  view().windowStart = Math.min(maxStart, Math.max(0, view().windowStart));
  const start = view().windowStart; const end = Math.min(windows.length, start + visible);
  const shownWindows = windows.slice(start, end); const shownSeries = series.map((row) => ({ ...row, values: (row.values || []).slice(start, end) }));
  const allValues = shownSeries.flatMap((row) => row.values).filter((value) => typeof value === 'number' && Number.isFinite(value));
  if (!shownWindows.length || shownSeries.some((row) => row.values.length !== shownWindows.length) || !allValues.length) { refs.chart.innerHTML = '<div class="results-placeholder">时序未记录或时间轴不一致</div>'; return; }
  const width = Math.max(640, refs.chart.clientWidth || 800); const height = 270; const pad = { left: 42, right: 16, top: 18, bottom: 30 };
  const min = Math.min(0, ...allValues); const max = Math.max(0, ...allValues); const span = max - min || 1;
  const x = (index) => pad.left + index * ((width - pad.left - pad.right) / Math.max(1, shownWindows.length - 1));
  const y = (value) => pad.top + (max - value) / span * (height - pad.top - pad.bottom);
  const grid = Array.from({ length: 5 }, (_, index) => { const value = max - span * index / 4; return `<line x1="${pad.left}" y1="${y(value)}" x2="${width - pad.right}" y2="${y(value)}"/><text x="${pad.left - 7}" y="${y(value) + 3}">${number(value, 0)}</text>`; }).join('');
  const lines = shownSeries.map((row) => `<polyline points="${row.values.map((value, index) => `${x(index)},${y(value)}`).join(' ')}" style="stroke:${row.color};opacity:${row.muted ? .35 : 1}"/>`).join('');
  const tickStep = Math.max(1, Math.ceil(shownWindows.length / 7));
  const ticks = shownWindows.map((window, index) => index % tickStep === 0 || index === shownWindows.length - 1 ? `<text class="x-label" x="${x(index)}" y="${height - 8}">${esc(window)}</text>` : '').join('');
  refs.chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(view().seriesKind === 'departures' ? '出动' : '返航')}时序比较"><g class="chart-grid">${grid}</g><line class="zero-line" x1="${pad.left}" y1="${y(0)}" x2="${width - pad.right}" y2="${y(0)}"/>${lines}${ticks}</svg>`;
  refs.legend.innerHTML = shownSeries.map((row) => `<span><i style="background:${row.color};opacity:${row.muted ? .35 : 1}"></i>${esc(row.label)}</span>`).join('');
  refs.timeNav.classList.toggle('hidden', windows.length <= visible); refs.timeStart.max = String(maxStart); refs.timeStart.value = String(start);
  refs.timeRange.textContent = `${shownWindows[0]}–${shownWindows[shownWindows.length - 1]}窗 / 共${windows.length}窗`;
  refs.chartTitle.textContent = `${view().seriesKind === 'departures' ? '出动' : '返航'}${view().observation === 'trend' ? '趋势' : view().observation === 'delta' ? '逐窗差异' : '累计变化'}`;
  const subtitle = view().observation === 'trend' ? '15分钟原始粒度' : view().observation === 'delta' ? '对照 − 参照' : '完整范围累计';
  refs.chartSubtitle.textContent = subtitle;
  refs.chartSubtitle.title = view().observation === 'cumulative' ? '累计值按完整正式时间范围计算，不受当前可视窗口影响' : '';
}

function renderValueComparison() {
  const payload = view().payload; const focus = currentFocus(); if (!payload || !focus) { refs.valueComparison.innerHTML = '<div class="results-placeholder">等待比较结果</div>'; return; }
  refs.focusRelation.textContent = `${selectionLabel(focus.baseline_run_id)} → ${selectionLabel(focus.comparison_run_id)}`;
  const rows = [
    ['任务需求', 'required_sorties_total', 0], ['实际履行', 'fulfilled_sorties_total', 0],
    ['任务缺口', 'unmet_sorties_total', 0], ['额外调度', 'additional_sorties_total', 0],
  ].map(([label, key, digits]) => { const row = focus.values[key] || {}; return `<tr><th>${label}</th><td>${displayValue(row.baseline)}</td><td>${displayValue(row.current)}</td><td>${displayValue(row.delta, { formatter: (v) => signed(v, digits) })}</td></tr>`; });
  for (const name of ['J', 'F1', 'F2', 'F3']) {
    const row = focus.objective_components?.[name] || {}; const comparable = row.definition_comparable !== false;
    rows.push(`<tr><th>${name}</th><td>${displayValue(row.baseline, { formatter: (v) => number(v, 4) })}</td><td>${displayValue(row.current, { formatter: (v) => number(v, 4) })}</td><td>${displayValue(row.delta, { comparable, formatter: (v) => signed(v, 4) })}</td></tr>`);
  }
  const statuses = payload.solver_comparison?.by_run || {}; const solver = solverStatusSummary(statuses[focus.comparison_run_id] || {});
  refs.valueComparison.innerHTML = `<table><thead><tr><th>指标</th><th>参照</th><th>对照</th><th>差值</th></tr></thead><tbody>${rows.join('')}</tbody></table><p title="${esc(solver.detail)}">对照状态：${esc(solver.text)}</p>`;
}

function focusRole(runId) {
  const payload = view().payload;
  return Object.entries(payload?.roles || {}).find((entry) => entry[1] === runId)?.[0] || runId;
}
function taskRunValue(row, runId, field) {
  if (!row) return null; const role = focusRole(runId);
  if (row[field]?.[role] !== undefined) return row[field][role];
  return row[runId]?.[field] ?? null;
}
function differenceBar(delta, maximum) {
  if (delta === null || delta === undefined || !Number.isFinite(Number(delta))) return '<span class="difference-missing">未记录</span>';
  const value = Number(delta); const width = maximum > 0 ? Math.min(50, Math.abs(value) / maximum * 50) : 0;
  const className = value > 0 ? 'positive' : value < 0 ? 'negative' : 'zero';
  return `<span class="difference-bar ${className}" title="变化 ${esc(signed(value, 0))}"><span class="difference-track"><i style="width:${width}%"></i></span><b>${esc(signed(value, 0))}</b></span>`;
}
function renderTaskTheme(focus) {
  const tasks = view().payload?.tasks || {};
  const changes = focus.structural_changes?.tasks || [];
  const changeById = new Map(changes.map((row) => [row.object_id, row]));
  const ids = [...new Set([...changes.map((row) => row.object_id), ...Object.keys(tasks)])];
  const maximum = Math.max(0, ...changes.map((row) => Math.abs(Number(row.delta))).filter(Number.isFinite));
  const rows = ids.map((id) => {
    const row = tasks[id];
    const base = taskRunValue(row, focus.baseline_run_id, 'scheduled_total'); const current = taskRunValue(row, focus.comparison_run_id, 'scheduled_total');
    const required = taskRunValue(row, focus.comparison_run_id, 'required_total');
    const delta = changeById.get(id)?.delta ?? (current !== null && base !== null ? Number(current) - Number(base) : null);
    return `<tr><th>${esc(id)}</th><td>${displayValue(required)}</td><td>${displayValue(base)}</td><td>${displayValue(current)}</td><td>${differenceBar(delta, maximum)}</td></tr>`;
  }).join('');
  return `<table class="task-contribution-table"><thead><tr><th>任务</th><th>需求</th><th>参照调度</th><th>对照调度</th><th>差异贡献</th></tr></thead><tbody>${rows}</tbody></table>`;
}
function airportRunValue(row, runId) {
  if (!row) return null; const role = focusRole(runId);
  if (row.departures_total?.[role] !== undefined) return row.departures_total[role];
  return row[runId]?.departures_total ?? null;
}
function renderAirportTheme(focus) {
  const airports = view().payload?.airports || {};
  const rows = Object.entries(airports).map(([id, row]) => { const base = airportRunValue(row, focus.baseline_run_id); const current = airportRunValue(row, focus.comparison_run_id); return `<tr><th>${esc(id)}</th><td>${displayValue(base)}</td><td>${displayValue(current)}</td><td>${displayValue(base !== null && current !== null ? Number(current) - Number(base) : null, { formatter: (v) => signed(v, 0) })}</td></tr>`; }).join('');
  const scheme = view().payload?.scheme || {}; const baseScheme = scheme[focusRole(focus.baseline_run_id)] || scheme[focus.baseline_run_id] || {}; const currentScheme = scheme[focusRole(focus.comparison_run_id)] || scheme[focus.comparison_run_id] || {};
  return `<div class="theme-summary"><span>参照参与机场 <b>${displayValue(baseScheme.participating_airports?.length)}</b></span><span>对照参与机场 <b>${displayValue(currentScheme.participating_airports?.length)}</b></span><span>跨机场返航占比 <b>${displayValue(currentScheme.cross_return_ratio, { formatter: (v) => number(v * 100, 1) + '%' })}</b></span></div><table><thead><tr><th>机场</th><th>参照出动</th><th>对照出动</th><th>变化</th></tr></thead><tbody>${rows}</tbody></table>`;
}
function renderAssuranceTheme(focus) {
  const base = focus.assurance?.baseline || {}; const current = focus.assurance?.current || {};
  const capacity = (current.capacity_by_airport || []).map((row) => `<tr><th>${esc(row.airport_id)}</th><td>${displayValue(row.period_utilization, { formatter: (v) => number(v * 100, 1) + '%' })}</td><td>${displayValue(row.valid_window_count)}</td><td>${displayValue(row.unavailable_window_count)}</td></tr>`).join('');
  const resources = (current.resources || []).map((row) => `<tr><th>${esc(row.airport_id)} / ${esc(row.resource_type_id)} (${esc(row.unit || '单位未记录')})</th><td>${displayValue(row.consumed_total, { formatter: (v) => number(v, 2) })}</td><td>${displayValue(row.replenishment_actual_total, { formatter: (v) => number(v, 2) })}</td><td>${displayValue(row.ending_remaining, { formatter: (v) => number(v, 2) })}</td></tr>`).join('');
  return `<div class="theme-summary"><span>峰值占用 <b>${displayValue(current.aircraft?.peak_in_use)}</b></span><span>期内再次可用 <b>${displayValue(current.aircraft?.ready_releases_total)}</b></span><span>期末可用 <b>${displayValue(current.aircraft?.ending_available)}</b></span></div><div class="assurance-tables"><table><thead><tr><th>机场</th><th>运行期容量占用</th><th>有效窗</th><th>不可用窗</th></tr></thead><tbody>${capacity}</tbody></table><table><thead><tr><th>同一机场/资源</th><th>累计消耗</th><th>实际补给</th><th>期末剩余</th></tr></thead><tbody>${resources}</tbody></table></div><span class="sr-only">${esc(JSON.stringify(base.aircraft || {}))}</span>`;
}
function renderTheme() {
  const focus = currentFocus(); if (!focus) { refs.themeContent.innerHTML = '<div class="results-placeholder">等待比较结果</div>'; return; }
  refs.themeContent.innerHTML = view().analysisTheme === 'tasks' ? renderTaskTheme(focus) : view().analysisTheme === 'airports' ? renderAirportTheme(focus) : renderAssuranceTheme(focus);
  document.querySelectorAll('[data-analysis-theme]').forEach((button) => button.classList.toggle('active', button.dataset.analysisTheme === view().analysisTheme));
}
function renderConclusions() {
  const focus = currentFocus(); const conclusions = (state.workspace === 'damage' ? focus?.conclusions : view().payload?.conclusions) || [];
  refs.conclusions.innerHTML = conclusions.slice(0, 2).map((text, index) => `<p><b>${index + 1}</b>${esc(text)}</p>`).join('') || '<p>当前正式数据不足以形成结论。</p>';
}
function render() {
  document.querySelectorAll('[data-workspace]').forEach((button) => button.classList.toggle('active', button.dataset.workspace === state.workspace));
  renderAssessmentWorkspace();
  if (state.workspace === 'assessment') return;
  document.querySelectorAll('[data-series-kind]').forEach((button) => button.classList.toggle('active', button.dataset.seriesKind === view().seriesKind));
  document.querySelectorAll('[data-observation]').forEach((button) => button.classList.toggle('active', button.dataset.observation === view().observation));
  renderCondition(); renderModeSpecific(); renderChart(); renderValueComparison(); renderTheme(); renderConclusions();
  refs.exportButton.disabled = !state.canExport || !view().payload;
}

function overlayModeName() { return state.workspace === 'damage' ? 'R0/R1/R2组合' : state.workspace === 'multi' ? '损毁场景Run（2–6个）' : '配置方案Run（2–5个）'; }
async function openOverlay() {
  const item = view(); item.draft = createDraft(); item.draft.search = '';
  if (state.workspace === 'damage') {
    const selection = item.selection;
    item.draft.damageIndex = state.damageCandidates.findIndex((row) => row.r0_run_id === selection?.r0_run_id && row.r1_run_id === selection?.r1_run_id && row.r2_run_id === selection?.r2_run_id);
  } else {
    item.draft.baseRunId = item.selection?.baseline_run_id || null;
    item.draft.selectedRunIds = new Set(item.selection?.run_ids || []);
    if (item.draft.baseRunId) item.draft.comparableRuns = await loadComparable(item.draft.baseRunId, state.workspace === 'multi' ? 'multi_scenario' : 'configuration');
  }
  refs.overlay.classList.add('open'); refs.overlay.setAttribute('aria-hidden', 'false'); refs.search.value = ''; renderOverlay();
}
function closeOverlay() { refs.overlay.classList.remove('open'); refs.overlay.setAttribute('aria-hidden', 'true'); }
function renderOverlay() {
  const item = view(); const query = item.draft.search;
  if (state.workspace === 'damage') {
    const rows = state.damageCandidates.map((row, index) => ({ row, index })).filter(({ row }) => [row.r0_run_id, row.r1_run_id, row.r2_run_id].some((id) => runMatches(state.runById.get(id) || { run_id: id }, query)));
    refs.overlayBody.innerHTML = `<p class="overlay-title">${overlayModeName()}</p><div class="candidate-list">${rows.map(({ row, index }) => `<label class="candidate-row"><input type="radio" name="damageTriple" value="${index}" ${item.draft.damageIndex === index ? 'checked' : ''}><span><b>R0 ${esc(shortRunId(row.r0_run_id))} → R1 ${esc(shortRunId(row.r1_run_id))} → R2 ${esc(shortRunId(row.r2_run_id))}</b><small>损毁影响与组群调整均保留</small></span></label>`).join('') || '<p>没有符合条件的组合。</p>'}</div>`;
    refs.overlayBody.querySelectorAll('[name="damageTriple"]').forEach((radio) => radio.addEventListener('change', () => { item.draft.damageIndex = Number(radio.value); renderOverlay(); }));
    refs.apply.disabled = item.draft.damageIndex < 0 || item.draft.damageIndex === null; return;
  }
  const baseOptions = state.runs.filter((run) => runMatches(run, query)).map((run) => `<option value="${esc(run.run_id)}" title="${esc(run.run_id)}" ${item.draft.baseRunId === run.run_id ? 'selected' : ''}>${esc(selectionLabel(run.run_id))}</option>`).join('');
  const candidates = [state.runById.get(item.draft.baseRunId), ...item.draft.comparableRuns].filter(Boolean).filter((run, index, array) => array.findIndex((other) => other.run_id === run.run_id) === index).filter((run) => runMatches(run, query));
  refs.overlayBody.innerHTML = `<label class="overlay-title">参照Run<select id="comparisonBaseRun" class="control"><option value="">请选择</option>${baseOptions}</select></label><p class="overlay-title">${overlayModeName()}</p><div class="candidate-list">${candidates.map((run) => `<label class="candidate-row" title="${esc(run.run_id)}"><input type="checkbox" data-run-id="${esc(run.run_id)}" ${item.draft.selectedRunIds.has(run.run_id) ? 'checked' : ''} ${run.run_id === item.draft.baseRunId ? 'disabled' : ''}><span><b>${esc(selectionLabel(run.run_id))}</b><small>${run.run_id === item.draft.baseRunId ? '参照Run' : `严格可比 · ${esc(shortRunId(run.run_id))}`}</small></span></label>`).join('') || '<p>请先选择参照Run。</p>'}</div>`;
  $('comparisonBaseRun').addEventListener('change', async (event) => { item.draft.baseRunId = event.target.value || null; item.draft.selectedRunIds = new Set(item.draft.baseRunId ? [item.draft.baseRunId] : []); item.draft.comparableRuns = item.draft.baseRunId ? await loadComparable(item.draft.baseRunId, state.workspace === 'multi' ? 'multi_scenario' : 'configuration') : []; renderOverlay(); });
  refs.overlayBody.querySelectorAll('[data-run-id]').forEach((box) => box.addEventListener('change', () => { box.checked ? item.draft.selectedRunIds.add(box.dataset.runId) : item.draft.selectedRunIds.delete(box.dataset.runId); renderOverlay(); }));
  const count = item.draft.selectedRunIds.size; refs.apply.disabled = count < 2 || count > (state.workspace === 'multi' ? 6 : 5);
}
async function applyOverlay() {
  const item = view(); refs.apply.disabled = true;
  try {
    if (state.workspace === 'damage') {
      const row = state.damageCandidates[item.draft.damageIndex];
      item.selection = { r0_run_id: row.r0_run_id, r1_run_id: row.r1_run_id, r2_run_id: row.r2_run_id };
    } else {
      const runIds = [...item.draft.selectedRunIds]; const baseline = item.draft.baseRunId;
      item.selection = { run_ids: runIds, baseline_run_id: baseline, comparison_run_id: runIds.find((id) => id !== baseline) };
    }
    item.payload = await requestComparison(state.workspace, item.selection); item.windowStart = 0; closeOverlay(); persistSessionState(); render();
  } catch (error) { showError(error); refs.apply.disabled = false; }
}
async function changeFocus() {
  if (state.workspace === 'damage' || !view().selection) return;
  const baseline = refs.focusBaseline.value; const comparison = refs.focusComparison.value;
  if (!baseline || !comparison || baseline === comparison) { showError(new Error('参照Run和对照Run必须不同。')); return; }
  view().selection = { ...view().selection, baseline_run_id: baseline, comparison_run_id: comparison };
  view().payload = await requestComparison(state.workspace, view().selection); view().windowStart = 0; persistSessionState(); render();
}

function exportRequestBody() {
  const payload = view().payload; if (!payload) return null;
  if (state.workspace === 'damage') return { kind: 'damage_comparison', r0_run_id: payload.roles.R0, r1_run_id: payload.roles.R1, r2_run_id: payload.roles.R2 };
  return {
    kind: state.workspace === 'multi' ? 'scenario_comparison' : 'configuration_comparison',
    run_ids: payload.run_ids, baseline_run_id: payload.baseline_run_id,
    comparison_run_id: payload.comparison_run_id,
  };
}
async function exportResults(format) {
  try { const file = await apiDownload('/api/results/export-file', { method: 'POST', body: { ...exportRequestBody(), format } }); saveBlob(file); }
  catch (error) { showError(error); }
}
function showError(error) { refs.error.textContent = error?.message || '操作失败'; refs.error.classList.remove('hidden'); globalThis.setTimeout(() => refs.error.classList.add('hidden'), 5000); }

document.querySelectorAll('[data-workspace]').forEach((button) => button.addEventListener('click', async () => {
  state.workspace = button.dataset.workspace;
  if (state.workspace === 'assessment') { render(); await loadAssessmentWorkspace(); return; }
  await applyDefault(state.workspace); persistSessionState(); render();
}));
document.querySelectorAll('[data-damage-stage]').forEach((button) => button.addEventListener('click', () => { view().activeDamageStage = button.dataset.damageStage; view().windowStart = 0; persistSessionState(); render(); }));
document.querySelectorAll('[data-series-kind]').forEach((button) => button.addEventListener('click', () => { view().seriesKind = button.dataset.seriesKind; persistSessionState(); render(); }));
document.querySelectorAll('[data-observation]').forEach((button) => button.addEventListener('click', () => { view().observation = button.dataset.observation; persistSessionState(); render(); }));
document.querySelectorAll('[data-analysis-theme]').forEach((button) => button.addEventListener('click', () => { view().analysisTheme = button.dataset.analysisTheme; persistSessionState(); render(); }));
refs.timeStart.addEventListener('input', () => { view().windowStart = Number(refs.timeStart.value); renderChart(); persistSessionState(); });
refs.focusBaseline.addEventListener('change', () => changeFocus().catch(showError)); refs.focusComparison.addEventListener('change', () => changeFocus().catch(showError));
refs.change.addEventListener('click', () => openOverlay().catch(showError)); refs.search.addEventListener('input', () => { view().draft.search = refs.search.value; renderOverlay(); });
$('closeResultsOverlay').addEventListener('click', closeOverlay); $('cancelResultsOverlay').addEventListener('click', closeOverlay); refs.apply.addEventListener('click', () => applyOverlay());
refs.exportButton.addEventListener('click', () => { const open = !refs.exportMenu.classList.contains('open'); refs.exportMenu.classList.toggle('open', open); refs.exportMenu.setAttribute('aria-hidden', String(!open)); });
refs.exportMenu.addEventListener('click', (event) => { const button = event.target.closest('[data-export-format]'); if (button) { refs.exportMenu.classList.remove('open'); exportResults(button.dataset.exportFormat); } });
refs.assessmentRefresh.addEventListener('click', () => loadAssessmentWorkspace({ force: true }));
refs.assessmentAirportSelect.addEventListener('change', async () => {
  state.assessment.airportId = refs.assessmentAirportSelect.value || null;
  try {
    state.assessment.curves = state.assessment.airportId ? await apiFetch(`/api/results/assessment/curves?airport_id=${encodeURIComponent(state.assessment.airportId)}`) : null;
    state.assessment.status = 'ready';
  } catch (error) { state.assessment.status = 'error'; state.assessment.error = error; }
  renderAssessmentWorkspace();
});
globalThis.addEventListener('resize', () => {
  if (state.workspace !== 'assessment' && view()?.payload) renderChart();
});

restoreSessionState();
getAccount().then((account) => { state.canExport = Boolean(account.permissions?.includes('results.export')); render(); }).catch(() => {});
loadInitial();
