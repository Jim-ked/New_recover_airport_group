import { apiFetch, apiDownload, saveBlob } from './api-client.js';
import { getAccount } from './account-context.js';
import { formatDecimal } from './number-display.js';
import { formatRunIdentity, shortRunId, repeatedRunIds, runIdentityMarkup } from '../utils/run-identity.js';

const $ = (id) => document.getElementById(id);
const MODES = ['damage', 'multi', 'configuration'];
function isComparisonWorkspace(workspace) { return MODES.includes(workspace); }
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
    comparisonLoading: false, comparisonGeneration: 0,
    seriesKind: 'departures', observation: 'trend', analysisTheme: 'tasks', windowStart: 0,
  };
}
const state = {
  workspace: 'damage', runs: [], runById: new Map(), damageCandidates: [], canExport: false,
  canSaveComparison: false, pendingSaves: new Set(), saveGeneration: 0,
  status: 'loading', error: null,
  workspaceStates: {
    damage: createWorkspaceState(),
    multi: createWorkspaceState(),
    configuration: createWorkspaceState(),
  },
  assessment: { status: 'idle', overview: null, curves: null, comparisons: null, evaluations: null, airportId: null, runId: null, curveRunId: null, error: null },
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
  saveComparison: $('saveComparisonButton'), saveStatus: $('comparisonSaveStatus'),
  comparisonCondition: $('comparisonCondition'), primary: $('resultsPrimary'), analysis: $('resultsAnalysis'),
  assessmentWorkbench: $('assessmentWorkbench'), assessmentError: $('assessmentError'),
  assessmentRefresh: $('assessmentRefreshButton'), assessmentHeatmap: $('assessmentHeatmap'),
  assessmentCurves: $('assessmentCurves'), assessmentCurveLegend: $('assessmentCurveLegend'),
  assessmentAirportSelect: $('assessmentAirportSelect'), assessmentRiskTable: $('assessmentRiskTable'),
  assessmentCurveRunSelect: $('assessmentCurveRunSelect'),
  assessmentComparisons: $('assessmentComparisons'), assessmentRunSelect: $('assessmentRunSelect'),
  assessmentIndicatorTable: $('assessmentIndicatorTable'), assessmentComposite: $('assessmentComposite'),
};

function view() { return state.workspaceStates[state.workspace]; }
function esc(value) { const node = document.createElement('span'); node.textContent = String(value ?? ''); return node.innerHTML; }
function shortHash(value) { const text = String(value || ''); return text.length > 12 ? `${text.substring(0, 12)}…` : text || '未记录'; }
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
function runIdentity(runId, { candidate = false } = {}) {
  const run = state.runById.get(runId) || { run_id: runId };
  const saved = view().payload?.run_identities?.[runId];
  const identity = formatRunIdentity(saved ? { ...run, display_identity: saved } : run, { multi: state.workspace === 'multi' });
  const ids = view().payload?.run_ids || Object.values(view().payload?.roles || {});
  const index = ids.indexOf(runId);
  let role = '';
  if (!candidate && index >= 0) {
    if (state.workspace === 'multi') role = `D${index}`;
    else if (state.workspace === 'damage') role = Object.entries(view().payload?.roles || {}).find(([, id]) => id === runId)?.[0] || '';
    else if (state.workspace === 'configuration') role = runId === view().payload?.baseline_run_id ? '基准方案' : '对照方案';
  }
  const title = state.workspace === 'multi' && identity.title === '无损毁' ? '基准场景' : identity.title;
  return { ...identity, title, role };
}
function selectionLabel(runId, options = {}) {
  const identity = runIdentity(runId, options);
  if (state.workspace === 'multi' && !options.candidate) {
    return [identity.role, identity.title, identity.short_id].filter(Boolean).join(' · ');
  }
  const baselineSubtitle = state.workspace === 'multi' && identity.title === '基准场景' ? '无损毁' : '';
  return [identity.role, identity.title, baselineSubtitle, identity.subtitle, identity.short_id].filter(Boolean).join(' · ');
}
function identityHtml(runId, options = {}) {
  const identity = runIdentity(runId, options);
  if (state.workspace === 'multi' && !options.candidate) {
    const baselineSubtitle = identity.title === '基准场景' ? '无损毁' : '';
    const configSummary = identity.subtitle.split(' · ').slice(0, -1).join(' · ');
    const subtitle = options.scope === 'overview' ? baselineSubtitle
      : options.scope === 'focus' ? [baselineSubtitle, identity.short_id].filter(Boolean).join(' · ')
      : [baselineSubtitle, configSummary].filter(Boolean).join(' · ');
    return `<span class="identity-chip primary">${esc(identity.role)}</span><span class="run-label multi-label" title="${esc(identity.detail)}"><b>${esc(identity.title)}</b>${subtitle ? `<small>${esc(subtitle)}</small>` : ''}</span>`;
  }
  const candidateIdentity = state.workspace === 'multi' && identity.title === '基准场景'
    ? { ...identity, subtitle: `无损毁 · ${identity.subtitle}` } : identity;
  return `${identity.role ? `<span class="identity-chip primary">${esc(identity.role)}</span>` : ''}${runIdentityMarkup(candidateIdentity, esc)}`;
}
function damageKey(runId) { return state.runById.get(runId)?.run_config?.damage_scenario_id ?? null; }

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
function compactAssessmentRun(label, runId) {
  const parts = String(label || '').split(' · ');
  return { primary: parts.length >= 4 ? parts[1] : parts[0] || '场景未记录',
    context: parts.length >= 4 ? parts.slice(2).join(' · ') : '',
    secondary: shortRunId(runId), title: `${label || '场景未记录'} · ${runId || '来源未记录'}` };
}
function renderAssessmentHeatmap() {
  const overview = state.assessment.overview;
  const scenarios = overview?.scenario_keys ?? overview?.scenario_types ?? [];
  const matrix = overview?.run_cells ?? overview?.cells;
  const label = (key) => overview?.scenario_labels?.[key] ?? assessmentScenarioLabel(key);
  if (!overview?.airport_ids?.length || !scenarios.length) {
    refs.assessmentHeatmap.innerHTML = '<div class="results-placeholder">暂无评估落库结果</div>'; return;
  }
  const header = scenarios.map((scenario) => {
    const compact = compactAssessmentRun(label(scenario), scenario);
    const current = scenario === state.assessment.runId;
    return `<th data-run-id="${esc(scenario)}"${current ? ' aria-current="true"' : ''} title="${esc(compact.title)}"><span class="assessment-run-label">${esc(compact.primary)}</span><small>${esc(compact.secondary)}${current ? ' · 当前评价' : ''}</small></th>`;
  }).join('');
  const rows = overview.airport_ids.map((airportId) => {
    const cells = scenarios.map((scenario) => {
      const cell = matrix?.[airportId]?.[scenario];
      if (!cell) return '<td class="assessment-heat-cell assessment-heat-empty" title="暂无评估">—</td>';
      const value = assessmentCellValue(cell);
      const isCapacity = cell.metric_kind === 'capacity_availability_v1';
      const display = value == null || !Number.isFinite(value) ? '未记录' : (isCapacity ? `${number(value * 100, 1)}%` : formatDecimal(value, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
      const coverage = isCapacity ? ` · 有效窗 ${cell.effective_windows ?? '未记录'} · 不可用窗 ${cell.unavailable_windows ?? '未记录'}` : '';
      const title = isCapacity
        ? `${assessmentAirportName(airportId)} · ${label(scenario)} · 运行期容量占用率 ${display}${coverage} · 来源 ${cell.source_run_id || '未记录'} · Snapshot ${cell.snapshot_hash || '未记录'} · Metrics ${cell.metrics_hash || '未记录'}`
        : `${assessmentAirportName(airportId)} · ${label(scenario)} · 支撑度 ${display} · 衰减率 ${number(cell.decline_rate * 100, 1)}%`;
      return `<td class="assessment-heat-cell${scenario === state.assessment.runId ? ' assessment-heat-current' : ''}" style="background:${assessmentHeatColor(value ?? 0)}" title="${esc(title)}">${esc(display)}</td>`;
    }).join('');
    return `<tr><th title="${esc(airportId)}">${esc(assessmentAirportName(airportId))}</th>${cells}</tr>`;
  }).join('');
  refs.assessmentHeatmap.innerHTML = `<table class="assessment-heat-table"><thead><tr><th>机场</th>${header}</tr></thead><tbody>${rows}</tbody></table>`;
}
function renderAssessmentCurves() {
  const airportIds = state.assessment.overview?.airport_ids || [];
  refs.assessmentAirportSelect.innerHTML = airportIds.map((id) => `<option value="${esc(id)}" ${id === state.assessment.airportId ? 'selected' : ''}>${esc(assessmentAirportName(id))}</option>`).join('');
  const allSeries = state.assessment.curves?.series || [];
  const chosen = state.assessment.curveRunId || state.assessment.runId;
  const series = allSeries.filter(row => row.source_run_id === chosen);
  refs.assessmentCurveRunSelect.innerHTML = '<option value="">跟随综合评价 Run</option>' + allSeries.map(row => {
    const compact = compactAssessmentRun(row.scenario_label, row.source_run_id);
    return `<option value="${esc(row.source_run_id)}" title="${esc(compact.title)}">${esc([compact.primary, compact.context, compact.secondary].filter(Boolean).join(' · '))}</option>`;
  }).join('');
  refs.assessmentCurveRunSelect.value = state.assessment.curveRunId || '';
  refs.assessmentCurveRunSelect.disabled = !allSeries.length;
  refs.assessmentCurveRunSelect.title = series[0] ? compactAssessmentRun(series[0].scenario_label, chosen).title : chosen || '来源未记录';
  if (!allSeries.length) {
    refs.assessmentCurves.innerHTML = '<div class="results-placeholder">当前机场暂无评估曲线</div>';
    refs.assessmentCurveLegend.innerHTML = ''; return;
  }
  if (!series.length) {
    refs.assessmentCurves.innerHTML = '<div class="results-placeholder">所选 Run 暂无当前机场的容量曲线，请选择其他 Run</div>';
    refs.assessmentCurveLegend.innerHTML = ''; return;
  }
  const allSteps = series.flatMap((row) => row.steps || []).map(Number).filter(Number.isFinite);
  if (!allSteps.length) {
    refs.assessmentCurves.innerHTML = '<div class="results-placeholder">所选 Run 暂无正式时间窗</div>';
    refs.assessmentCurveLegend.innerHTML = ''; return;
  }
  const minStep = Math.min(...allSteps); const maxStep = Math.max(...allSteps); const stepSpan = Math.max(1, maxStep - minStep);
  const width = 760; const height = 300; const pad = { left: 46, right: 22, top: 30, bottom: 44 };
  const x = (value) => pad.left + (Number(value) - minStep) / stepSpan * (width - pad.left - pad.right);
  const y = (value) => height - pad.bottom - Math.max(0, Math.min(1, Number(value))) * (height - pad.top - pad.bottom);
  const grid = [0, .5, 1].map((value) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(value)}" y2="${y(value)}" stroke="#29495d"/><text x="${pad.left - 7}" y="${y(value) + 3}" text-anchor="end" fill="#7894a6" font-size="10">${value.toFixed(1)}</text>`).join('');
  const lines = series.map((row, index) => {
    const values = row.values || row.support || [];
    const segments = [[]];
    (row.steps || []).forEach((step, pointIndex) => {
      const value = values[pointIndex];
      if (value == null || !Number.isFinite(Number(value)) || !Number.isFinite(Number(step))) { segments.push([]); return; }
      segments.at(-1).push(`${x(step)},${y(value)}`);
    });
    return segments.filter(points => points.length).map(points => `<polyline points="${points.join(' ')}" fill="none" stroke="${colors[index % colors.length]}" stroke-width="2.4" vector-effect="non-scaling-stroke"/>`).join('');
  }).join('');
  const metricLabel = series[0]?.metric_label || (series[0]?.metric_kind === 'capacity_availability_v1' ? '起降容量可用比例' : '评估指标');
  const steps = [...new Set(allSteps)].sort((a, b) => a - b);
  const ticks = [...new Set(Array.from({ length: Math.min(8, steps.length) }, (_, i) => steps[Math.round(i * (steps.length - 1) / Math.max(1, Math.min(8, steps.length) - 1))]))];
  const axis = ticks.map(step => `<text x="${x(step)}" y="${height - pad.bottom + 18}" text-anchor="middle" fill="#9fb7c6" font-size="11">T${esc(step)}</text>`).join('');
  refs.assessmentCurves.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(metricLabel)}逐窗曲线"><text x="${pad.left}" y="16" fill="#9fb7c6" font-size="11">${esc(metricLabel)}</text>${grid}<path d="M${pad.left} ${height - pad.bottom}H${width - pad.right}M${pad.left} ${pad.top}V${height - pad.bottom}" stroke="#48687b"/>${axis}<text x="${width - pad.right}" y="${height - 4}" text-anchor="end" fill="#9fb7c6" font-size="10">时间窗</text>${lines}</svg>`;
  refs.assessmentCurveLegend.innerHTML = series.map((row, index) => {
    const compact = compactAssessmentRun(row.scenario_label || assessmentScenarioLabel(row.scenario_type), row.source_run_id);
    return `<span title="${esc(compact.title)}"><i style="background:${colors[index % colors.length]}"></i>${esc([compact.primary, compact.context, compact.secondary].filter(Boolean).join(' · '))}</span>`;
  }).join('');
}
function currentAssessmentEvaluation() {
  const items = Array.isArray(state.assessment.evaluations?.items) ? state.assessment.evaluations.items : [];
  return items.find((item) => item.run_id === state.assessment.runId) || items[0] || null;
}
function renderAssessmentEvaluation() {
  const items = Array.isArray(state.assessment.evaluations?.items) ? state.assessment.evaluations.items : [];
  refs.assessmentRunSelect.innerHTML = items.map((item) => {
    const context = item.run || {};
    const situation = context.situation_name || context.situation_id || '情境未记录';
    const damage = context.damage_scenario_id || '无损毁';
    const status = item.provenance_status === 'stale' ? '来源已过期' : item.status === 'available' ? '可评价' : '不可用';
    const title = `${item.run_id} · ${situation} · ${damage}${context.owner_user_id ? ` · ${context.owner_user_id}` : ''}`;
    return `<option value="${esc(item.run_id)}" title="${esc(title)}">${esc(shortRunId(item.run_id))} · ${esc(situation)} · ${esc(damage)} · ${esc(status)}</option>`;
  }).join('');
  refs.assessmentRunSelect.disabled = !items.length;
  refs.assessmentRunSelect.value = state.assessment.runId || '';
  const evaluation = currentAssessmentEvaluation();
  if (!evaluation) {
    refs.assessmentIndicatorTable.innerHTML = '<div class="results-placeholder">尚无综合评价结果</div>';
    refs.assessmentComposite.innerHTML = '<div class="results-placeholder">尚无综合评价结果</div>';
    return;
  }
  const context = evaluation.run || {};
  const situation = context.situation_name || context.situation_id || '未记录';
  const damage = context.damage_scenario_id || '无损毁';
  const sourceLabel = evaluation.weight_source === 'ahp' ? 'AHP' : '专家评分';
  const traceRows = `<dl class="assessment-trace"><dt>Run</dt><dd title="${esc(evaluation.run_id)}">${esc(evaluation.run_id)}</dd><dt>情境</dt><dd title="${esc(context.situation_id || '')}">${esc(situation)}</dd><dt>损毁场景</dt><dd>${esc(damage)}</dd><dt>Snapshot</dt><dd title="${esc(evaluation.snapshot_hash || '')}">${esc(shortHash(evaluation.snapshot_hash))}</dd><dt>Metrics</dt><dd title="${esc(evaluation.metrics_hash || '')}">${esc(shortHash(evaluation.metrics_hash))}</dd><dt>权重来源</dt><dd>${esc(sourceLabel)}</dd></dl>`;
  if (evaluation.status !== 'available') {
    const reason = evaluation.unavailable_reason || '正式评价输入不完整';
    const label = evaluation.provenance_status === 'stale' ? '评价来源已过期' : '不可用';
    refs.assessmentIndicatorTable.innerHTML = `<div class="results-placeholder">${esc(label)}：${esc(reason)}</div>`;
    refs.assessmentComposite.innerHTML = `<div class="assessment-unavailable"><p>${esc(label)}：${esc(reason)}</p>${traceRows}</div>`;
    return;
  }
  const ratingLabels = { very_low: '很低', low: '较低', medium: '中等', high: '较高', very_high: '很高' };
  const indicatorRows = (evaluation.indicators || []).map((item) => {
    const membership = item.evidence?.membership || {};
    const dominant = Object.entries(membership).sort((a, b) => Number(b[1]) - Number(a[1]))[0];
    const contribution = item.evidence?.contribution;
    return `<tr><th title="${esc(item.calculation_type)}">${esc(item.name)}</th><td>${esc(number(Number(item.raw_value) * 100, 2))}%</td><td>${esc(number(Number(item.normalized_value) * 100, 2))}%</td><td>${esc(number(Number(item.weight) * 100, 2))}%</td><td>${contribution == null ? '未记录' : `${esc(number(Number(contribution) * 100, 2))}%`}</td><td>${esc(dominant ? ratingLabels[dominant[0]] || dominant[0] : '未记录')}</td></tr>`;
  }).join('');
  refs.assessmentIndicatorTable.innerHTML = `<table><thead><tr><th>指标</th><th>原始值</th><th>归一化</th><th>权重</th><th>贡献</th><th>隶属等级</th></tr></thead><tbody>${indicatorRows}</tbody></table>`;
  const membership = evaluation.overall_membership || {};
  const dominant = Object.entries(membership).sort((a, b) => Number(b[1]) - Number(a[1]))[0];
  const traceTitle = `Snapshot ${evaluation.snapshot_hash || '未记录'} · Metrics ${evaluation.metrics_hash || '未记录'} · ${evaluation.calculation_version || '计算版本未记录'}`;
  refs.assessmentComposite.innerHTML = `<div class="assessment-composite-score" title="${esc(traceTitle)}"><strong>${esc(number(Number(evaluation.support_index) * 100, 2))}</strong><span>综合能力指数 / 100</span></div><div>${traceRows}<dl><dt>评价等级</dt><dd>${esc(dominant ? ratingLabels[dominant[0]] || dominant[0] : '未记录')}</dd><dt>指标集</dt><dd>${esc(evaluation.indicator_set?.version || '未记录')}</dd><dt>计算版本</dt><dd>${esc(evaluation.calculation_version || '未记录')}</dd></dl></div>`;
}
function renderAssessmentRisk() {
  const evaluations = Array.isArray(state.assessment.evaluations?.items) ? state.assessment.evaluations.items : [];
  if (evaluations.length) {
    const rows = evaluations.map((item) => {
      const risk = item.risk || {};
      if (risk.status !== 'available') return `<tr><td title="${esc(item.run_id)}">${esc(shortRunId(item.run_id))}</td><td colspan="3">不可用：${esc(risk.reason || item.unavailable_reason || '正式输入不完整')}</td></tr>`;
      return `<tr><td title="${esc(item.run_id)}">${esc(shortRunId(item.run_id))}</td><td><span class="assessment-risk-badge" data-risk-level="${esc(risk.level)}">${esc(ASSESSMENT_RISK_LABELS[risk.level] || risk.level)}</span></td><td>${esc(number(Number(risk.score) * 100, 2))}%</td><td>${esc(item.weight_source === 'ahp' ? 'AHP' : '专家评分')}</td></tr>`;
    }).join('');
    refs.assessmentRiskTable.innerHTML = `<table><thead><tr><th>Run</th><th>风险等级</th><th>风险值</th><th>权重</th></tr></thead><tbody>${rows}</tbody></table>`;
    return;
  }
  const risks = state.assessment.overview?.risks || [];
  if (!risks.length) {
    const reason = state.assessment.overview?.risk_status?.reason || '未配置独立风险评估模型或正式专家评分';
    refs.assessmentRiskTable.innerHTML = `<div class="results-placeholder">${esc(reason)}</div>`; return;
  }
  const rows = risks.map((risk) => `<tr><td title="${esc(risk.airport_id)}">${esc(assessmentAirportName(risk.airport_id))}</td><td><span class="assessment-risk-badge" data-risk-level="${esc(risk.risk_level)}">${esc(ASSESSMENT_RISK_LABELS[risk.risk_level] || risk.risk_level)}</span></td><td>${esc(number(Number(risk.risk_score) * 100, 1))}%</td><td>${esc(risk.updated_at || '—')}</td></tr>`).join('');
  refs.assessmentRiskTable.innerHTML = `<table><thead><tr><th>机场</th><th>风险等级</th><th>评分</th><th>更新时间</th></tr></thead><tbody>${rows}</tbody></table>`;
}
const recordModeLabels = { damage_effect: '损毁影响', cluster_effect: '组群调整', multi_scenario: '多损毁', configuration: '方案配置' };
const recordStatusLabels = { current: '来源有效', stale: '来源已过期', unavailable: '来源不可用' };
function savedRecordTime(value) {
  if (!value) return '时间未记录';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}
function savedEvidence(value) {
  if (value == null || typeof value !== 'object') return `<span>${esc(value == null ? '未记录' : String(value))}</span>`;
  const entries = Object.entries(value);
  if (!entries.length) return `<span>${Array.isArray(value) ? '[]' : '{}'}</span>`;
  return `<dl class="comparison-evidence">${entries.map(([key, fact]) => `<dt>${esc(key)}</dt><dd>${savedEvidence(fact)}</dd>`).join('')}</dl>`;
}
function comparisonRecordDetails(item) {
  const envelope = item.result_json;
  if (!envelope || typeof envelope !== 'object' || envelope.schema_version !== 'comparison-record.v1') return '<p>保存事实不可读取</p>';
  const focus = envelope.focus_comparison || {};
  const valueLabels = { required_sorties_total: '任务需求', fulfilled_sorties_total: '已履行', unmet_sorties_total: '任务缺口', additional_sorties_total: '额外出动', scheduled_sorties_total: '总出动', completion_ratio: '履行率' };
  const values = Object.entries(focus.values || {}).map(([key, value]) => `<tr><th>${esc(valueLabels[key] || key)}</th><td>${esc(displayValue(value.baseline))}</td><td>${esc(displayValue(value.current))}</td><td>${esc(displayValue(value.delta))}</td></tr>`).join('');
  const components = Object.entries(focus.objective_components || {}).map(([key, value]) => `<tr><th>${esc(key)}</th><td>${esc(displayValue(value.baseline))}</td><td>${esc(displayValue(value.current))}</td><td>${esc(value.definition_comparable === false ? '不可直接比较' : displayValue(value.delta))}</td></tr>`).join('');
  const notes = Object.entries(focus.objective_components || {}).filter(([, value]) => value.definition_comparable === false || !value.recorded || value.reasons?.length)
    .map(([key, value]) => `<li>${esc(key)}：${esc((value.reasons || []).join('；') || (value.recorded ? '不可直接比较' : '未记录'))}</li>`).join('');
  const provenance = Object.fromEntries(['base_run_id','compared_run_id','base_snapshot_hash','base_metrics_hash','compared_snapshot_hash','compared_metrics_hash','comparison_version'].map(key => [key, item[key]]));
  const basic = [['记录', item.id], ['比较类型', recordModeLabels[item.comparison_mode] || item.comparison_mode],
    ['基准 Run', item.base_run_id], ['对照 Run', item.compared_run_id], ['作者', item.created_by_user_id],
    ['保存时间（北京时间）', savedRecordTime(item.created_at)], ['来源状态', `${recordStatusLabels[item.provenance_status] || '来源不可用'} (${item.provenance_status})`]];
  return `<div class="comparison-record-detail-body"><h4>基本信息</h4><dl class="comparison-record-basics">${basic.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value || '未记录')}</dd>`).join('')}</dl><h4>核心结果 · 保存事实</h4><table><thead><tr><th>指标</th><th>基准</th><th>对照</th><th>差值</th></tr></thead><tbody>${values}${components}</tbody></table>${notes ? `<h4>不可比／缺失说明</h4><ul>${notes}</ul>` : ''}<p class="comparison-record-conclusion">${esc(item.conclusion || '')}${item.conclusion ? '<br>' : ''}${esc((focus.conclusions || []).join('；'))}</p><details class="comparison-record-evidence"><summary>保存的 pair 与结构事实</summary>${savedEvidence({pair_facts: envelope.pair_facts, structural_changes: focus.structural_changes, timeline: focus.timeline, assurance: focus.assurance})}</details><details class="comparison-record-evidence"><summary>provenance / eligibility · 来源与资格证据</summary>${savedEvidence({provenance, provenance_status: item.provenance_status, reasons: item.provenance_reasons, eligibility: envelope.eligibility_evidence})}</details></div>`;
}
function savedRunIdentity(item, id) {
  const saved = item.result_json?.labels?.runs?.[id];
  if (saved?.display_identity) return runIdentityMarkup(formatRunIdentity(saved, { multi: item.comparison_mode === 'multi_scenario' }), esc);
  return `<span class="run-label" title="${esc(id || '')}"><b>${esc(saved?.label || (id ? '历史 Run' : '无权查看'))}</b><small>${esc(shortRunId(id) || '无权查看')}</small></span>`;
}
function renderAssessmentComparisons() {
  const items = state.assessment.comparisons?.items || [];
  if (!items.length) { refs.assessmentComparisons.innerHTML = '<div class="results-placeholder">暂无结果比较记录，可在结果分析中保存当前重点对照</div>'; return; }
  refs.assessmentComparisons.innerHTML = items.map(item => {
    const meta = `<div class="comparison-record-meta"><b>${esc(recordModeLabels[item.comparison_mode] || item.comparison_mode)}</b><span class="comparison-record-pair">${savedRunIdentity(item, item.base_run_id)} → ${savedRunIdentity(item, item.compared_run_id)}</span><span data-provenance-status="${esc(item.provenance_status)}" title="${esc((item.provenance_reasons || []).join('；'))}">${esc(recordStatusLabels[item.provenance_status] || '来源不可用')} (${esc(item.provenance_status)})</span><span>${esc(item.created_by_user_id)}</span><time title="${esc(item.created_at)} · 北京时间">${esc(savedRecordTime(item.created_at))}</time></div>`;
    const summary = `<span class="comparison-record-summary" title="${esc(item.conclusion || '')}">${esc(item.conclusion || '无权查看保存事实')}</span>`;
    return item.result_json
      ? `<details class="comparison-record-details"><summary>${meta}<div class="comparison-record-preview">${summary}<span class="comparison-record-toggle">查看保存事实</span></div></summary>${comparisonRecordDetails(item)}</details>`
      : `<article class="comparison-record-details">${meta}${summary}</article>`;
  }).join('');
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
  renderAssessmentHeatmap(); renderAssessmentCurves(); renderAssessmentEvaluation(); renderAssessmentRisk(); renderAssessmentComparisons();
}
async function loadAssessmentWorkspace({ force = false } = {}) {
  if (state.assessment.status === 'loading' && !force) return;
  state.assessment.status = 'loading'; state.assessment.error = null; renderAssessmentWorkspace();
  try {
    const [overview, comparisons, evaluations] = await Promise.all([
      apiFetch('/api/results/assessment/overview'),
      apiFetch('/api/results/assessment/comparisons?limit=50'),
      apiFetch('/api/results/assessment/evaluations?limit=100'),
    ]);
    state.assessment.overview = overview; state.assessment.comparisons = comparisons; state.assessment.evaluations = evaluations;
    if (!state.assessment.airportId || !overview.airport_ids.includes(state.assessment.airportId)) state.assessment.airportId = overview.airport_ids[0] || null;
    const evaluationItems = Array.isArray(evaluations.items) ? evaluations.items : [];
    const evaluationRunIds = evaluationItems.map((item) => item.run_id);
    if (!state.assessment.runId || !evaluationRunIds.includes(state.assessment.runId)) state.assessment.runId = evaluationRunIds[0] || null;
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
  const items = response.items || [];
  for (const run of items) state.runById.set(run.run_id, { ...state.runById.get(run.run_id), ...run });
  return items;
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
async function loadComparison(mode, selection) {
  const item = state.workspaceStates[mode];
  const generation = ++item.comparisonGeneration;
  item.selection = structuredClone(selection); item.payload = null; item.comparisonLoading = true;
  renderSaveComparison();
  try {
    const payload = await requestComparison(mode, structuredClone(selection));
    if (generation !== item.comparisonGeneration) return false;
    item.payload = payload; item.windowStart = 0;
    return true;
  } catch (error) {
    if (generation !== item.comparisonGeneration) return false;
    throw error;
  } finally {
    if (generation === item.comparisonGeneration) item.comparisonLoading = false;
    renderSaveComparison();
  }
}
async function restoreComparison(mode) {
  const item = state.workspaceStates[mode];
  if (!item.selection) return false;
  try { return await loadComparison(mode, item.selection); }
  catch (_) { item.payload = null; item.selection = null; return false; }
}
async function applyDefault(mode) {
  if (!isComparisonWorkspace(mode)) return;
  const item = state.workspaceStates[mode];
  if (item.payload || item.selection) return;
  if (mode === 'damage') {
    if (!state.damageCandidates.length) return;
    const row = state.damageCandidates[0];
    item.selection = { r0_run_id: row.r0_run_id, r1_run_id: row.r1_run_id, r2_run_id: row.r2_run_id };
  } else {
    const candidate = await discoverCandidates(mode); if (!candidate || item.selection) return;
    const max = mode === 'multi' ? 6 : 5;
    const runIds = [candidate.baseRunId, ...candidate.items.map((row) => row.run_id)];
    const schemes = new Set();
    const unique = [...new Set(runIds)].filter(id => {
      if (mode !== 'multi') return true;
      const key = damageKey(id); if (schemes.has(key)) return false; schemes.add(key); return true;
    }).slice(0, max);
    item.selection = {
      run_ids: unique, baseline_run_id: candidate.baseRunId,
      comparison_run_id: unique.find((id) => id !== candidate.baseRunId),
    };
  }
  await loadComparison(mode, item.selection);
}
async function loadInitial() {
  refs.page.setAttribute('aria-busy', 'true');
  try {
    const result = await apiFetch('/api/runs?status=succeeded&limit=500');
    state.runs = result.items || []; state.runById = new Map(state.runs.map((run) => [run.run_id, run]));
    state.damageCandidates = (await apiFetch('/api/results/damage-candidates')).items || [];
    await Promise.all(MODES.map((mode) => restoreComparison(mode)));
    if (isComparisonWorkspace(state.workspace)) await applyDefault(state.workspace);
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
    return `<a class="run-identity" href="/runs/${encodeURIComponent(id)}" title="${esc(runIdentity(id).detail)}">${identityHtml(id)}</a>`;
  }).join('<i class="identity-separator" aria-hidden="true"></i>');
  refs.focusControls.classList.toggle('hidden', state.workspace === 'damage');
  if (state.workspace !== 'damage') {
    const options = ids.map((id) => `<option value="${esc(id)}" title="${esc(runIdentity(id).detail)}">${esc(selectionLabel(id))}</option>`).join('');
    refs.focusBaseline.innerHTML = options; refs.focusComparison.innerHTML = options;
    refs.focusBaseline.value = payload.baseline_run_id; refs.focusComparison.value = payload.comparison_run_id;
    $('focusBaselineIdentity').innerHTML = identityHtml(payload.baseline_run_id, { scope: 'focus' });
    $('focusComparisonIdentity').innerHTML = identityHtml(payload.comparison_run_id, { scope: 'focus' });
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
    return `<article title="${esc(runIdentity(id).detail)}"><div class="run-identity">${identityHtml(id, { scope: 'overview' })}</div><span><i>实际履行 <b>${displayValue(summary.fulfilled_sorties_total)}</b></i><i>任务缺口 <b>${displayValue(summary.unmet_sorties_total)}</b></i><i>出动 <b>${displayValue(summary.scheduled_sorties_total)}</b></i><i class="multi-objective">J <b>${displayValue(technical.objective, { formatter: (v) => number(v, 4) })}</b></i></span></article>`;
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
  renderSaveComparison();
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
    refs.overlayBody.innerHTML = `<p class="overlay-title">${overlayModeName()}</p><div class="candidate-list">${rows.map(({ row, index }) => `<label class="candidate-row"><input type="radio" name="damageTriple" value="${index}" ${item.draft.damageIndex === index ? 'checked' : ''}><span><b>R0</b>${identityHtml(row.r0_run_id, { candidate: true })}<b>R1</b>${identityHtml(row.r1_run_id, { candidate: true })}<b>R2</b>${identityHtml(row.r2_run_id, { candidate: true })}<small>损毁影响与组群调整均保留</small></span></label>`).join('') || '<p>没有符合条件的组合。</p>'}</div>`;
    refs.overlayBody.querySelectorAll('[name="damageTriple"]').forEach((radio) => radio.addEventListener('change', () => { item.draft.damageIndex = Number(radio.value); renderOverlay(); }));
    refs.apply.disabled = item.draft.damageIndex < 0 || item.draft.damageIndex === null; return;
  }
  const baseOptions = state.runs.filter((run) => runMatches(run, query)).map((run) => `<option value="${esc(run.run_id)}" title="${esc(runIdentity(run.run_id, { candidate: true }).detail)}" ${item.draft.baseRunId === run.run_id ? 'selected' : ''}>${esc(selectionLabel(run.run_id, { candidate: true }))}</option>`).join('');
  const candidates = [state.runById.get(item.draft.baseRunId), ...item.draft.comparableRuns].filter(Boolean).filter((run, index, array) => array.findIndex((other) => other.run_id === run.run_id) === index).filter((run) => runMatches(run, query));
  const repeats = repeatedRunIds([...state.runById.values()]);
  const candidateRows = candidates.map(run => {
    const selected = item.draft.selectedRunIds.has(run.run_id);
    const duplicateScheme = state.workspace === 'multi' && !selected && [...item.draft.selectedRunIds].some(id => damageKey(id) === damageKey(run.run_id));
    const disabled = run.run_id === item.draft.baseRunId || duplicateScheme;
    return `<label class="candidate-row" title="${esc(runIdentity(run.run_id, { candidate: true }).detail)}"><input type="checkbox" data-run-id="${esc(run.run_id)}" ${selected ? 'checked' : ''} ${disabled ? 'disabled' : ''}><span>${identityHtml(run.run_id, { candidate: true })}<small>${run.run_id === item.draft.baseRunId ? '参照Run' : duplicateScheme ? '该损毁方案已选择一次 Run' : '严格可比'}${repeats.has(run.run_id) ? ' · 同配置重复运行' : ''}</small></span></label>`;
  }).join('');
  refs.overlayBody.innerHTML = `<label class="overlay-title">参照Run<select id="comparisonBaseRun" class="control"><option value="">请选择</option>${baseOptions}</select>${item.draft.baseRunId ? identityHtml(item.draft.baseRunId, { candidate: true }) : ''}</label><p class="overlay-title">${overlayModeName()}</p><div class="candidate-list">${candidateRows || '<p>请先选择参照Run。</p>'}</div>`;
  $('comparisonBaseRun').addEventListener('change', async (event) => { item.draft.baseRunId = event.target.value || null; item.draft.selectedRunIds = new Set(item.draft.baseRunId ? [item.draft.baseRunId] : []); item.draft.comparableRuns = item.draft.baseRunId ? await loadComparable(item.draft.baseRunId, state.workspace === 'multi' ? 'multi_scenario' : 'configuration') : []; renderOverlay(); });
  refs.overlayBody.querySelectorAll('[data-run-id]').forEach((box) => box.addEventListener('change', () => { box.checked ? item.draft.selectedRunIds.add(box.dataset.runId) : item.draft.selectedRunIds.delete(box.dataset.runId); renderOverlay(); }));
  const count = item.draft.selectedRunIds.size; refs.apply.disabled = count < 2 || (state.workspace === 'multi' && new Set([...item.draft.selectedRunIds].map(damageKey)).size !== count) || count > (state.workspace === 'multi' ? 6 : 5);
}
async function applyOverlay() {
  const mode = state.workspace; const item = view(); refs.apply.disabled = true;
  try {
    if (state.workspace === 'damage') {
      const row = state.damageCandidates[item.draft.damageIndex];
      item.selection = { r0_run_id: row.r0_run_id, r1_run_id: row.r1_run_id, r2_run_id: row.r2_run_id };
    } else {
      const runIds = [...item.draft.selectedRunIds]; const baseline = item.draft.baseRunId;
      item.selection = { run_ids: runIds, baseline_run_id: baseline, comparison_run_id: runIds.find((id) => id !== baseline) };
    }
    if (await loadComparison(mode, item.selection)) { closeOverlay(); persistSessionState(); render(); }
  } catch (error) { showError(error); refs.apply.disabled = false; }
}
async function changeFocus() {
  if (state.workspace === 'damage' || !view().selection) return;
  const baseline = refs.focusBaseline.value; const comparison = refs.focusComparison.value;
  if (!baseline || !comparison || baseline === comparison) { showError(new Error('参照Run和对照Run必须不同。')); return; }
  const mode = state.workspace;
  const selection = { ...view().selection, baseline_run_id: baseline, comparison_run_id: comparison };
  if (await loadComparison(mode, selection)) { persistSessionState(); render(); }
}

function captureComparisonSave() {
  if (!state.canSaveComparison || !isComparisonWorkspace(state.workspace)) return null;
  const item = view(); const focus = currentFocus(); const context = item.payload?.save_context;
  if (item.comparisonLoading || !item.selection || !focus || focus.eligible !== true || item.payload?.eligible === false || !context?.comparison_version) return null;
  const sources = context.sources;
  const ids = state.workspace === 'damage' ? [item.selection.r0_run_id, item.selection.r1_run_id, item.selection.r2_run_id] : item.selection.run_ids;
  if (!Array.isArray(sources) || sources.length !== ids.length || !ids.every((id) => sources.some((source) => source.run_id === id && /^[0-9a-f]{64}$/.test(source.snapshot_hash) && /^[0-9a-f]{64}$/.test(source.metrics_hash)))) return null;
  const pair = [focus.baseline_run_id, focus.comparison_run_id];
  const expectedPair = state.workspace === 'damage'
    ? (item.activeDamageStage === 'cluster' ? [item.selection.r1_run_id, item.selection.r2_run_id] : [item.selection.r0_run_id, item.selection.r1_run_id])
    : [item.selection.baseline_run_id, item.selection.comparison_run_id];
  if (!pair[0] || pair[0] === pair[1] || pair.some((id, index) => id !== expectedPair[index])) return null;
  const mode = state.workspace === 'damage' ? (item.activeDamageStage === 'cluster' ? 'cluster_effect' : 'damage_effect') : state.workspace === 'multi' ? 'multi_scenario' : 'configuration';
  const body = structuredClone({ comparison_mode: mode, selection: item.selection, expected_provenance: sources, comparison_version: context.comparison_version });
  return { workspace: state.workspace, stage: item.activeDamageStage, pair, body,
    requestGeneration: item.comparisonGeneration, key: JSON.stringify([state.workspace, item.activeDamageStage, item.comparisonGeneration, body]) };
}
function renderSaveComparison() {
  const snapshot = captureComparisonSave();
  refs.saveComparison.classList.toggle('hidden', !state.canSaveComparison || !isComparisonWorkspace(state.workspace));
  const pending = snapshot && state.pendingSaves.has(snapshot.key);
  refs.saveComparison.disabled = !snapshot || pending;
  refs.saveComparison.textContent = pending ? '保存中…' : state.workspace === 'damage'
    ? `保存比较记录 · ${view().activeDamageStage === 'cluster' ? '组群调整' : '损毁影响'}` : '保存比较记录';
  refs.saveComparison.title = snapshot ? `仅保存 ${snapshot.pair.join(' → ')}` : '等待当前重点对照完成并通过严格校验';
}
async function saveCurrentComparison() {
  const snapshot = captureComparisonSave();
  if (!snapshot || state.pendingSaves.has(snapshot.key)) return;
  const operation = ++state.saveGeneration;
  state.pendingSaves.add(snapshot.key); renderSaveComparison();
  try {
    const response = await apiFetch('/api/results/comparison-records', { method: 'POST', body: snapshot.body });
    const record = response.record;
    // The response belongs to the captured pair, even if the current UI has moved on.
    if (record.base_run_id !== snapshot.pair[0] || record.compared_run_id !== snapshot.pair[1]) throw new Error('保存响应与请求对照不一致');
    if (operation === state.saveGeneration) {
      refs.saveStatus.textContent = `${response.created ? '已保存' : '已有记录'} ${snapshot.pair.map(shortRunId).join(' → ')}`;
      refs.saveStatus.title = `${snapshot.pair.join(' → ')} · ${record.id}`;
      refs.saveStatus.classList.remove('hidden');
    }
    // Refresh only the read-only history, without overwriting any comparison workspace.
    if (state.assessment.comparisons) {
      state.assessment.comparisons = await apiFetch('/api/results/assessment/comparisons?limit=50');
      renderAssessmentComparisons();
    }
  } catch (error) {
    if (operation === state.saveGeneration) {
      refs.saveStatus.textContent = `保存 ${snapshot.pair.map(shortRunId).join(' → ')} 失败：${error.message || '请重试'}`;
      refs.saveStatus.classList.remove('hidden');
    }
  } finally {
    state.pendingSaves.delete(snapshot.key); renderSaveComparison();
  }
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
refs.saveComparison.addEventListener('click', saveCurrentComparison);
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
refs.assessmentRunSelect.addEventListener('change', () => {
  state.assessment.runId = refs.assessmentRunSelect.value || null;
  state.assessment.curveRunId = null;
  renderAssessmentWorkspace();
});
refs.assessmentCurveRunSelect.addEventListener('change', () => {
  state.assessment.curveRunId = refs.assessmentCurveRunSelect.value || null;
  renderAssessmentWorkspace();
});
globalThis.addEventListener('resize', () => {
  if (state.workspace !== 'assessment' && view()?.payload) renderChart();
});

restoreSessionState();
getAccount().then((account) => { state.canExport = Boolean(account.permissions?.includes('results.export')); state.canSaveComparison = Boolean(account.permissions?.includes('results.comparison.write')); render(); }).catch(() => {});
loadInitial();
