import { apiFetch, ApiError } from './api-client.js';
import { formatDecimal, formatHhi, formatInteger, formatPercent, formatSeconds } from './number-display.js';

const PREFERENCE_LABELS = { sortie_max: '出动架次优先', resource_min: '资源消耗优先', time_min: '时间代价优先', custom: '自定义权重' };
const SOLVER_STATUS_LABELS = { optimal: '最优', gaplimit: '达到 Gap 限制', timelimit: '达到时间限制', infeasible: '不可行', unbounded: '无界', unknown: '状态未记录' };
const SVG_NS = 'http://www.w3.org/2000/svg';
const page = document.getElementById('singleRunPage');
const state = {
  runId: page?.dataset.runId || '', run: null, runConfig: null, situation: null, solution: null, metrics: null,
  airportById: new Map(), missionById: new Map(), timelineMode: 'all', timelineObjectId: null,
  supportAirportId: null, supportResourceId: null, supportAircraftTypeId: null,
};
const $ = (id) => document.getElementById(id);
const refs = {
  message: $('singleRunMessage'), loading: $('singleRunLoading'), content: $('singleRunContent'), runBadges: $('runBadges'),
  openRuntimeButton: $('openRuntimeButton'), overviewMetrics: $('overviewMetrics'), clusterSummary: $('clusterSummary'), solverFacts: $('solverFacts'),
  taskMatrix: $('taskFulfillmentMatrix'), timelineModes: $('timelineModes'), timelineObjectSelect: $('timelineObjectSelect'), timelineChart: $('timelineChart'),
  chainTableBody: $('chainTableBody'), supportAirportSelect: $('supportAirportSelect'), supportResourceSelect: $('supportResourceSelect'),
  supportAircraftSelect: $('supportAircraftSelect'), capacityHeatmap: $('capacityHeatmap'), resourceInventoryChart: $('resourceInventoryChart'),
  aircraftTurnaroundChart: $('aircraftTurnaroundChart'), capacitySubtitle: $('capacitySubtitle'), resourceSubtitle: $('resourceSubtitle'), aircraftSubtitle: $('aircraftSubtitle'),
};

function text(tag, value, className = '') { const el = document.createElement(tag); el.textContent = value ?? '—'; if (className) el.className = className; return el; }
function svgElement(name, attrs = {}) { const el = document.createElementNS(SVG_NS, name); for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value)); return el; }
function integer(value) { return formatInteger(value); }
function percent(value, digits = 1) { return formatPercent(value, { digits }); }
function number(value, digits = 2) { return formatDecimal(value, { minimumFractionDigits: digits, maximumFractionDigits: digits }); }
function windowLabel(value) { return Number.isInteger(value) ? `T${value}` : '—'; }
function recorded(value, formatter = String) { return value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value)) ? '未记录' : formatter(value); }
function shortRunId(id) { const match = /^RUN-([a-f0-9]{8})/i.exec(String(id || '')); return match ? `R-${match[1].toUpperCase()}` : String(id || '—'); }
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
  const damageId = state.runConfig?.damage_scenario_id;
  const damage = (state.situation?.damage_scenarios || []).find((row) => row.damage_scenario_id === damageId);
  const values = [shortRunId(state.run.run_id), state.situation?.name || state.run.situation_id, damage?.name || damageId || '无损毁', PREFERENCE_LABELS[state.runConfig?.preference_mode] || state.runConfig?.preference_mode || '偏好未记录'];
  refs.runBadges.replaceChildren(...values.map((value) => text('span', value, 'single-badge')));
}
function metricCard(label, value, note = '') {
  const article = document.createElement('article'); article.className = 'overview-metric';
  article.append(text('span', label), text('strong', value), text('small', note)); return article;
}
function renderOverview() {
  const summary = state.metrics.summary || {}, technical = state.metrics.technical || {};
  refs.overviewMetrics.replaceChildren(
    metricCard('需求', integer(summary.required_sorties_total), '架次'),
    metricCard('实际履行', integer(summary.fulfilled_sorties_total), '需求内完成'),
    metricCard('当前缺口', integer(summary.unmet_sorties_total), '普通 unmet'),
    metricCard('额外调度', integer(summary.additional_sorties_total), '超出任务需求'),
    metricCard('组选机场', integer(summary.selected_cluster_count), state.runConfig?.cluster_enabled ? '组选已启用' : '组选未启用'),
    metricCard('实际参与机场', integer(summary.participating_airport_count), `核心 ${integer(summary.core_airport_count)}`),
    metricCard('求解状态', recorded(technical.solver_status, (value) => SOLVER_STATUS_LABELS[value] || value), technical.gap === null || technical.gap === undefined ? 'Gap 未记录' : `Gap ${percent(technical.gap, 2)}`),
  );
  const collaboration = state.metrics.collaboration || {};
  const selected = (collaboration.selected_cluster || []).map(airportDisplay);
  const participating = (collaboration.participating_airports || []).map(airportDisplay);
  refs.clusterSummary.replaceChildren(
    text('p', `组选：${selected.join('、') || '未启用或未形成组选'}`),
    text('p', `实际参与：${participating.join('、') || '无参与机场'}`),
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
function nearestWindowIndex(clientX, rect, count, pad, width) { if (count <= 1) return 0; const svgX = (clientX - rect.left) / Math.max(1, rect.width) * width; return Math.round(Math.max(0, Math.min(1, (svgX - pad) / Math.max(1, width - pad * 2))) * (count - 1)); }
function renderLineChart(container, series, { contextLabel = null, valueFormatter = integer } = {}) {
  container.replaceChildren(); const windows = state.metrics.time_axis.windows; const checked = validateTimelineSeries(windows, series);
  if (!checked.ok) { container.append(text('div', checked.message, 'chart-empty chart-error')); return; } series = checked.series;
  const values = series.flatMap((row) => row.values.filter((value) => typeof value === 'number' && Number.isFinite(value))); if (!values.length) { container.append(text('div', '当前对象没有可展示的逐窗数据', 'chart-empty')); return; }
  const width = 760, height = 245, pad = 28, maxValue = Math.max(1, ...values); const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none' });
  for (let index = 0; index <= 4; index += 1) { const y = pad + (height - pad * 2) / 4 * index; svg.append(svgElement('line', { x1: pad, x2: width - pad, y1: y, y2: y, class: 'chart-grid-line' })); }
  series.forEach((row) => svg.append(svgElement('path', { d: pathFromSeries(row.values, width, height, maxValue, pad), fill: 'none', stroke: row.stroke, 'stroke-width': 2.4, 'vector-effect': 'non-scaling-stroke' })));
  const hover = svgElement('line', { class: 'chart-hover-line', y1: pad, y2: height - pad, visibility: 'hidden' }); svg.append(hover); const tooltip = text('div', '', 'chart-hover-tooltip'); tooltip.hidden = true;
  const move = (event) => { const rect = svg.getBoundingClientRect(), index = nearestWindowIndex(event.clientX, rect, windows.length, pad, width), x = pad + (windows.length === 1 ? (width - 2 * pad) / 2 : index / (windows.length - 1) * (width - 2 * pad)); hover.setAttribute('x1', x); hover.setAttribute('x2', x); hover.setAttribute('visibility', 'visible'); tooltip.textContent = [contextLabel, windowLabel(windows[index]), ...series.map((row) => `${row.label} ${row.values[index] === null ? '不适用' : valueFormatter(row.values[index])}`)].filter(Boolean).join('\n'); tooltip.hidden = false; tooltip.style.left = `${Math.min(Math.max(8, event.clientX - container.getBoundingClientRect().left + 10), Math.max(8, container.clientWidth - 180))}px`; tooltip.style.top = '18px'; };
  svg.addEventListener('pointermove', move); svg.addEventListener('pointerleave', () => { hover.setAttribute('visibility', 'hidden'); tooltip.hidden = true; }); container.append(svg, tooltip);
}
function timelineKeys(mode) { const source = mode === 'airport' ? state.metrics.timeline.by_airport : mode === 'mission' ? state.metrics.timeline.by_mission : mode === 'aircraft' ? state.metrics.timeline.by_aircraft : {}; return Object.keys(source || {}).sort(); }
function timelineLabel(mode, id) { return mode === 'airport' ? airportDisplay(id) : mode === 'mission' ? missionName(id) : id; }
function renderTimeline() {
  [...refs.timelineModes.querySelectorAll('button')].forEach((button) => button.classList.toggle('active', button.dataset.mode === state.timelineMode)); const ids = timelineKeys(state.timelineMode); const needsObject = state.timelineMode !== 'all'; refs.timelineObjectSelect.classList.toggle('hidden', !needsObject); refs.timelineObjectSelect.replaceChildren();
  if (needsObject) { if (!ids.includes(state.timelineObjectId)) state.timelineObjectId = ids[0] || null; for (const id of ids) { const option = text('option', timelineLabel(state.timelineMode, id)); option.value = id; option.selected = id === state.timelineObjectId; refs.timelineObjectSelect.append(option); } } else state.timelineObjectId = null;
  const timeline = state.metrics.timeline || {}; const block = state.timelineMode === 'all' ? { departures: timeline.departures_total, returns: timeline.returns_total } : (state.timelineMode === 'airport' ? timeline.by_airport : state.timelineMode === 'mission' ? timeline.by_mission : timeline.by_aircraft)?.[state.timelineObjectId];
  if (!block) { refs.timelineChart.replaceChildren(text('div', '当前维度没有对象', 'chart-empty')); return; }
  const label = state.timelineMode === 'all' ? '总体' : timelineLabel(state.timelineMode, state.timelineObjectId); renderLineChart(refs.timelineChart, [{ label: '出动', values: block.departures || [], stroke: '#49a5e9' }, { label: '返航', values: block.returns || [], stroke: '#68c88a' }], { contextLabel: label });
}
function renderChainTable() {
  refs.chainTableBody.replaceChildren(); const chains = [...(state.solution.sortie_chains || [])].sort((a, b) => a.depart_window - b.depart_window || a.origin_airport_id.localeCompare(b.origin_airport_id) || a.mission_id.localeCompare(b.mission_id));
  if (!chains.length) { const tr = document.createElement('tr'); const td = text('td', '当前 Solution 没有可展示航链', 'empty-cell'); td.colSpan = 8; tr.append(td); refs.chainTableBody.append(tr); return; }
  for (const chain of chains) { const tr = document.createElement('tr'); [airportDisplay(chain.origin_airport_id), missionName(chain.mission_id), airportDisplay(chain.return_airport_id), chain.aircraft_type, windowLabel(chain.depart_window), windowLabel(chain.return_window), windowLabel(chain.ready_window), integer(chain.sorties)].forEach((value) => tr.append(text('td', value))); refs.chainTableBody.append(tr); }
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
function renderResourceInventory() {
  const row = state.metrics.resources?.by_airport?.[state.supportAirportId]?.[state.supportResourceId], meta = state.metrics.resources?.resource_types?.[state.supportResourceId] || {}, windows = state.metrics.time_axis.windows; refs.resourceSubtitle.textContent = row ? `${airportDisplay(state.supportAirportId)} · ${resourceLabel(state.supportResourceId)}` : '无具体资源'; refs.resourceInventoryChart.replaceChildren();
  if (!row) { refs.resourceInventoryChart.append(text('div', '该机场没有资源库存记录', 'chart-empty')); return; }
  const series = [row.remaining, row.damage_adjusted_base_boundary].filter(Array.isArray); const values = series.flat().filter((value) => typeof value === 'number' && Number.isFinite(value)); if (!values.length) { refs.resourceInventoryChart.append(text('div', '库存时序未记录', 'chart-empty')); return; }
  const width = 760, height = 270, pad = 30, plotHeight = 202, maxValue = Math.max(1, ...values); const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none' });
  svg.append(svgElement('path', { d: stepPath(row.damage_adjusted_base_boundary || [], width, plotHeight, maxValue, pad), class: 'inventory-boundary', fill: 'none' })); svg.append(svgElement('path', { d: stepPath(row.remaining || [], width, plotHeight, maxValue, pad), class: 'inventory-stock', fill: 'none' }));
  const eventRows = [['replenishment_actual', 218, '补给', 'event-replenishment'], ['consumed_increment', 238, '消耗', 'event-consumption'], ['permanent_loss', 258, '永久损失', 'event-loss']];
  eventRows.forEach(([field, y, label, className]) => { const valuesForEvent = row[field]; if (!Array.isArray(valuesForEvent)) return; valuesForEvent.forEach((value, index) => { if (!(value > 0)) return; const x = pad + (valuesForEvent.length === 1 ? (width - 2 * pad) / 2 : index / (valuesForEvent.length - 1) * (width - 2 * pad)); const point = svgElement('circle', { cx: x, cy: y, r: 4, class: className }); const title = svgElement('title'); title.textContent = `${windowLabel(windows[index])} ${label} ${number(value)} ${meta.unit || ''}`; point.append(title); svg.append(point); }); });
  const missingLoss = !Array.isArray(row.permanent_loss); const note = svgElement('text', { x: pad, y: 14, class: 'inventory-note' }); note.textContent = missingLoss ? '永久损失：未记录（不以损毁调整边界代替）' : '永久损失使用正式逐窗记录'; svg.append(note); refs.resourceInventoryChart.append(svg);
}
function renderAircraftTurnaround() {
  const row = state.metrics.aircraft_inventory?.by_airport?.[state.supportAirportId]?.[state.supportAircraftTypeId]; refs.aircraftSubtitle.textContent = row ? `${airportDisplay(state.supportAirportId)} · ${state.supportAircraftTypeId}` : '无该机型';
  if (!row) { refs.aircraftTurnaroundChart.replaceChildren(text('div', '该机场没有航空器保有记录', 'chart-empty')); return; }
  renderLineChart(refs.aircraftTurnaroundChart, [{ label: '可用', values: row.available_before_departure || [], stroke: '#49a5e9' }, { label: '占用', values: row.in_use || [], stroke: '#e5a449' }, { label: '出动', values: row.departures || [], stroke: '#d46b70' }, { label: '再次可用', values: row.ready_releases || [], stroke: '#68c88a' }], { contextLabel: `${airportDisplay(state.supportAirportId)} · ${state.supportAircraftTypeId}` });
}
function renderSupport() { renderCapacityHeatmap(); renderResourceInventory(); renderAircraftTurnaround(); }
function bindInteractions() {
  refs.timelineModes.addEventListener('click', (event) => { const button = event.target.closest('button[data-mode]'); if (!button) return; state.timelineMode = button.dataset.mode; state.timelineObjectId = null; renderTimeline(); });
  refs.timelineObjectSelect.addEventListener('change', () => { state.timelineObjectId = refs.timelineObjectSelect.value || null; renderTimeline(); });
  refs.supportAirportSelect.addEventListener('change', () => { state.supportAirportId = refs.supportAirportSelect.value || null; state.supportResourceId = null; state.supportAircraftTypeId = null; populateDependentSupportControls(); renderSupport(); });
  refs.supportResourceSelect.addEventListener('change', () => { state.supportResourceId = refs.supportResourceSelect.value || null; renderResourceInventory(); });
  refs.supportAircraftSelect.addEventListener('change', () => { state.supportAircraftTypeId = refs.supportAircraftSelect.value || null; renderAircraftTurnaround(); });
  refs.openRuntimeButton.addEventListener('click', () => { window.location.href = `/runs/${encodeURIComponent(state.runId)}/runtime`; });
}
function renderAll() { renderBadges(); renderOverview(); renderTaskFulfillmentMatrix(); renderTimeline(); renderChainTable(); populateSupportControls(); renderSupport(); refs.loading.classList.add('hidden'); refs.content.classList.remove('hidden'); }
async function init() {
  bindInteractions(); if (!state.runId) { showError(new Error('missing run id')); return; }
  try {
    const run = await apiFetch(`/api/runs/${encodeURIComponent(state.runId)}`); if (run.status !== 'succeeded') throw new ApiError(`单次运行仪表盘仅支持成功 Run；当前状态=${run.status}`, { status: 409, code: 'RUN_NOT_SUCCEEDED' }); state.run = run; state.runConfig = run.run_config || {};
    const [situation, solution, metrics] = await Promise.all([apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/situation`), apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/solution`), apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/metrics`)]);
    if (solution.run_id !== state.runId || metrics.run_id !== state.runId) throw new Error('Run result identity mismatch'); state.situation = situation; state.solution = solution; state.metrics = metrics; buildIndexes(); renderAll();
  } catch (error) { showError(error); }
}
document.addEventListener('DOMContentLoaded', init);
