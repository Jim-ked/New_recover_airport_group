import { apiFetch, ApiError } from './api-client.js';
import { formatRunIdentity } from '../utils/run-identity.js';
import { formatDecimal, formatHhi, formatInteger, formatPercent, formatSeconds } from './number-display.js';

const PREFERENCE_LABELS = { sortie_max: '出动架次优先', resource_min: '资源消耗优先', time_min: '时间代价优先', custom: '自定义权重' };
const SOLVER_STATUS_LABELS = { optimal: '最优', gaplimit: '达到 Gap 限制', timelimit: '达到时间限制', infeasible: '不可行', unbounded: '无界', unknown: '状态未记录' };
const SVG_NS = 'http://www.w3.org/2000/svg';
const page = document.getElementById('singleRunPage');
const state = {
  runId: page?.dataset.runId || '', run: null, runConfig: null, situation: null, solution: null, metrics: null,
  airportById: new Map(), missionById: new Map(), timelineMode: 'all', timelineObjectId: null,
  supportAirportId: null, supportResourceId: null, supportAircraftTypeId: null, supportView: 'aircraft', airportView: 'acceptance', timelineView: 'events', selectedWindowIndex: null,
  timelineEventMode: 'total', timelineDirection: 'departures', timelineWindowStart: 0, timelineWindowSize: null, recordView: 'facts',
};
const $ = (id) => document.getElementById(id);
const refs = {
  message: $('singleRunMessage'), loading: $('singleRunLoading'), content: $('singleRunContent'), runBadges: $('runBadges'),
  constraintStatus: $('constraintStatus'), constraintStatusIcon: $('constraintStatusIcon'), constraintStatusLabel: $('constraintStatusLabel'), constraintStatusDetails: $('constraintStatusDetails'),
  openRuntimeButton: $('openRuntimeButton'), openRunRecordButton: $('openRunRecordButton'), closeRunRecordButton: $('closeRunRecordButton'), runRecordDialog: $('runRecordDialog'), clusterSummary: $('clusterSummary'), solverFacts: $('solverFacts'),
  taskMatrix: $('taskFulfillmentMatrix'), timelineModes: $('timelineModes'), timelineObjectSelect: $('timelineObjectSelect'), timelineChart: $('timelineChart'),
  timelineEventModes: $('timelineEventModes'), timelineDirections: $('timelineDirections'), timelineWindowNav: $('timelineWindowNav'), timelineWindowRange: $('timelineWindowRange'),
  timelineContext: $('timelineContext'), timelineSummary: $('timelineSummary'), timelineLegend: $('timelineLegend'), airportViews: $('airportViews'), airportAcceptanceList: $('airportAcceptanceList'), airportCollaborationSummary: $('airportCollaborationSummary'), taskDistributionRows: $('taskDistributionRows'), aircraftLegend: $('aircraftLegend'),
  chainTableBody: $('chainTableBody'), supportAirportSelect: $('supportAirportSelect'), supportResourceSelect: $('supportResourceSelect'),
  supportAircraftSelect: $('supportAircraftSelect'), capacityHeatmap: $('capacityHeatmap'), resourceInventoryChart: $('resourceInventoryChart'),
  aircraftTurnaroundChart: $('aircraftTurnaroundChart'), capacitySubtitle: $('capacitySubtitle'), resourceSubtitle: $('resourceSubtitle'), aircraftSubtitle: $('aircraftSubtitle'),
  aircraftMiniChart: $('aircraftMiniChart'), resourceMiniChart: $('resourceMiniChart'), aircraftSummary: $('aircraftSummary'), resourceSummary: $('resourceSummary'), supportAirportLabel: $('supportAirportLabel'),
  supportViews: $('supportViews'), supportSecondarySummary: $('supportSecondarySummary'), aircraftSupportPanel: $('aircraftSupportPanel'), resourceSupportPanel: $('resourceSupportPanel'),
  timelineViews: $('timelineViews'), openResourceDetailButton: $('openResourceDetailButton'), resourceDetailDialog: $('resourceDetailDialog'), closeResourceDetailButton: $('closeResourceDetailButton'), resourceDetailAirportSelect: $('resourceDetailAirportSelect'), resourceDetailResourceSelect: $('resourceDetailResourceSelect'), resourceDetailChart: $('resourceDetailChart'), resourceDetailSummary: $('resourceDetailSummary'),
  runRecordTabs: $('runRecordTabs'), runSpatialView: $('runSpatialView'), runSpatialChart: $('runSpatialChart'), recordFactsPanel: document.querySelector('[data-record-panel="facts"]'),
};

function text(tag, value, className = '') { const el = document.createElement(tag); el.textContent = value ?? '—'; if (className) el.className = className; return el; }
function svgElement(name, attrs = {}) { const el = document.createElementNS(SVG_NS, name); for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value)); return el; }
function integer(value) { return formatInteger(value); }
function percent(value, digits = 1) { return formatPercent(value, { digits }); }
function number(value, digits = 2) { return formatDecimal(value, { minimumFractionDigits: digits, maximumFractionDigits: digits }); }
function windowLabel(value) { return Number.isInteger(value) ? `T${value}` : '—'; }
function recorded(value, formatter = String) { return value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value)) ? '未记录' : formatter(value); }
function airportName(id) { return state.airportById.get(id)?.airport_name || id || '—'; }
function airportDisplay(id) { const match = /^AP(\d+)$/i.exec(String(id || '')); const prefix = match ? `${match[1].padStart(3, '0')} ` : ''; return `${prefix}${airportName(id).replace(/\s+(?:International\s+|General\s+)?(?:Airport|Air Base)$/i, '').trim() || id || '—'}`; }
function missionName(id) { return state.missionById.get(id)?.name || id || '—'; }
function resourceLabel(id) { const row = state.metrics?.resources?.resource_types?.[id] || {}; return `${row.name || id}${row.unit ? `（${row.unit}）` : ''}`; }
function showError(error) { console.error(error); refs.message.textContent = error instanceof ApiError ? error.message : '加载单次运行结果时发生未预期错误'; refs.message.classList.remove('hidden'); refs.loading.classList.add('hidden'); refs.content.classList.add('hidden'); }

function buildIndexes() {
  state.airportById.clear(); state.missionById.clear();
  for (const item of state.situation?.airports || []) { const airport = item.airport || {}; if (airport.airport_id) state.airportById.set(airport.airport_id, airport); }
  for (const mission of state.situation?.missions || []) if (mission.mission_id) state.missionById.set(mission.mission_id, mission);
}
function renderBadges() {
  const identity = formatRunIdentity({ ...state.run, run_config: state.runConfig, situation: state.situation });
  const title = text('div', '', 'single-context'); title.append(text('b', identity.title, 'single-damage-status'));
  const subtitle = text('div', identity.subtitle, 'single-cluster-meta');
  const trace = text('div', identity.short_id, 'single-run-id'); trace.title = identity.detail;
  const status = text('span', '', 'single-run-status'); status.title = 'Run 已成功完成'; status.append(text('i', '', 'status-dot'), document.createTextNode('成功'));
  const label = text('div', '', 'run-label'); label.title = identity.detail; label.append(title, subtitle, trace);
  refs.runBadges.replaceChildren(label, status);
}
function constraintDetailFacts(run, metrics) {
  const summary = metrics?.summary || {}, technical = metrics?.technical || {};
  const rows = [];
  const basisLabels = {
    feasible_solution: '当前方案为可行解',
    solver_proven_infeasible: '求解器已明确证明不可行',
    no_feasibility_conclusion: '当前没有可行性结论',
  };
  const basis = basisLabels[run?.constraint_status?.basis];
  if (basis) rows.push(['依据', basis]);
  if (Number.isFinite(summary.fulfilled_sorties_total) && Number.isFinite(summary.required_sorties_total)) {
    rows.push(['任务完成', `${integer(summary.fulfilled_sorties_total)} / ${integer(summary.required_sorties_total)}`]);
  }
  if (Number.isFinite(summary.unmet_sorties_total)) rows.push(['任务缺口', integer(summary.unmet_sorties_total)]);
  if (typeof technical.solver_status === 'string' && technical.solver_status) {
    rows.push(['终止原因', SOLVER_STATUS_LABELS[technical.solver_status] || technical.solver_status]);
  }
  if (Number.isFinite(technical.gap)) rows.push(['Gap', percent(technical.gap, 2)]);
  return rows;
}
function renderConstraintStatus() {
  const status = state.run.constraint_status || { state: 'unavailable', label: '未判定' };
  const icons = { satisfied: '✓', unsatisfied: '×', unavailable: '—' };
  const visualState = Object.hasOwn(icons, status.state) ? status.state : 'unavailable';
  refs.constraintStatus.dataset.state = visualState;
  refs.constraintStatusIcon.textContent = icons[visualState];
  refs.constraintStatusLabel.textContent = status.label || '未判定';
  refs.constraintStatusDetails.replaceChildren(...constraintDetailFacts(state.run, state.metrics).map(([label, value]) => {
    const row = document.createElement('p');
    row.append(text('span', label), text('strong', value));
    return row;
  }));
  refs.constraintStatus.classList.remove('hidden');
}
function metricCard(label, value, note = '') {
  const article = document.createElement('article'); article.className = 'overview-metric';
  article.append(text('span', label), text('strong', value), text('small', note)); return article;
}
function renderOverview() {
  const summary = state.metrics.summary || {}, technical = state.metrics.technical || {};
  const collaboration = state.metrics.collaboration || {};
  const selected = (collaboration.selected_cluster || []).map(airportDisplay);
  const participating = (collaboration.participating_airports || []).map(airportDisplay);
  refs.clusterSummary.replaceChildren(
    text('p', `需求 ${integer(summary.required_sorties_total)} · 履行 ${integer(summary.fulfilled_sorties_total)} · 缺口 ${integer(summary.unmet_sorties_total)} · 额外 ${integer(summary.additional_sorties_total)}`),
    text('p', `组选：${selected.join('、') || '未启用或未形成组选'}`),
    text('p', `实际参与（${integer(summary.participating_airport_count)}）：${participating.join('、') || '无参与机场'}`),
    text('p', `出动集中度 HHI：${formatHhi(collaboration.departure_hhi)} · 跨机场返航：${percent(collaboration.cross_return_ratio)}`),
  );
  const facts = [
    ['终止状态', recorded(technical.solver_status, (value) => SOLVER_STATUS_LABELS[value] || value)],
    ['可行目标（下界）', recorded(technical.objective, (value) => number(value, 4))],
    ['对偶界（上界）', recorded(technical.best_bound, (value) => number(value, 4))],
    ['相对 Gap', recorded(technical.gap, (value) => percent(value, 2))],
    ['MIP 求解时间', recorded(technical.solve_time_s, (value) => formatSeconds(value))],
    ['组选 LP 目标', recorded(technical.cluster_lp_objective, (value) => number(value, 4))],
    ['F1', recorded(technical.f1, (value) => number(value, 4))], ['F2', recorded(technical.f2, (value) => number(value, 4))], ['F3', recorded(technical.f3, (value) => number(value, 4))],
  ];
  refs.solverFacts.replaceChildren(...facts.map(([label, value]) => { const row = document.createElement('div'); row.append(text('span', label), text('strong', value)); return row; }));
}

function renderTaskFulfillmentMatrix() {
  const tasks = state.metrics.tasks || {};
  const aircraftTypes = [...new Set(Object.values(tasks).flatMap((row) => Object.keys(row.required_by_aircraft || {}).concat(Object.keys(row.scheduled_by_aircraft || {}))))].sort();
  if (!Object.keys(tasks).length || !aircraftTypes.length) { refs.taskMatrix.replaceChildren(text('div', '没有任务×机型结果', 'chart-empty')); return; }
  const table = document.createElement('table'); table.className = 'data-table task-matrix';
  const thead = document.createElement('thead'); const hr = document.createElement('tr');
  ['任务', ...aircraftTypes, '承接机场'].forEach((label) => hr.append(text('th', label))); thead.append(hr); table.append(thead);
  const tbody = document.createElement('tbody');
  for (const [missionId, row] of Object.entries(tasks).sort((a, b) => a[0].localeCompare(b[0]))) {
    const tr = document.createElement('tr'); tr.append(text('th', missionName(missionId)));
    for (const aircraftType of aircraftTypes) {
      const required = Number(row.required_by_aircraft?.[aircraftType] || 0), fulfilled = Number(row.fulfilled_by_aircraft?.[aircraftType] || 0), unmet = Number(row.unmet_by_aircraft?.[aircraftType] || 0), additional = Number(row.additional_by_aircraft?.[aircraftType] || 0);
      const cell = document.createElement('td'); cell.className = `fulfillment-cell${unmet > 0 ? ' has-shortfall' : additional > 0 ? ' has-additional' : ''}`;
      cell.style.setProperty('--fulfillment', String(required > 0 ? Math.min(1, fulfilled / required) : 0));
      cell.append(text('strong', `${integer(fulfilled)} / ${integer(required)}`), text('small', unmet > 0 ? `缺口 ${integer(unmet)}` : additional > 0 ? `额外 ${integer(additional)}` : required > 0 ? '已满足' : '不适用'));
      tr.append(cell);
    }
    const origins = Object.entries(row.by_origin_airport || {}).map(([airportId, sorties]) => `${airportDisplay(airportId)} ${integer(sorties)}`).join('、') || '—'; tr.append(text('td', origins)); tbody.append(tr);
  }
  table.append(tbody); refs.taskMatrix.replaceChildren(table);
}

function pathFromSeries(values, width, height, maxValue, pad = 28) {
  if (!values.length) return ''; const xSpan = width - pad * 2, ySpan = height - pad * 2; let started = false; const parts = [];
  values.forEach((value, index) => { if (typeof value !== 'number' || !Number.isFinite(value)) { started = false; return; } const x = pad + (values.length === 1 ? xSpan / 2 : index / (values.length - 1) * xSpan); const y = height - pad - value / Math.max(1e-12, maxValue) * ySpan; parts.push(`${started ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`); started = true; }); return parts.join(' ');
}
function validateTimelineSeries(windows, series) {
  if (!Array.isArray(windows) || !Array.isArray(series)) return { ok: false, message: '时序数据无效。' }; const checked = [];
  for (const row of series) { if (!Array.isArray(row?.values)) return { ok: false, message: '时序数据无效。' }; if (row.values.length !== windows.length) return { ok: false, message: '时序长度与时间窗不一致。' }; const values = []; for (const value of row.values) { if (value === null) { values.push(null); continue; } if (typeof value !== 'number' || !Number.isFinite(value)) return { ok: false, message: '时序数据无效。' }; values.push(value); } checked.push({ ...row, values }); }
  return { ok: true, series: checked };
}
function niceAxisMaximum(value) {
  const raw = Math.max(0, Number(value) || 0) * 1.08; if (raw <= 0) return 1;
  const step = raw < 4 ? Math.ceil(raw * 10 / 4) / 10 : Math.ceil(raw / 4); return Math.max(1, step * 4);
}
function timelineWindowStep() { return Math.max(1, Math.floor((state.timelineWindowSize || 12) * .75)); }
function visibleTimelineRange() {
  const windows = state.metrics?.time_axis?.windows || [], width = refs.timelineChart?.clientWidth || 840;
  const size = Math.min(windows.length, Math.max(12, Math.floor((width - 70) / 15)));
  state.timelineWindowSize = size; state.timelineWindowStart = Math.max(0, Math.min(state.timelineWindowStart, Math.max(0, windows.length - size)));
  return { start: state.timelineWindowStart, end: state.timelineWindowStart + size, windows: windows.slice(state.timelineWindowStart, state.timelineWindowStart + size), total: windows.length };
}
function renderTimelineWindowNavigation(range) {
  const buttons = refs.timelineWindowNav.querySelectorAll('button[data-window-nav]'); buttons[0].disabled = range.start <= 0; buttons[1].disabled = range.end >= range.total;
  refs.timelineWindowRange.textContent = range.total ? `${windowLabel(range.windows[0])}–${windowLabel(range.windows.at(-1))} / ${range.total}窗（15分钟）` : '无时间窗';
  refs.timelineWindowNav.classList.remove('hidden');
  refs.taskDistributionRows.querySelectorAll('[data-window-index]').forEach((cell) => { const index = Number(cell.dataset.windowIndex); cell.classList.toggle('timeline-window-hidden', index < range.start || index >= range.end); });
}
function aggregateAircraftTimeline(direction) {
  const windows = state.metrics?.time_axis?.windows || [], indexByWindow = new Map(windows.map((value, index) => [Number(value), index])), rows = new Map();
  for (const chain of state.solution.sortie_chains || []) {
    if (state.timelineMode === 'airport' && (direction === 'departures' ? chain.origin_airport_id : chain.return_airport_id) !== state.timelineObjectId) continue;
    if (state.timelineMode === 'mission' && chain.mission_id !== state.timelineObjectId) continue;
    if (state.timelineMode === 'aircraft' && chain.aircraft_type !== state.timelineObjectId) continue;
    const eventWindow = direction === 'departures' ? chain.depart_window : chain.return_window; if (eventWindow === null || eventWindow === undefined) continue; const index = indexByWindow.get(Number(eventWindow)); if (index === undefined) continue;
    if (!rows.has(chain.aircraft_type)) rows.set(chain.aircraft_type, Array(windows.length).fill(0)); rows.get(chain.aircraft_type)[index] += Number(chain.sorties || 0);
  }
  return [...rows.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([aircraftType, values]) => ({ label: aircraftType, values, fill: aircraftColor(aircraftType) }));
}
function validateCompositionTotals(series, totals) {
  if (!Array.isArray(totals) || series.some((row) => !Array.isArray(row.values) || row.values.length !== totals.length)) return false;
  return totals.every((value, index) => Number.isFinite(value) && Math.abs(series.reduce((sum, row) => sum + Number(row.values[index] || 0), 0) - value) < 1e-9);
}
function nearestWindowIndex(clientX, rect, count, pad, width) { if (count <= 1) return 0; const svgX = (clientX - rect.left) / Math.max(1, rect.width) * width; return Math.round(Math.max(0, Math.min(1, (svgX - pad) / Math.max(1, width - pad * 2))) * (count - 1)); }
function setTimelineFocus(index) {
  state.selectedWindowIndex = Number.isInteger(index) ? index : null;
  refs.taskDistributionRows.querySelectorAll('[data-window-index]').forEach((cell) => cell.classList.toggle('focused', Number(cell.dataset.windowIndex) === state.selectedWindowIndex));
}
function renderGroupedBarChart(container, series, options) {
  options ||= {}; const { contextLabel = null, windows = state.metrics.time_axis.windows, windowOffset = 0 } = options; container.replaceChildren(); const checked = validateTimelineSeries(windows, series);
  if (!checked.ok) { container.append(text('div', checked.message, 'chart-empty chart-error')); return; }
  const values = checked.series.flatMap((row) => row.values.filter(Number.isFinite));
  if (!values.length) { container.append(text('div', '当前对象没有可展示的逐窗数据', 'chart-empty')); return; }
  const width = 840, height = 250, left = 38, right = 12, top = 16, bottom = 30, plotWidth = width - left - right, plotHeight = height - top - bottom;
  const maxValue = niceAxisMaximum(Math.max(1, ...values)), groupWidth = plotWidth / Math.max(1, windows.length), barWidth = Math.max(1, Math.min(10, groupWidth * .34));
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'xMidYMid meet' });
  for (let index = 0; index <= 4; index += 1) { const y = top + plotHeight / 4 * index; svg.append(svgElement('line', { x1: left, x2: width - right, y1: y, y2: y, class: 'chart-grid-line' })); const tick = svgElement('text', { x: left - 6, y: y + 3, 'text-anchor': 'end', class: 'chart-axis-label' }); tick.textContent = integer(maxValue * (4 - index) / 4); svg.append(tick); }
  checked.series.forEach((row, seriesIndex) => row.values.forEach((value, index) => { if (!Number.isFinite(value)) return; const h = value / maxValue * plotHeight, groupX = left + groupWidth * index + groupWidth / 2; const x = groupX + (seriesIndex === 0 ? -barWidth - 1 : 1); const bar = svgElement('rect', { x, y: top + plotHeight - h, width: barWidth, height: Math.max(0, h), rx: 1, fill: row.fill, class: 'timeline-bar' }); const title = svgElement('title'); title.textContent = `${contextLabel || '总体'} · ${windowLabel(windows[index])} · ${row.label} ${integer(value)}`; bar.append(title); svg.append(bar); }));
  const labelStep = Math.max(1, Math.ceil(windows.length / 9)); windows.forEach((window, index) => { if (index % labelStep && index !== windows.length - 1) return; const label = svgElement('text', { x: left + groupWidth * (index + .5), y: height - 8, 'text-anchor': 'middle', class: 'chart-axis-label' }); label.textContent = windowLabel(window); svg.append(label); });
  const hover = svgElement('line', { class: 'chart-hover-line', y1: top, y2: top + plotHeight, visibility: 'hidden' }); svg.append(hover); const tooltip = text('div', '', 'chart-hover-tooltip'); tooltip.hidden = true;
  svg.addEventListener('pointermove', (event) => { const rect = svg.getBoundingClientRect(), index = nearestWindowIndex(event.clientX, rect, windows.length, left, width), x = left + groupWidth * (index + .5); setTimelineFocus(index + windowOffset); hover.setAttribute('x1', x); hover.setAttribute('x2', x); hover.setAttribute('visibility', 'visible'); tooltip.textContent = [contextLabel, windowLabel(windows[index]), ...checked.series.map((row) => `${row.label} ${row.values[index] === null ? '不适用' : integer(row.values[index])}`)].filter(Boolean).join('\n'); tooltip.hidden = false; tooltip.style.left = `${Math.min(Math.max(8, event.clientX - container.getBoundingClientRect().left + 10), Math.max(8, container.clientWidth - 160))}px`; tooltip.style.top = '18px'; });
  svg.addEventListener('pointerleave', () => { setTimelineFocus(null); hover.setAttribute('visibility', 'hidden'); tooltip.hidden = true; }); container.append(svg, tooltip);
}
function renderStackedBarChart(container, series, { contextLabel = null, windows, windowOffset = 0 } = {}) {
  container.replaceChildren(); const checked = validateTimelineSeries(windows, series); if (!checked.ok) { container.append(text('div', checked.message, 'chart-empty chart-error')); return; }
  const totals = windows.map((_, index) => checked.series.reduce((sum, row) => sum + Number(row.values[index] || 0), 0)); if (!totals.length) { container.append(text('div', '当前对象没有可展示的逐窗数据', 'chart-empty')); return; }
  const width = 840, height = 250, left = 38, right = 12, top = 16, bottom = 30, plotWidth = width - left - right, plotHeight = height - top - bottom, maxValue = niceAxisMaximum(Math.max(1, ...totals)), group = plotWidth / Math.max(1, windows.length);
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'xMidYMid meet' });
  for (let index = 0; index <= 4; index += 1) { const y = top + plotHeight / 4 * index; svg.append(svgElement('line', { x1: left, x2: width - right, y1: y, y2: y, class: 'chart-grid-line' })); const tick = svgElement('text', { x: left - 6, y: y + 3, 'text-anchor': 'end', class: 'chart-axis-label' }); tick.textContent = integer(maxValue * (4 - index) / 4); svg.append(tick); }
  windows.forEach((window, index) => { let cumulative = 0; for (const row of checked.series) { const value = row.values[index], h = Number(value || 0) / maxValue * plotHeight; const bar = svgElement('rect', { x: left + group * index + group * .16, y: top + plotHeight - cumulative - h, width: Math.max(1, group * .68), height: h, fill: row.fill }); cumulative += h; const title = svgElement('title'); title.textContent = `${contextLabel || '总体'} · ${windowLabel(window)} · ${row.label} ${integer(value)}`; bar.append(title); svg.append(bar); } });
  const labelStep = Math.max(1, Math.ceil(windows.length / 9)); windows.forEach((window, index) => { if (index % labelStep && index !== windows.length - 1) return; const label = svgElement('text', { x: left + group * (index + .5), y: height - 8, 'text-anchor': 'middle', class: 'chart-axis-label' }); label.textContent = windowLabel(window); svg.append(label); });
  svg.addEventListener('pointermove', (event) => setTimelineFocus(nearestWindowIndex(event.clientX, svg.getBoundingClientRect(), windows.length, left, width) + windowOffset)); svg.addEventListener('pointerleave', () => setTimelineFocus(null)); container.append(svg);
}
function renderLineChart(container, series, { contextLabel = null, valueFormatter = integer } = {}) {
  container.replaceChildren(); const windows = state.metrics.time_axis.windows; const checked = validateTimelineSeries(windows, series);
  if (!checked.ok) { container.append(text('div', checked.message, 'chart-empty chart-error')); return; } series = checked.series;
  const values = series.flatMap((row) => row.values.filter((value) => typeof value === 'number' && Number.isFinite(value))); if (!values.length) { container.append(text('div', '当前对象没有可展示的逐窗数据', 'chart-empty')); return; }
  const width = 760, height = 245, pad = 28, maxValue = niceAxisMaximum(Math.max(1, ...values)); const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'xMidYMid meet' });
  for (let index = 0; index <= 4; index += 1) { const y = pad + (height - pad * 2) / 4 * index; svg.append(svgElement('line', { x1: pad, x2: width - pad, y1: y, y2: y, class: 'chart-grid-line' })); }
  series.forEach((row) => svg.append(svgElement('path', { d: pathFromSeries(row.values, width, height, maxValue, pad), fill: 'none', stroke: row.stroke, 'stroke-width': 2.4, 'vector-effect': 'non-scaling-stroke' })));
  const hover = svgElement('line', { class: 'chart-hover-line', y1: pad, y2: height - pad, visibility: 'hidden' }); svg.append(hover); const tooltip = text('div', '', 'chart-hover-tooltip'); tooltip.hidden = true;
  const move = (event) => { const rect = svg.getBoundingClientRect(), index = nearestWindowIndex(event.clientX, rect, windows.length, pad, width), x = pad + (windows.length === 1 ? (width - 2 * pad) / 2 : index / (windows.length - 1) * (width - 2 * pad)); hover.setAttribute('x1', x); hover.setAttribute('x2', x); hover.setAttribute('visibility', 'visible'); tooltip.textContent = [contextLabel, windowLabel(windows[index]), ...series.map((row) => `${row.label} ${row.values[index] === null ? '不适用' : valueFormatter(row.values[index])}`)].filter(Boolean).join('\n'); tooltip.hidden = false; tooltip.style.left = `${Math.min(Math.max(8, event.clientX - container.getBoundingClientRect().left + 10), Math.max(8, container.clientWidth - 180))}px`; tooltip.style.top = '18px'; };
  svg.addEventListener('pointermove', move); svg.addEventListener('pointerleave', () => { hover.setAttribute('visibility', 'hidden'); tooltip.hidden = true; }); container.append(svg, tooltip);
}
function timelineKeys(mode) { const source = mode === 'airport' ? state.metrics.timeline.by_airport : mode === 'mission' ? state.metrics.timeline.by_mission : mode === 'aircraft' ? state.metrics.timeline.by_aircraft : {}; return Object.keys(source || {}).sort(); }
function timelineLabel(mode, id) { return mode === 'airport' ? airportDisplay(id) : mode === 'mission' ? missionName(id) : id; }
function phaseOccupancyBlock() {
  const facts = state.metrics.timeline?.phase_occupancy; if (!facts?.verified) return null;
  if (state.timelineMode === 'all') return facts.all || null;
  const source = state.timelineMode === 'airport' ? facts.by_airport : state.timelineMode === 'mission' ? facts.by_mission : facts.by_aircraft; return source?.[state.timelineObjectId] || null;
}
function updatePhaseAvailability() {
  const button = refs.timelineViews.querySelector('[data-view="phases"]'), facts = state.metrics?.timeline?.phase_occupancy, available = Boolean(facts?.verified && facts.all);
  button.disabled = !available; button.title = available ? '查看经正式记录的四阶段逐窗占用' : '过程事实未记录';
  if (!available && state.timelineView === 'phases') state.timelineView = 'events';
}
function renderPhaseChart(container, block, contextLabel, range) {
  const windows = range.windows, definitions = [['departure', '出动阶段', '#49a5e9'], ['mission', '任务作业', '#8d7be8'], ['return', '返航阶段', '#55b99a'], ['turnaround', '整备', '#e5a449']];
  const checked = validateTimelineSeries(windows, definitions.map(([field, label, fill]) => ({ field, label, fill, values: Array.isArray(block?.[field]) ? block[field].slice(range.start, range.end) : block?.[field] })));
  if (!checked.ok) { container.replaceChildren(text('div', checked.message, 'chart-empty chart-error')); return; }
  const totals = windows.map((_, index) => checked.series.reduce((sum, row) => sum + row.values[index], 0)), maxValue = niceAxisMaximum(Math.max(1, ...totals)), width = 840, height = 250, left = 38, right = 12, top = 16, bottom = 30, plotWidth = width - left - right, plotHeight = height - top - bottom, group = plotWidth / Math.max(1, windows.length); const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'xMidYMid meet' });
  for (let index = 0; index <= 4; index += 1) { const y = top + plotHeight / 4 * index; svg.append(svgElement('line', { x1: left, x2: width - right, y1: y, y2: y, class: 'chart-grid-line' })); const tick = svgElement('text', { x: left - 6, y: y + 3, 'text-anchor': 'end', class: 'chart-axis-label' }); tick.textContent = integer(maxValue * (4 - index) / 4); svg.append(tick); }
  windows.forEach((window, index) => { let cumulative = 0; checked.series.forEach((row) => { const value = row.values[index], h = value / maxValue * plotHeight, rect = svgElement('rect', { x: left + group * index + group * .12, y: top + plotHeight - cumulative - h, width: Math.max(1, group * .76), height: h, fill: row.fill }); cumulative += h; const title = svgElement('title'); title.textContent = `${contextLabel} · ${windowLabel(window)} · ${row.label} ${integer(value)}`; rect.append(title); svg.append(rect); }); }); container.replaceChildren(svg);
  const labelStep = Math.max(1, Math.ceil(windows.length / 9)); windows.forEach((window, index) => { if (index % labelStep && index !== windows.length - 1) return; const label = svgElement('text', { x: left + group * (index + .5), y: height - 8, 'text-anchor': 'middle', class: 'chart-axis-label' }); label.textContent = windowLabel(window); svg.append(label); });
}
function legendItem(label, className) { const item = document.createElement('span'); item.append(text('i', '', className), document.createTextNode(label)); return item; }
function renderTimeline() {
  [...refs.timelineModes.querySelectorAll('button')].forEach((button) => button.classList.toggle('active', button.dataset.mode === state.timelineMode)); const ids = timelineKeys(state.timelineMode); const needsObject = state.timelineMode !== 'all'; refs.timelineObjectSelect.classList.toggle('hidden', !needsObject); refs.timelineObjectSelect.replaceChildren();
  if (needsObject) { if (!ids.includes(state.timelineObjectId)) state.timelineObjectId = ids[0] || null; for (const id of ids) { const option = text('option', timelineLabel(state.timelineMode, id)); option.value = id; option.selected = id === state.timelineObjectId; refs.timelineObjectSelect.append(option); } } else state.timelineObjectId = null;
  const timeline = state.metrics.timeline || {}; const block = state.timelineMode === 'all' ? { departures: timeline.departures_total, returns: timeline.returns_total } : (state.timelineMode === 'airport' ? timeline.by_airport : state.timelineMode === 'mission' ? timeline.by_mission : timeline.by_aircraft)?.[state.timelineObjectId];
  if (!block) { refs.timelineChart.replaceChildren(text('div', '当前维度没有对象', 'chart-empty')); refs.timelineSummary.replaceChildren(); return; }
  const label = state.timelineMode === 'all' ? '总体' : timelineLabel(state.timelineMode, state.timelineObjectId); refs.timelineContext.textContent = label; const range = visibleTimelineRange();
  [...refs.timelineViews.querySelectorAll('button')].forEach((button) => button.classList.toggle('active', button.dataset.view === state.timelineView));
  const eventsVisible = state.timelineView === 'events'; refs.timelineEventModes.classList.toggle('hidden', !eventsVisible); const compositionVisible = eventsVisible && state.timelineEventMode === 'composition'; refs.timelineDirections.classList.toggle('hidden', !compositionVisible);
  [...refs.timelineEventModes.querySelectorAll('button')].forEach((button) => button.classList.toggle('active', button.dataset.eventMode === state.timelineEventMode)); [...refs.timelineDirections.querySelectorAll('button')].forEach((button) => button.classList.toggle('active', button.dataset.direction === state.timelineDirection));
  if (state.timelineView === 'phases') { renderPhaseChart(refs.timelineChart, phaseOccupancyBlock(), label, range); refs.timelineLegend.replaceChildren(legendItem('出动阶段', 'legend-phase-departure'), legendItem('任务作业', 'legend-phase-mission'), legendItem('返航阶段', 'legend-phase-return'), legendItem('整备', 'legend-phase-turnaround')); }
  else if (state.timelineEventMode === 'composition') {
    const fullSeries = aggregateAircraftTimeline(state.timelineDirection), totals = block[state.timelineDirection] || [];
    if (!validateCompositionTotals(fullSeries, totals)) refs.timelineChart.replaceChildren(text('div', '逐窗机型构成与正式总量不一致，已停止绘图。', 'chart-empty chart-error'));
    else { const visibleSeries = fullSeries.map((row) => ({ ...row, values: row.values.slice(range.start, range.end) })); renderStackedBarChart(refs.timelineChart, visibleSeries, { contextLabel: `${label} · ${state.timelineDirection === 'departures' ? '出动' : '返航'}`, windows: range.windows, windowOffset: range.start }); }
    refs.timelineLegend.replaceChildren(...fullSeries.map((row) => { const item = legendItem(row.label, ''); item.querySelector('i').style.background = row.fill; return item; }));
  } else {
    renderGroupedBarChart(refs.timelineChart, [{ label: '出动', values: (block.departures || []).slice(range.start, range.end), fill: '#49a5e9' }, { label: '返航', values: (block.returns || []).slice(range.start, range.end), fill: '#68c88a' }], { contextLabel: label, windows: range.windows, windowOffset: range.start }); refs.timelineLegend.replaceChildren(legendItem('出动', 'legend-departure'), legendItem('返航', 'legend-return'));
  }
  renderTimelineWindowNavigation(range);
  renderTimelineSummary(block);
}

function summaryItem(label, value, note = '') { const item = document.createElement('div'); item.append(text('span', label), text('strong', value), text('small', note)); return item; }
function renderTimelineSummary(block) {
  const departures = Array.isArray(block.departures) ? block.departures : [], returns = Array.isArray(block.returns) ? block.returns : [], windows = state.metrics.time_axis.windows || [];
  const totalDepartures = departures.every(Number.isFinite) ? departures.reduce((sum, value) => sum + value, 0) : null;
  const totalReturns = returns.every(Number.isFinite) ? returns.reduce((sum, value) => sum + value, 0) : null;
  const peak = departures.length ? Math.max(...departures.filter(Number.isFinite)) : null, peakIndex = Number.isFinite(peak) ? departures.indexOf(peak) : -1;
  let middleLabel = '累计返航', middleValue = recorded(totalReturns, integer), middleNote = '';
  if (state.timelineMode === 'all') { const summary = state.metrics.summary || {}; middleLabel = '需求履行'; middleValue = `${integer(summary.fulfilled_sorties_total)} / ${integer(summary.required_sorties_total)}`; middleNote = `额外 ${integer(summary.additional_sorties_total)} · 缺口 ${integer(summary.unmet_sorties_total)}`; }
  if (state.timelineMode === 'mission') { const task = state.metrics.tasks?.[state.timelineObjectId] || {}; const sum = (obj) => Object.values(obj || {}).reduce((total, value) => total + Number(value || 0), 0); middleLabel = '需求履行'; middleValue = `${integer(sum(task.fulfilled_by_aircraft))} / ${integer(sum(task.required_by_aircraft))}`; middleNote = `额外 ${integer(sum(task.additional_by_aircraft))} · 缺口 ${integer(sum(task.unmet_by_aircraft))}`; }
  refs.timelineSummary.replaceChildren(summaryItem(state.timelineMode === 'airport' ? '机场出动' : state.timelineMode === 'mission' ? '任务出动' : state.timelineMode === 'aircraft' ? '机型出动' : '累计出动', recorded(totalDepartures, integer), labelForSummary()), summaryItem(middleLabel, middleValue, middleNote), summaryItem('出动峰值', recorded(peak, integer), peakIndex >= 0 ? `${windowLabel(windows[peakIndex])} · 原生时间窗` : '未记录'));
}
function labelForSummary() { return state.timelineMode === 'all' ? '当前 Run' : timelineLabel(state.timelineMode, state.timelineObjectId); }
function renderRunSpatialView() {
  const airports = [...state.airportById.entries()].filter(([, row]) => Number.isFinite(Number(row.longitude)) && Number.isFinite(Number(row.latitude)));
  const missions = [...state.missionById.entries()].filter(([, row]) => Number.isFinite(Number(row.longitude)) && Number.isFinite(Number(row.latitude)));
  const points = [...airports.map(([id, row]) => ({ id, kind: 'airport', longitude: Number(row.longitude), latitude: Number(row.latitude) })), ...missions.map(([id, row]) => ({ id, kind: 'mission', longitude: Number(row.longitude), latitude: Number(row.latitude) }))];
  refs.runSpatialChart.replaceChildren(); if (!points.length) { refs.runSpatialChart.append(text('div', '冻结情境未记录可展示的位置', 'chart-empty')); return; }
  const width = 960, height = 560, pad = 48, longitudes = points.map((row) => row.longitude), latitudes = points.map((row) => row.latitude), minLon = Math.min(...longitudes), maxLon = Math.max(...longitudes), minLat = Math.min(...latitudes), maxLat = Math.max(...latitudes);
  const project = (longitude, latitude) => ({ x: pad + (longitude - minLon) / Math.max(1e-9, maxLon - minLon) * (width - 2 * pad), y: height - pad - (latitude - minLat) / Math.max(1e-9, maxLat - minLat) * (height - 2 * pad) }); const positions = new Map(points.map((row) => [row.id, project(row.longitude, row.latitude)]));
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'xMidYMid meet' }), aggregates = new Map();
  for (const chain of state.solution.sortie_chains || []) { const key = `${chain.origin_airport_id}\u0000${chain.mission_id}\u0000${chain.return_airport_id}`; aggregates.set(key, (aggregates.get(key) || 0) + Number(chain.sorties || 0)); }
  for (const [key, sorties] of aggregates) { const [originId, missionId, returnId] = key.split('\u0000'), origin = positions.get(originId), mission = positions.get(missionId), returned = positions.get(returnId); if (!origin || !mission || !returned) continue; const relation = svgElement('polyline', { points: `${origin.x},${origin.y} ${mission.x},${mission.y} ${returned.x},${returned.y}`, class: `spatial-link${originId !== returnId ? ' cross-return' : ''}` }); const title = svgElement('title'); title.textContent = `${airportDisplay(originId)} → ${missionName(missionId)} → ${airportDisplay(returnId)} · ${integer(sorties)}架次`; relation.append(title); svg.append(relation); }
  for (const [id] of airports) { const point = positions.get(id), circle = svgElement('circle', { cx: point.x, cy: point.y, r: 6, class: 'spatial-airport' }); const title = svgElement('title'); title.textContent = airportDisplay(id); circle.append(title); svg.append(circle); const label = svgElement('text', { x: point.x + 8, y: point.y - 7, class: 'spatial-label' }); label.textContent = airportDisplay(id); svg.append(label); }
  for (const [id] of missions) { const point = positions.get(id), size = 7, diamond = svgElement('path', { d: `M${point.x} ${point.y - size} L${point.x + size} ${point.y} L${point.x} ${point.y + size} L${point.x - size} ${point.y} Z`, class: 'spatial-mission' }); const title = svgElement('title'); title.textContent = missionName(id); diamond.append(title); svg.append(diamond); const label = svgElement('text', { x: point.x + 9, y: point.y + 12, class: 'spatial-label' }); label.textContent = missionName(id); svg.append(label); }
  refs.runSpatialChart.append(svg);
}
function renderRecordView() {
  const spatial = state.recordView === 'spatial'; refs.recordFactsPanel.classList.toggle('hidden', spatial); refs.runSpatialView.classList.toggle('hidden', !spatial); [...refs.runRecordTabs.querySelectorAll('button[data-record-view]')].forEach((button) => button.classList.toggle('active', button.dataset.recordView === state.recordView)); if (spatial) renderRunSpatialView();
}
function renderChainTable() {
  refs.chainTableBody.replaceChildren(); const chains = [...(state.solution.sortie_chains || [])].sort((a, b) => a.depart_window - b.depart_window || a.origin_airport_id.localeCompare(b.origin_airport_id) || a.mission_id.localeCompare(b.mission_id));
  if (!chains.length) { const tr = document.createElement('tr'); const td = text('td', '当前 Solution 没有可展示航链', 'empty-cell'); td.colSpan = 8; tr.append(td); refs.chainTableBody.append(tr); return; }
  for (const chain of chains) { const tr = document.createElement('tr'); [airportDisplay(chain.origin_airport_id), missionName(chain.mission_id), airportDisplay(chain.return_airport_id), chain.aircraft_type, windowLabel(chain.depart_window), windowLabel(chain.return_window), windowLabel(chain.ready_window), integer(chain.sorties)].forEach((value) => tr.append(text('td', value))); refs.chainTableBody.append(tr); }
}

const AIRCRAFT_COLORS = ['#49a5e9', '#8d7be8', '#e5a449', '#55b99a', '#d46b70', '#78a8b8'];
function aircraftColor(id) { const types = [...new Set(Object.values(state.metrics.tasks || {}).flatMap((row) => Object.keys(row.scheduled_by_aircraft || {})))].sort(); return AIRCRAFT_COLORS[Math.max(0, types.indexOf(id)) % AIRCRAFT_COLORS.length]; }
function capacityOccupancyFacts(capacity, windows = []) {
  const fields = ['available', 'used_departure', 'used_arrival', 'used_total', 'utilization'];
  const lengths = fields.map((field) => Array.isArray(capacity?.[field]) ? capacity[field].length : 0);
  const expectedLength = Array.isArray(windows) && windows.length ? windows.length : Math.max(0, ...lengths);
  let usedSum = 0, availableSum = 0, validWindowCount = 0, unavailableWindowCount = 0, missingWindowCount = 0;
  let peak = null, peakWindow = null, fullWindowCount = 0;
  for (let index = 0; index < expectedLength; index += 1) {
    const values = Object.fromEntries(fields.map((field) => [field, Array.isArray(capacity?.[field]) ? capacity[field][index] : undefined]));
    if (typeof values.available === 'number' && Number.isFinite(values.available) && values.available === 0) { unavailableWindowCount += 1; continue; }
    const complete = fields.every((field) => typeof values[field] === 'number' && Number.isFinite(values[field]));
    if (!(values.available > 0) || !complete) { missingWindowCount += 1; continue; }
    validWindowCount += 1; usedSum += values.used_total; availableSum += values.available;
    if (peak === null || values.utilization > peak) { peak = values.utilization; peakWindow = Array.isArray(windows) && index < windows.length ? windows[index] : index; }
    if (values.utilization >= 1 - 1e-9) fullWindowCount += 1;
  }
  let status = 'recorded';
  if (!validWindowCount) status = missingWindowCount ? 'missing' : 'not_applicable';
  else if (missingWindowCount) status = 'partial';
  return {
    status,
    occupancy: availableSum > 0 ? usedSum / availableSum : null,
    peak,
    peakWindow,
    fullWindowCount,
    validWindowCount,
    unavailableWindowCount,
    missingWindowCount,
  };
}
function airportAircraftDepartures(airportId) {
  const result = {}; for (const chain of state.solution.sortie_chains || []) if (chain.origin_airport_id === airportId) result[chain.aircraft_type] = (result[chain.aircraft_type] || 0) + Number(chain.sorties || 0); return result;
}
function renderCollaborationView() {
  const aggregates = new Map();
  for (const chain of state.solution.sortie_chains || []) { const key = `${chain.origin_airport_id}\u0000${chain.mission_id}\u0000${chain.return_airport_id}`; const row = aggregates.get(key) || { origin_airport_id: chain.origin_airport_id, mission_id: chain.mission_id, return_airport_id: chain.return_airport_id, sorties: 0 }; row.sorties += Number(chain.sorties || 0); aggregates.set(key, row); }
  const rows = [...aggregates.values()].sort((a, b) => b.sorties - a.sorties || a.origin_airport_id.localeCompare(b.origin_airport_id)); refs.airportAcceptanceList.replaceChildren();
  if (!rows.length) { refs.airportAcceptanceList.append(text('div', '当前 Solution 没有协同航链', 'chart-empty')); return; }
  for (const row of rows) { const item = document.createElement('button'); item.type = 'button'; item.dataset.airportId = row.origin_airport_id; item.className = `collaboration-row${row.origin_airport_id !== row.return_airport_id ? ' cross-return' : ''}${row.origin_airport_id === state.supportAirportId ? ' selected' : ''}`; item.append(text('strong', `${airportDisplay(row.origin_airport_id)} → ${missionName(row.mission_id)}`), text('span', `返航 ${airportDisplay(row.return_airport_id)}`), text('b', `${integer(row.sorties)} 架次`)); refs.airportAcceptanceList.append(item); }
}
function renderAirportShare(airports) {
  refs.airportAcceptanceList.replaceChildren(); const total = airports.reduce((sum, [, row]) => sum + Number(row.departures_total || 0), 0), colors = ['#49a5e9', '#8d7be8', '#e5a449', '#55b99a', '#d46b70', '#78a8b8', '#b4c65d', '#c47cab'];
  const layout = document.createElement('div'); layout.className = 'airport-share-layout'; const donut = document.createElement('div'); donut.className = 'airport-donut'; const svg = svgElement('svg', { viewBox: '0 0 120 120', 'aria-hidden': 'true' }); const radius = 45, circumference = 2 * Math.PI * radius; let offset = 0;
  airports.forEach(([airportId, row], index) => { const share = total > 0 ? Number(row.departures_total || 0) / total : 0; const circle = svgElement('circle', { cx: 60, cy: 60, r: radius, fill: 'none', stroke: colors[index % colors.length], 'stroke-width': 16, 'stroke-dasharray': `${share * circumference} ${circumference}`, 'stroke-dashoffset': -offset * circumference }); offset += share; const title = svgElement('title'); title.textContent = `${airportDisplay(airportId)} ${percent(share)}`; circle.append(title); svg.append(circle); });
  const center = document.createElement('div'); center.className = 'airport-donut-center'; center.append(text('strong', integer(total)), text('span', '总出动架次')); donut.append(svg, center); const list = document.createElement('div'); list.className = 'airport-share-list';
  airports.forEach(([airportId, row], index) => { const share = total > 0 ? Number(row.departures_total || 0) / total : 0; const button = document.createElement('button'); button.type = 'button'; button.dataset.airportId = airportId; button.className = `airport-share-row${airportId === state.supportAirportId ? ' selected' : ''}`; button.style.setProperty('--airport-color', colors[index % colors.length]); button.append(text('i', ''), text('span', airportDisplay(airportId)), text('strong', `${integer(row.departures_total)} · ${percent(share)}`)); list.append(button); });
  layout.append(donut, list); refs.airportAcceptanceList.append(layout);
}
function renderAirportAcceptance() {
  if (state.airportView === 'collaboration') { renderCollaborationView(); return; }
  const airports = Object.entries(state.metrics.airports || {}).sort((a, b) => Number(b[1].departures_total || 0) - Number(a[1].departures_total || 0) || a[0].localeCompare(b[0]));
  refs.airportAcceptanceList.replaceChildren();
  if (!airports.length) { refs.airportAcceptanceList.append(text('div', '没有机场承接记录', 'chart-empty')); return; }
  if (state.airportView === 'share') { renderAirportShare(airports); return; }
  const maxDepartures = Math.max(1, ...airports.map(([, row]) => Number(row.departures_total || 0)));
  for (const [airportId, row] of airports) {
    const button = document.createElement('button'); button.type = 'button'; button.className = `airport-row${airportId === state.supportAirportId ? ' selected' : ''}`; button.dataset.airportId = airportId; button.setAttribute('role', 'listitem');
    const identity = document.createElement('div'); identity.className = 'airport-identity'; identity.append(text('strong', airportDisplay(airportId)));
    const roles = document.createElement('span'); roles.className = 'airport-roles'; if (row.is_core) roles.append(text('i', '核心')); if (row.is_selected_cluster) roles.append(text('i', '组选')); if (row.is_participating) roles.append(text('i', '参与')); identity.append(roles);
    const bar = document.createElement('div'); bar.className = 'airport-bar'; const byAircraft = airportAircraftDepartures(airportId); for (const [aircraftType, sorties] of Object.entries(byAircraft).sort(([a], [b]) => a.localeCompare(b))) { const segment = text('span', '', 'airport-aircraft-stack'); segment.style.setProperty('--part', String(Number(sorties || 0) / maxDepartures)); segment.style.setProperty('--aircraft-color', aircraftColor(aircraftType)); segment.title = `${aircraftType} ${integer(sorties)} 架次`; bar.append(segment); }
    const capacityFacts = capacityOccupancyFacts(row.capacity, state.metrics.time_axis?.windows || []); const facts = document.createElement('div'); facts.className = 'airport-facts'; const factMeta = document.createElement('span'); factMeta.className = 'airport-fact-meta';
    const occupancyValue = capacityFacts.status === 'not_applicable' ? '不适用' : capacityFacts.status === 'missing' ? '未记录' : percent(capacityFacts.occupancy); const capacityBadge = text('i', `占用 ${occupancyValue}`, 'capacity-pressure');
    capacityBadge.title = [`运行期容量占用率：${occupancyValue}`, `峰值利用率：${recorded(capacityFacts.peak, (value) => percent(value))}`, `峰值时间窗：${capacityFacts.peakWindow === null ? '未记录' : windowLabel(capacityFacts.peakWindow)}`, `满负荷时间窗：${integer(capacityFacts.fullWindowCount)}`, `有效时间窗：${integer(capacityFacts.validWindowCount)}`, `不可用时间窗：${integer(capacityFacts.unavailableWindowCount)}`, `缺失时间窗：${integer(capacityFacts.missingWindowCount)}`].join('\n'); capacityBadge.setAttribute('aria-label', capacityBadge.title.replaceAll('\n', '；'));
    factMeta.append(document.createTextNode(`承接 ${percent(row.departure_share)}`), capacityBadge); facts.append(text('strong', `${integer(row.departures_total)} 架次`), factMeta);
    button.append(identity, bar, facts); refs.airportAcceptanceList.append(button);
  }
  const collaboration = state.metrics.collaboration || {};
  refs.airportCollaborationSummary.textContent = `跨机场返航 ${integer(collaboration.cross_return_sorties)} 架次 · 占比 ${percent(collaboration.cross_return_ratio)} · 出动集中度 HHI ${formatHhi(collaboration.departure_hhi)}`;
}

function orderedTaskEntries() {
  const tasks = state.metrics.tasks || {}, orderedIds = (state.situation?.missions || []).map((row) => row.mission_id).filter((id) => tasks[id]);
  for (const id of Object.keys(tasks).sort()) if (!orderedIds.includes(id)) orderedIds.push(id); return orderedIds.map((id) => [id, tasks[id]]);
}
function taskPanelHeight(taskCount) { const count = Number.isFinite(taskCount) ? Math.max(0, Math.floor(taskCount)) : 0; return Math.min(340, Math.max(182, 68 + count * 38)); }
function getHeatColor(heat) {
  const ratio = Math.max(0, Math.min(1, heat));
  const low = [11, 42, 60], high = [73, 165, 233];
  const channels = low.map((value, index) => Math.round(value + (high[index] - value) * ratio));
  return `rgb(${channels.join(', ')})`;
}
function renderTaskDistribution() {
  const entries = orderedTaskEntries(), windows = state.metrics.time_axis.windows || [], range = visibleTimelineRange(), aircraftTypes = [...new Set(entries.flatMap(([, row]) => Object.keys(row.scheduled_by_aircraft || {})))].sort();
  refs.content.style.setProperty('--task-panel-height', `${taskPanelHeight(entries.length)}px`); refs.content.dataset.taskCount = String(entries.length);
  refs.aircraftLegend.replaceChildren(...aircraftTypes.map((id) => { const item = text('span', id); item.style.setProperty('--aircraft-color', aircraftColor(id)); return item; })); refs.taskDistributionRows.replaceChildren();
  if (!entries.length) { refs.taskDistributionRows.append(text('div', '没有任务结果', 'chart-empty')); return; }
  const allTimelineValues = entries.flatMap(([, row]) => Array.isArray(row.departures_timeline) ? row.departures_timeline.filter(Number.isFinite) : []), maxTimeline = Math.max(1, ...allTimelineValues);
  for (const [missionId, row] of entries) {
    const timeline = row.departures_timeline, line = document.createElement('button'); line.type = 'button'; line.className = `task-row${state.timelineMode === 'mission' && state.timelineObjectId === missionId ? ' selected' : ''}`; line.dataset.missionId = missionId; line.setAttribute('role', 'listitem');
    const name = text('strong', missionName(missionId), 'task-name'); name.title = missionName(missionId); const heat = document.createElement('div'); heat.className = 'task-heat';
    if (!Array.isArray(timeline) || timeline.length !== windows.length) heat.append(text('span', '时序未记录', 'task-missing')); else timeline.forEach((value, index) => {
      const cell = document.createElement('i'), numericValue = typeof value === 'number' && Number.isFinite(value) ? value : null, label = windowLabel(windows[index]);
      cell.dataset.windowIndex = String(index); cell.classList.toggle('focused', index === state.selectedWindowIndex); cell.classList.toggle('timeline-window-hidden', index < range.start || index >= range.end);
      if (numericValue === null) { cell.classList.add('task-heat-missing'); cell.title = `${label} · 出动未记录`; cell.setAttribute('aria-label', `${label} · 出动未记录`); }
      else {
        const heatValue = numericValue / maxTimeline;
        cell.style.setProperty('--heat', String(heatValue));
        cell.style.backgroundColor = getHeatColor(heatValue);
        cell.title = `${label} · 出动 ${integer(numericValue)}`; cell.setAttribute('aria-label', `${label} · 出动 ${integer(numericValue)}`);
      }
      heat.append(cell);
    });
    const composition = document.createElement('div'); composition.className = 'task-composition'; const bar = document.createElement('div'); bar.className = 'composition-bar'; const scheduledTotal = Object.values(row.scheduled_by_aircraft || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    aircraftTypes.forEach((id) => { const value = Number(row.scheduled_by_aircraft?.[id] || 0); if (!(value > 0)) return; const part = document.createElement('i'); part.style.setProperty('--part', String(value / Math.max(1, scheduledTotal))); part.style.setProperty('--aircraft-color', aircraftColor(id)); part.title = `${id} · 调度 ${integer(value)}`; bar.append(part); });
    const sum = (obj) => Object.values(obj || {}).reduce((total, value) => total + Number(value || 0), 0), fulfilled = sum(row.fulfilled_by_aircraft), required = sum(row.required_by_aircraft), unmet = sum(row.unmet_by_aircraft), additional = sum(row.additional_by_aircraft);
    composition.append(bar, text('small', `${integer(fulfilled)}/${integer(required)}${unmet ? ` · 缺 ${integer(unmet)}` : ''}${additional ? ` · 额外 ${integer(additional)}` : ''}`)); line.append(name, heat, composition); refs.taskDistributionRows.append(line);
  }
  renderTimelineWindowNavigation(range);
}

function option(select, value, label) { const row = text('option', label); row.value = value; select.append(row); }
function populateSupportControls() {
  const airportIds = Object.keys(state.metrics.airports || {}).sort(); if (!airportIds.includes(state.supportAirportId)) state.supportAirportId = airportIds.find((id) => state.metrics.airports[id]?.is_participating) || airportIds[0] || null;
  refs.supportAirportSelect.replaceChildren(); airportIds.forEach((id) => option(refs.supportAirportSelect, id, airportDisplay(id))); refs.supportAirportSelect.value = state.supportAirportId || '';
  populateDependentSupportControls();
}
function populateDependentSupportControls() {
  const resources = Object.keys(state.metrics.resources?.by_airport?.[state.supportAirportId] || {}).sort(); if (!resources.includes(state.supportResourceId)) state.supportResourceId = resources[0] || null;
  refs.supportResourceSelect.replaceChildren(); resources.forEach((id) => option(refs.supportResourceSelect, id, resourceLabel(id))); refs.supportResourceSelect.value = state.supportResourceId || ''; refs.supportResourceSelect.disabled = !resources.length;
  const aircraftTypes = Object.keys(state.metrics.aircraft_inventory?.by_airport?.[state.supportAirportId] || {}).sort(); if (!aircraftTypes.includes(state.supportAircraftTypeId)) state.supportAircraftTypeId = aircraftTypes[0] || null;
  refs.supportAircraftSelect.replaceChildren(); aircraftTypes.forEach((id) => option(refs.supportAircraftSelect, id, id)); refs.supportAircraftSelect.value = state.supportAircraftTypeId || ''; refs.supportAircraftSelect.disabled = !aircraftTypes.length;
}
function renderCapacityHeatmap() {
  const row = state.metrics.airports?.[state.supportAirportId], windows = state.metrics.time_axis.windows; refs.capacitySubtitle.textContent = airportDisplay(state.supportAirportId);
  if (!row?.capacity || !windows.length) { refs.capacityHeatmap.replaceChildren(text('div', '该机场没有容量时序', 'chart-empty')); return; }
  const table = document.createElement('table'); table.className = 'heatmap-table'; const head = document.createElement('tr'); head.append(text('th', '指标')); windows.forEach((window) => head.append(text('th', windowLabel(window)))); const thead = document.createElement('thead'); thead.append(head); table.append(thead); const tbody = document.createElement('tbody');
  const definitions = [['utilization', '利用率', percent], ['available', '可用容量', number], ['used_departure', '出动占用', number], ['used_arrival', '返航占用', number]];
  for (const [field, label, formatter] of definitions) { const tr = document.createElement('tr'); tr.append(text('th', label)); (row.capacity[field] || []).forEach((value, index) => { const td = text('td', formatter(value, field === 'utilization' ? 1 : 2)); const ratio = field === 'utilization' ? value : (row.capacity.available?.[index] ? value / row.capacity.available[index] : 0); td.style.setProperty('--heat', String(Math.max(0, Math.min(1, Number(ratio) || 0)))); td.title = `${airportDisplay(state.supportAirportId)} · ${windowLabel(windows[index])} · ${label} ${formatter(value)}`; tr.append(td); }); tbody.append(tr); }
  table.append(tbody); refs.capacityHeatmap.replaceChildren(table);
}
function stepPath(values, width, height, maxValue, pad) { if (!values.length) return ''; const span = width - 2 * pad, ySpan = height - 2 * pad; const point = (value, index) => [pad + (values.length === 1 ? span / 2 : index / (values.length - 1) * span), height - pad - value / Math.max(1e-12, maxValue) * ySpan]; const first = point(values[0], 0); const parts = [`M${first[0]} ${first[1]}`]; for (let index = 1; index < values.length; index += 1) { const [x, y] = point(values[index], index); parts.push(`H${x} V${y}`); } return parts.join(' '); }
function stepPathInArea(values, width, maxValue, left, right, top, bottom) { if (!values.length) return ''; const xSpan = width - left - right, ySpan = bottom - top; const point = (value, index) => [left + (values.length === 1 ? xSpan / 2 : index / (values.length - 1) * xSpan), bottom - value / Math.max(1e-12, maxValue) * ySpan]; const first = point(values[0], 0); const parts = [`M${first[0]} ${first[1]}`]; for (let index = 1; index < values.length; index += 1) { const [x, y] = point(values[index], index); parts.push(`H${x} V${y}`); } return parts.join(' '); }
function renderResourceInventory() {
  const row = state.metrics.resources?.by_airport?.[state.supportAirportId]?.[state.supportResourceId], meta = state.metrics.resources?.resource_types?.[state.supportResourceId] || {}, windows = state.metrics.time_axis.windows; refs.resourceSubtitle.textContent = row ? `${airportDisplay(state.supportAirportId)} · ${resourceLabel(state.supportResourceId)}` : '无具体资源'; refs.resourceInventoryChart.replaceChildren();
  if (!row) { refs.resourceInventoryChart.append(text('div', '该机场没有资源库存记录', 'chart-empty')); return; }
  const series = [row.remaining, row.damage_adjusted_base_boundary, row.storage_capacity].filter(Array.isArray); const values = series.flat().filter((value) => typeof value === 'number' && Number.isFinite(value)); if (!values.length) { refs.resourceInventoryChart.append(text('div', '库存时序未记录', 'chart-empty')); return; }
  const width = 760, height = 270, pad = 30, plotHeight = 202, maxValue = niceAxisMaximum(Math.max(1, ...values)); const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'xMidYMid meet' });
  if (Array.isArray(row.damage_adjusted_base_boundary)) svg.append(svgElement('path', { d: stepPath(row.damage_adjusted_base_boundary, width, plotHeight, maxValue, pad), class: 'inventory-boundary', fill: 'none' })); if (Array.isArray(row.storage_capacity)) svg.append(svgElement('path', { d: stepPath(row.storage_capacity, width, plotHeight, maxValue, pad), class: 'inventory-capacity', fill: 'none' })); if (Array.isArray(row.remaining)) svg.append(svgElement('path', { d: stepPath(row.remaining, width, plotHeight, maxValue, pad), class: 'inventory-stock', fill: 'none' }));
  const eventRows = [['replenishment_actual', 218, '补给', 'event-replenishment'], ['consumed_increment', 238, '消耗', 'event-consumption'], ['permanent_loss', 258, '永久损失', 'event-loss']];
  eventRows.forEach(([field, y, label, className]) => { const valuesForEvent = row[field]; if (!Array.isArray(valuesForEvent)) return; valuesForEvent.forEach((value, index) => { if (!(value > 0)) return; const x = pad + (valuesForEvent.length === 1 ? (width - 2 * pad) / 2 : index / (valuesForEvent.length - 1) * (width - 2 * pad)); const point = svgElement('circle', { cx: x, cy: y, r: 4, class: className }); const title = svgElement('title'); title.textContent = `${windowLabel(windows[index])} ${label} ${number(value)} ${meta.unit || ''}`; point.append(title); svg.append(point); }); });
  const missingLoss = !Array.isArray(row.permanent_loss); const note = svgElement('text', { x: pad, y: 14, class: 'inventory-note' }); note.textContent = missingLoss ? '永久损失：未记录（不以损毁调整边界代替）' : '永久损失使用正式逐窗记录'; svg.append(note); refs.resourceInventoryChart.append(svg);
}
function renderAircraftTurnaround() {
  const row = state.metrics.aircraft_inventory?.by_airport?.[state.supportAirportId]?.[state.supportAircraftTypeId]; refs.aircraftSubtitle.textContent = row ? `${airportDisplay(state.supportAirportId)} · ${state.supportAircraftTypeId}` : '无该机型';
  if (!row) { refs.aircraftTurnaroundChart.replaceChildren(text('div', '该机场没有航空器保有记录', 'chart-empty')); return; }
  renderLineChart(refs.aircraftTurnaroundChart, [{ label: '可用', values: row.available_before_departure || [], stroke: '#49a5e9' }, { label: '占用', values: row.in_use || [], stroke: '#e5a449' }, { label: '出动', values: row.departures || [], stroke: '#d46b70' }, { label: '再次可用', values: row.ready_releases || [], stroke: '#68c88a' }], { contextLabel: `${airportDisplay(state.supportAirportId)} · ${state.supportAircraftTypeId}` });
}
function renderMiniBars(container, values, { fill, label, valueFormatter = number } = {}) {
  container.replaceChildren(); const windows = state.metrics.time_axis.windows || [], checked = validateTimelineSeries(windows, [{ label, values }]);
  if (!checked.ok) { container.append(text('div', checked.message, 'chart-empty chart-error')); return; }
  const finite = values.filter(Number.isFinite); if (!finite.length) { container.append(text('div', '逐窗数据未记录', 'chart-empty')); return; }
  const width = 520, height = 112, pad = 10, maxValue = niceAxisMaximum(Math.max(1, ...finite)), span = width - pad * 2, group = span / Math.max(1, values.length); const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'xMidYMid meet' });
  values.forEach((value, index) => { if (!Number.isFinite(value)) return; const h = value / maxValue * (height - 2 * pad), bar = svgElement('rect', { x: pad + group * index + group * .16, y: height - pad - h, width: Math.max(1, group * .68), height: h, fill, rx: 1 }); const title = svgElement('title'); title.textContent = `${windowLabel(windows[index])} · ${label} ${valueFormatter(value)}`; bar.append(title); svg.append(bar); }); container.append(svg);
}
function aircraftSupportFacts(aircraft) {
  const finiteInUse = Array.isArray(aircraft?.in_use) ? aircraft.in_use.filter(Number.isFinite) : [];
  const peakInUse = finiteInUse.length ? Math.max(...finiteInUse) : null;
  const endAvailable = Array.isArray(aircraft?.available_after_departure) ? aircraft.available_after_departure.at(-1) : null;
  const readyTotal = Array.isArray(aircraft?.ready_releases) && aircraft.ready_releases.every(Number.isFinite) ? aircraft.ready_releases.reduce((sum, value) => sum + value, 0) : null;
  return [['峰值占用', recorded(peakInUse, integer), '本场记录'], ['期末可用', recorded(endAvailable, integer), state.supportAircraftTypeId || '—'], ['期内再次可用', recorded(readyTotal, integer), '正式逐窗释放量']];
}
function resourceSupportFacts(resource) {
  const sum = (values) => Array.isArray(values) && values.every((value) => Number.isFinite(value)) ? values.reduce((total, value) => total + value, 0) : null;
  const remaining = Array.isArray(resource?.remaining) ? resource.remaining.at(-1) : null;
  return [['累计消耗', recorded(sum(resource?.consumed_increment), number), ''], ['实际补给', recorded(sum(resource?.replenishment_actual), number), ''], ['期末剩余', recorded(remaining, number), '']];
}
function summaryNodes(facts) { return facts.map(([label, value, note]) => summaryItem(label, value, note)); }
function renderSupportView() {
  const aircraftActive = state.supportView === 'aircraft';
  refs.aircraftSupportPanel.classList.toggle('active', aircraftActive);
  refs.resourceSupportPanel.classList.toggle('active', !aircraftActive);
  [...refs.supportViews.querySelectorAll('button[data-support-view]')].forEach((button) => { const active = button.dataset.supportView === state.supportView; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
  const aircraft = state.metrics.aircraft_inventory?.by_airport?.[state.supportAirportId]?.[state.supportAircraftTypeId];
  const resource = state.metrics.resources?.by_airport?.[state.supportAirportId]?.[state.supportResourceId];
  const meta = state.metrics.resources?.resource_types?.[state.supportResourceId] || {};
  const label = text('strong', aircraftActive ? (resource ? resourceLabel(state.supportResourceId) : '资源摘要') : '航空器摘要');
  const facts = aircraftActive ? resourceSupportFacts(resource) : aircraftSupportFacts(aircraft);
  refs.supportSecondarySummary.replaceChildren(label, ...summaryNodes(facts));
}
function renderCompactSupport() {
  refs.supportAirportLabel.textContent = airportDisplay(state.supportAirportId);
  const aircraft = state.metrics.aircraft_inventory?.by_airport?.[state.supportAirportId]?.[state.supportAircraftTypeId];
  refs.aircraftSubtitle.textContent = aircraft ? `${airportDisplay(state.supportAirportId)} · ${state.supportAircraftTypeId}` : '无该机型';
  if (!aircraft) { refs.aircraftMiniChart.replaceChildren(text('div', '该机场没有航空器保有记录', 'chart-empty')); }
  else renderLineChart(refs.aircraftMiniChart, [{ label: '可用', values: aircraft.available_before_departure || [], stroke: '#49a5e9' }, { label: '占用', values: aircraft.in_use || [], stroke: '#e5a449' }, { label: '再次可用', values: aircraft.ready_releases || [], stroke: '#68c88a' }], { contextLabel: `${airportDisplay(state.supportAirportId)} · ${state.supportAircraftTypeId}` });
  refs.aircraftSummary.replaceChildren(...summaryNodes(aircraftSupportFacts(aircraft)));
  const resource = state.metrics.resources?.by_airport?.[state.supportAirportId]?.[state.supportResourceId], meta = state.metrics.resources?.resource_types?.[state.supportResourceId] || {};
  refs.resourceSubtitle.textContent = resource ? `${resourceLabel(state.supportResourceId)} · ${airportDisplay(state.supportAirportId)}` : '无具体资源';
  if (!resource) { refs.resourceMiniChart.replaceChildren(text('div', '该机场没有资源记录', 'chart-empty')); }
  else renderMiniBars(refs.resourceMiniChart, resource.consumed_increment || [], { fill: '#e5a449', label: '消耗', valueFormatter: (value) => `${number(value)} ${meta.unit || ''}`.trim() });
  refs.resourceSummary.replaceChildren(...summaryNodes(resourceSupportFacts(resource)));
  renderSupportView();
}
function populateResourceDetailControls() {
  refs.resourceDetailAirportSelect.replaceChildren(); Object.keys(state.metrics.airports || {}).sort().forEach((id) => option(refs.resourceDetailAirportSelect, id, airportDisplay(id))); refs.resourceDetailAirportSelect.value = state.supportAirportId || '';
  refs.resourceDetailResourceSelect.replaceChildren(); Object.keys(state.metrics.resources?.by_airport?.[state.supportAirportId] || {}).sort().forEach((id) => option(refs.resourceDetailResourceSelect, id, resourceLabel(id))); refs.resourceDetailResourceSelect.value = state.supportResourceId || '';
}
function renderResourceProcessChart(container, row, meta) {
  container.replaceChildren(); const windows = state.metrics.time_axis.windows || [], stock = Array.isArray(row.remaining) ? row.remaining : null;
  const eventDefinitions = [['consumed_increment', '消耗', 'resource-event-consumption'], ['replenishment_actual', '补给', 'resource-event-replenishment'], ['permanent_loss', '永久损失', 'resource-event-loss']];
  const eventSeries = eventDefinitions.map(([field, label, className]) => ({ field, label, className, values: Array.isArray(row[field]) ? row[field] : null }));
  if (!stock && eventSeries.every((item) => !item.values)) { container.append(text('div', '库存与事件时序均未记录', 'chart-empty')); return; }
  const width = Math.max(820, windows.length * 13), height = 400, left = 42, right = 18, stockTop = 22, stockBottom = 244, dividerY = 260, eventTop = 282, eventBottom = 370, plotWidth = width - left - right;
  const stockSeries = [stock, Array.isArray(row.storage_capacity) && meta.unit ? row.storage_capacity : null].filter(Array.isArray), stockValues = stockSeries.flat().filter(Number.isFinite), stockMax = niceAxisMaximum(Math.max(1, ...stockValues));
  const eventValues = eventSeries.flatMap((item) => (item.values || []).filter(Number.isFinite)), eventMax = niceAxisMaximum(Math.max(1, ...eventValues)); const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'xMinYMid meet' }); svg.style.width = `${width}px`; svg.style.minWidth = `${width}px`;
  for (let index = 0; index <= 3; index += 1) { const y = stockTop + (stockBottom - stockTop) / 3 * index; svg.append(svgElement('line', { x1: left, x2: width - right, y1: y, y2: y, class: 'chart-grid-line' })); const label = svgElement('text', { x: left - 6, y: y + 3, 'text-anchor': 'end', class: 'chart-axis-label' }); label.textContent = number(stockMax * (3 - index) / 3); svg.append(label); }
  if (stock) svg.append(svgElement('path', { d: stepPathInArea(stock, width, stockMax, left, right, stockTop, stockBottom), class: 'resource-stock-layer' }));
  if (Array.isArray(row.storage_capacity) && meta.unit) svg.append(svgElement('path', { d: stepPathInArea(row.storage_capacity, width, stockMax, left, right, stockTop, stockBottom), class: 'resource-capacity-layer' }));
  svg.append(svgElement('line', { x1: left, x2: width - right, y1: dividerY, y2: dividerY, class: 'resource-layer-divider' }));
  const group = plotWidth / Math.max(1, windows.length), barWidth = Math.max(1, Math.min(6, group * .22)); eventSeries.forEach((series, seriesIndex) => { if (!series.values) return; series.values.forEach((value, index) => { if (!Number.isFinite(value)) return; const barHeight = Math.max(0, value / eventMax * (eventBottom - eventTop)), x = left + group * (index + .5) + (seriesIndex - 1) * (barWidth + 1); const bar = svgElement('rect', { x, y: eventBottom - barHeight, width: barWidth, height: barHeight, class: `resource-event-layer ${series.className}` }); const title = svgElement('title'); title.textContent = `${windowLabel(windows[index])} · ${series.label} ${number(value)} ${meta.unit || ''}`; bar.append(title); svg.append(bar); }); });
  const labelStep = Math.max(1, Math.ceil(windows.length / 12)); windows.forEach((window, index) => { if (index % labelStep && index !== windows.length - 1) return; const label = svgElement('text', { x: left + group * (index + .5), y: 392, 'text-anchor': 'middle', class: 'chart-axis-label' }); label.textContent = windowLabel(window); svg.append(label); });
  const stockLabel = svgElement('text', { x: left, y: 13, class: 'inventory-note' }); stockLabel.textContent = `窗末库存${Array.isArray(row.storage_capacity) && meta.unit ? ' / 仓储上限' : ''}（${meta.unit || '单位未记录'}）`; svg.append(stockLabel); const eventLabel = svgElement('text', { x: left, y: 275, class: 'inventory-note' }); eventLabel.textContent = Array.isArray(row.permanent_loss) ? '数量事件：消耗 / 补给 / 正式永久损失' : '数量事件：消耗 / 补给；永久损失：未记录'; svg.append(eventLabel); container.append(svg);
}
function renderResourceDetail() {
  const row = state.metrics.resources?.by_airport?.[state.supportAirportId]?.[state.supportResourceId], meta = state.metrics.resources?.resource_types?.[state.supportResourceId] || {};
  if (!row) { refs.resourceDetailChart.replaceChildren(text('div', '当前选择没有资源记录', 'chart-empty')); refs.resourceDetailSummary.replaceChildren(); return; }
  renderResourceProcessChart(refs.resourceDetailChart, row, meta);
  const sum = (values) => Array.isArray(values) && values.every(Number.isFinite) ? values.reduce((total, value) => total + value, 0) : null, remaining = Array.isArray(row.remaining) ? row.remaining.at(-1) : null, permanentLoss = Array.isArray(row.permanent_loss) ? sum(row.permanent_loss) : null;
  refs.resourceDetailSummary.replaceChildren(summaryItem('累计消耗', recorded(sum(row.consumed_increment), (value) => `${number(value)} ${meta.unit || ''}`.trim())), summaryItem('实际补给', recorded(sum(row.replenishment_actual), (value) => `${number(value)} ${meta.unit || ''}`.trim())), summaryItem('期末剩余', recorded(remaining, (value) => `${number(value)} ${meta.unit || ''}`.trim())), summaryItem('永久损失', recorded(permanentLoss, (value) => `${number(value)} ${meta.unit || ''}`.trim()), Array.isArray(row.permanent_loss) ? '正式逐窗记录' : '未记录，不以边界代替'));
}
function renderSupport() { renderCapacityHeatmap(); renderResourceInventory(); renderAircraftTurnaround(); renderCompactSupport(); }
function bindInteractions() {
  refs.timelineViews.addEventListener('click', (event) => { const button = event.target.closest('button[data-view]'); if (!button || button.disabled) return; state.timelineView = button.dataset.view; renderTimeline(); });
  refs.timelineEventModes.addEventListener('click', (event) => { const button = event.target.closest('button[data-event-mode]'); if (!button) return; state.timelineEventMode = button.dataset.eventMode; renderTimeline(); });
  refs.timelineDirections.addEventListener('click', (event) => { const button = event.target.closest('button[data-direction]'); if (!button) return; state.timelineDirection = button.dataset.direction; renderTimeline(); });
  refs.timelineWindowNav.addEventListener('click', (event) => { const button = event.target.closest('button[data-window-nav]'); if (!button) return; const direction = button.dataset.windowNav === 'previous' ? -1 : 1; state.timelineWindowStart += direction * timelineWindowStep(); renderTimeline(); renderTaskDistribution(); });
  refs.timelineModes.addEventListener('click', (event) => { const button = event.target.closest('button[data-mode]'); if (!button) return; state.timelineMode = button.dataset.mode; state.timelineObjectId = null; renderTimeline(); });
  refs.timelineObjectSelect.addEventListener('change', () => { state.timelineObjectId = refs.timelineObjectSelect.value || null; renderTimeline(); });
  refs.supportViews.addEventListener('click', (event) => { const button = event.target.closest('button[data-support-view]'); if (!button) return; state.supportView = button.dataset.supportView; renderSupportView(); });
  refs.supportAirportSelect.addEventListener('change', () => { state.supportAirportId = refs.supportAirportSelect.value || null; state.supportResourceId = null; state.supportAircraftTypeId = null; populateDependentSupportControls(); renderAirportAcceptance(); renderSupport(); });
  refs.supportResourceSelect.addEventListener('change', () => { state.supportResourceId = refs.supportResourceSelect.value || null; renderResourceInventory(); renderCompactSupport(); });
  refs.supportAircraftSelect.addEventListener('change', () => { state.supportAircraftTypeId = refs.supportAircraftSelect.value || null; renderAircraftTurnaround(); renderCompactSupport(); });
  refs.airportAcceptanceList.addEventListener('click', (event) => { const row = event.target.closest('[data-airport-id]'); if (!row) return; const airportId = row.dataset.airportId; state.supportAirportId = airportId; state.supportResourceId = null; state.supportAircraftTypeId = null; state.timelineMode = 'airport'; state.timelineObjectId = airportId; populateSupportControls(); renderAirportAcceptance(); renderTimeline(); renderSupport(); });
  refs.taskDistributionRows.addEventListener('click', (event) => { const row = event.target.closest('[data-mission-id]'); if (!row) return; state.timelineMode = 'mission'; state.timelineObjectId = row.dataset.missionId; renderTaskDistribution(); renderTimeline(); });
  refs.airportViews.addEventListener('click', (event) => { const button = event.target.closest('button[data-airport-view]'); if (!button) return; state.airportView = button.dataset.airportView; [...refs.airportViews.querySelectorAll('button')].forEach((item) => item.classList.toggle('active', item === button)); refs.airportAcceptanceList.classList.toggle('collaboration-view', state.airportView === 'collaboration'); renderAirportAcceptance(); });
  refs.openRunRecordButton.addEventListener('click', () => { renderRecordView(); refs.runRecordDialog.showModal(); });
  refs.closeRunRecordButton.addEventListener('click', () => refs.runRecordDialog.close());
  refs.runRecordTabs.addEventListener('click', (event) => { const button = event.target.closest('button[data-record-view]'); if (!button) return; state.recordView = button.dataset.recordView; renderRecordView(); });
  refs.runRecordDialog.addEventListener('click', (event) => { if (event.target === refs.runRecordDialog) refs.runRecordDialog.close(); });
  refs.openResourceDetailButton.addEventListener('click', () => { populateResourceDetailControls(); renderResourceDetail(); refs.resourceDetailDialog.showModal(); });
  refs.closeResourceDetailButton.addEventListener('click', () => refs.resourceDetailDialog.close());
  refs.resourceDetailAirportSelect.addEventListener('change', () => { state.supportAirportId = refs.resourceDetailAirportSelect.value || null; state.supportResourceId = null; state.supportAircraftTypeId = null; populateSupportControls(); populateResourceDetailControls(); renderAirportAcceptance(); renderSupport(); renderResourceDetail(); });
  refs.resourceDetailResourceSelect.addEventListener('change', () => { state.supportResourceId = refs.resourceDetailResourceSelect.value || null; refs.supportResourceSelect.value = state.supportResourceId || ''; renderCompactSupport(); renderResourceInventory(); renderResourceDetail(); });
  refs.resourceDetailDialog.addEventListener('click', (event) => { if (event.target === refs.resourceDetailDialog) refs.resourceDetailDialog.close(); });
  refs.openRuntimeButton.addEventListener('click', () => { window.location.href = `/runs/${encodeURIComponent(state.runId)}/runtime`; });
  let resizeFrame = null; window.addEventListener('resize', () => { if (resizeFrame) cancelAnimationFrame(resizeFrame); resizeFrame = requestAnimationFrame(() => { resizeFrame = null; if (state.metrics) { renderTimeline(); renderTaskDistribution(); } }); });
}
function renderAll() { renderBadges(); renderConstraintStatus(); renderOverview(); renderTaskFulfillmentMatrix(); renderChainTable(); populateSupportControls(); updatePhaseAvailability(); renderTimeline(); renderAirportAcceptance(); renderTaskDistribution(); renderSupport(); renderRecordView(); refs.loading.classList.add('hidden'); refs.content.classList.remove('hidden'); }
async function init() {
  bindInteractions(); if (!state.runId) { showError(new Error('missing run id')); return; }
  try {
    const run = await apiFetch(`/api/runs/${encodeURIComponent(state.runId)}`); if (run.status !== 'succeeded') throw new ApiError(`单次运行仪表盘仅支持成功 Run；当前状态=${run.status}`, { status: 409, code: 'RUN_NOT_SUCCEEDED' }); state.run = run; state.runConfig = run.run_config || {};
    const [situation, solution, metrics] = await Promise.all([apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/situation`), apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/solution`), apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/metrics`)]);
    if (solution.run_id !== state.runId || metrics.run_id !== state.runId) throw new Error('Run result identity mismatch'); state.situation = situation; state.solution = solution; state.metrics = metrics; buildIndexes(); renderAll();
  } catch (error) { showError(error); }
}
document.addEventListener('DOMContentLoaded', init);
