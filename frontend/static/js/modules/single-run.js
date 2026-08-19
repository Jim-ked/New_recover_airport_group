import { apiFetch, ApiError } from './api-client.js';
import { formatDecimal, formatHhi, formatInteger, formatPercent, formatSeconds } from './number-display.js';

const PREFERENCE_LABELS = {
  sortie_max: '出动架次优先',
  resource_min: '资源消耗优先',
  time_min: '时间代价优先',
  custom: '自定义权重',
};
const RESOURCE_LABELS = { fuel: '燃油', material: '航材', munition: '航弹' };
const SVG_NS = 'http://www.w3.org/2000/svg';

const page = document.getElementById('singleRunPage');
const state = {
  runId: page?.dataset.runId || '',
  run: null,
  runConfig: null,
  situation: null,
  solution: null,
  metrics: null,
  airportById: new Map(),
  missionById: new Map(),
  timelineMode: 'all',
  timelineObjectId: null,
  singleAuxMode: 'spatial',
  singleBottomMode: 'airports',
  detailSelection: { airport: null, mission: null, aircraft: null, resource: 'fuel' },
};

const $ = (id) => document.getElementById(id);
const refs = {
  message: $('singleRunMessage'), loading: $('singleRunLoading'), content: $('singleRunContent'),
  runBadges: $('runBadges'), openRuntimeButton: $('openRuntimeButton'),
  clusterPrimary: $('clusterPrimary'), clusterMeta: $('clusterMeta'), clusterBadges: $('clusterBadges'),
  missionPrimary: $('missionPrimary'), missionMeta: $('missionMeta'),
  sortiePrimary: $('sortiePrimary'), sortieMeta: $('sortieMeta'),
  collaborationPrimary: $('collaborationPrimary'), collaborationMeta: $('collaborationMeta'),
  resourceSummary: $('resourceSummary'), timelineModes: $('timelineModes'), timelineObjectSelect: $('timelineObjectSelect'),
  timelineChart: $('timelineChart'), spatialChart: $('spatialChart'), resourceTimelineChart: $('resourceTimelineChart'),
  airportTableBody: $('airportTableBody'), missionTableBody: $('missionTableBody'), aircraftTableBody: $('aircraftTableBody'),
  airportCountLabel: $('airportCountLabel'), missionCountLabel: $('missionCountLabel'), aircraftCountLabel: $('aircraftCountLabel'),
  detailDock: $('detailDock'), detailTabs: $('detailTabs'), detailBody: $('detailBody'), detailCloseButton: $('detailCloseButton'),
  auxTabs: $('singleAuxTabs'), bottomTabs: $('singleBottomTabs'), technicalSummary: $('technicalSummary'),
};

function showError(error) {
  console.error(error);
  const message = error instanceof ApiError
    ? `${error.message}${error.field ? `（${error.field}）` : ''}`
    : '加载单次运行结果时发生未预期错误';
  refs.message.textContent = message;
  refs.message.classList.remove('hidden');
  refs.loading.classList.add('hidden');
  refs.content.classList.add('hidden');
}

function text(tag, value, className = '') {
  const el = document.createElement(tag);
  el.textContent = value ?? '—';
  if (className) el.className = className;
  return el;
}

function airportName(id) {
  return state.airportById.get(id)?.airport_name || id || '—';
}
function shortRunId(id) {
  const match = /^RUN-([a-f0-9]{8})/i.exec(String(id || ''));
  return match ? `R-${match[1].toUpperCase()}` : String(id || '—');
}
function airportNumber(id) {
  const match = /^AP(\d+)$/i.exec(String(id || ''));
  return match ? match[1].padStart(3, '0') : '';
}
function shortAirportName(id) {
  const name = airportName(id);
  return name.replace(/\s+(?:International\s+|General\s+)?(?:Airport|Air Base)$/i, '').trim() || name;
}
function airportDisplay(id) {
  const number = airportNumber(id);
  return `${number ? `${number} ` : ''}${shortAirportName(id)}`;
}
function missionName(id) {
  const mission = state.missionById.get(id);
  return mission?.name || id || '—';
}
function percent(ratio, digits = 1) {
  return formatPercent(ratio, { digits });
}
function number(value, digits = 2) {
  return formatDecimal(value, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function integer(value) {
  return formatInteger(value);
}
function windowLabel(window) {
  return Number.isInteger(window) ? `T${window}` : '—';
}

function buildIndexes() {
  state.airportById.clear();
  state.missionById.clear();
  for (const item of state.situation?.airports || []) {
    const airport = item.airport || {};
    if (airport.airport_id) state.airportById.set(airport.airport_id, airport);
  }
  for (const mission of state.situation?.missions || []) {
    if (mission.mission_id) state.missionById.set(mission.mission_id, mission);
  }
}

function renderBadges() {
  refs.runBadges.replaceChildren();
  const damageId = state.runConfig?.damage_scenario_id;
  const damage = (state.situation?.damage_scenarios || []).find((x) => x.damage_scenario_id === damageId);
  const values = [
    [shortRunId(state.run.run_id), ''], ['成功', 'success'],
    [state.situation?.name || state.run.situation_id, ''],
    [damage ? (damage.name || damage.damage_scenario_id) : (damageId || '无损毁'), ''],
    [PREFERENCE_LABELS[state.runConfig?.preference_mode] || state.runConfig?.preference_mode || '—', ''],
    [state.runConfig?.cluster_enabled ? `组群${state.runConfig.cluster_size}` : '未启用组群', ''],
  ];
  for (const [value, cls] of values) refs.runBadges.append(text('span', value, `single-badge ${cls}`.trim()));
}

function renderSummary() {
  const m = state.metrics;
  const s = m.summary;
  const c = m.collaboration;
  const clusterEnabled = Boolean(state.runConfig?.cluster_enabled);
  refs.clusterPrimary.textContent = clusterEnabled ? `组选 ${integer(s.selected_cluster_count)}` : '未启用组选';
  refs.clusterMeta.textContent = `参与 ${integer(s.participating_airport_count)} · 核心 ${integer(s.core_airport_count)}`;
  refs.clusterBadges.replaceChildren();
  const selectedCluster = Array.isArray(c.selected_cluster) ? c.selected_cluster : [];
  if (selectedCluster.length) {
    for (let index = 0; index < Math.min(2, selectedCluster.length); index += 1) {
      refs.clusterBadges.append(text('span', airportDisplay(selectedCluster[index])));
    }
    if (selectedCluster.length > 2) refs.clusterBadges.append(text('span', `另 ${selectedCluster.length - 2} 个`));
  } else {
    refs.clusterBadges.append(text('span', '本 Run 无组选机场'));
  }

  refs.missionPrimary.textContent = `任务 ${integer(s.mission_count)}`;
  refs.missionMeta.textContent = `需求 ${integer(s.required_sorties_total)} · 调度 ${integer(s.scheduled_sorties_total)}`;

  refs.sortiePrimary.textContent = `出动 ${integer(s.scheduled_sorties_total)}`;
  refs.sortieMeta.textContent = `返航 ${integer(s.returned_sorties_total)} · 峰值 ${integer(s.peak_departure_slot?.sorties)} · ${windowLabel(s.peak_departure_slot?.window)}`;

  const maxAirport = s.max_airport_departure || {};
  refs.collaborationPrimary.textContent = `参与 ${integer(s.participating_airport_count)}`;
  refs.collaborationMeta.innerHTML = `最大承接　${airportDisplay(maxAirport.airport_id)}<br>承接占比　${percent(maxAirport.share)}<br>集中度 HHI　${formatHhi(c.departure_hhi)}`;

  refs.resourceSummary.replaceChildren();
  const mins = m.resources?.category_min_remaining_ratio || {};
  for (const category of ['fuel', 'material', 'munition']) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'resource-summary-row';
    row.dataset.category = category;
    const label = text('span', RESOURCE_LABELS[category]);
    const bar = document.createElement('span'); bar.className = 'resource-bar';
    const fill = document.createElement('i'); bar.append(fill);
    const fact = mins[category];
    if (fact) {
      fill.style.width = `${Math.max(0, Math.min(100, fact.ratio * 100))}%`;
      row.append(label, bar, text('span', `最低 ${percent(fact.ratio)}`));
      row.title = `${airportName(fact.airport_id)} / ${fact.resource_type_id} / ${windowLabel(fact.window)}；分母=初始库存`;
    } else {
      fill.style.width = '0%';
      const missing = text('span', '无可比库存', 'missing');
      row.append(label, bar, missing);
    }
    row.addEventListener('click', () => openDetail('resource', category));
    refs.resourceSummary.append(row);
  }
}

function svgElement(name, attrs = {}) {
  const el = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  return el;
}

function pathFromSeries(values, width, height, maxValue, pad = 26) {
  if (!values.length) return '';
  const xSpan = Math.max(1, width - pad * 2);
  const ySpan = Math.max(1, height - pad * 2);
  let started = false;
  const parts = [];
  values.forEach((value, i) => {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      started = false;
      return;
    }
    const x = pad + (values.length === 1 ? xSpan / 2 : (i / (values.length - 1)) * xSpan);
    const y = height - pad - (value / maxValue) * ySpan;
    parts.push(`${started ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`);
    started = true;
  });
  return parts.join(' ');
}

function validateTimelineSeries(windows, series) {
  const invalid = '时序数据无效。';
  const mismatch = '时序长度与时间窗不一致。';
  if (!Array.isArray(windows) || !Array.isArray(series)) return { ok: false, message: invalid };
  const checked = [];
  for (const row of series) {
    if (!Array.isArray(row?.values)) return { ok: false, message: invalid };
    if (row.values.length !== windows.length) return { ok: false, message: mismatch };
    const values = [];
    for (const value of row.values) {
      if (value === null) { values.push(null); continue; }
      if (typeof value !== 'number' || !Number.isFinite(value)) return { ok: false, message: invalid };
      values.push(value);
    }
    checked.push({ ...row, values });
  }
  return { ok: true, series: checked };
}

function nearestWindowIndex(clientX, rect, count, pad, width) {
  if (count <= 1) return 0;
  const svgX = ((clientX - rect.left) / Math.max(1, rect.width)) * width;
  const ratio = Math.max(0, Math.min(1, (svgX - pad) / Math.max(1, width - pad * 2)));
  return Math.round(ratio * (count - 1));
}

function renderLineChart(container, series, { ratioAxis = false, contextLabel = null } = {}) {
  container.replaceChildren();
  const windows = state.metrics.time_axis.windows;
  const checked = validateTimelineSeries(windows, series);
  if (!checked.ok) { container.append(text('div', checked.message, 'chart-empty chart-error')); return; }
  series = checked.series;
  const valid = series.some((row) => row.values.some((v) => typeof v === 'number' && Number.isFinite(v)));
  if (!valid) { container.append(text('div', '当前维度没有可展示的时序数据', 'chart-empty')); return; }
  const width = 760, height = 250, pad = 28;
  const numericValues = series.flatMap((row) => row.values.filter((v) => typeof v === 'number' && Number.isFinite(v)));
  const maxValue = Math.max(ratioAxis ? 1 : 0, ...numericValues, ratioAxis ? 1 : 1);
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none' });
  const bg = svgElement('g', { stroke: '#294c64', 'stroke-width': '1', opacity: '.55' });
  for (let i = 0; i <= 4; i += 1) {
    const y = pad + ((height - pad * 2) / 4) * i;
    bg.append(svgElement('line', { x1: pad, y1: y, x2: width - pad, y2: y }));
  }
  svg.append(bg);
  svg.append(svgElement('line', { x1: pad, y1: height - pad, x2: width - pad, y2: height - pad, stroke: '#49677a' }));
  svg.append(svgElement('line', { x1: pad, y1: pad, x2: pad, y2: height - pad, stroke: '#49677a' }));

  for (const row of series) {
    const d = pathFromSeries(row.values, width, height, maxValue, pad);
    const path = svgElement('path', { d, fill: 'none', stroke: row.stroke, 'stroke-width': '2.4', 'vector-effect': 'non-scaling-stroke' });
    const titleEl = svgElement('title'); titleEl.textContent = row.label; path.append(titleEl);
    svg.append(path);
  }
  const hover = svgElement('g', { class: 'chart-hover-layer', visibility: 'hidden' });
  const hoverLine = svgElement('line', { class: 'chart-hover-line', y1: pad, y2: height - pad });
  hover.append(hoverLine);
  const hoverPoints = series.map((row) => {
    const point = svgElement('circle', { class: 'chart-hover-point', r: 3.6, fill: row.stroke });
    hover.append(point);
    return point;
  });
  svg.append(hover);
  const labels = windows.length > 2 ? [0, Math.floor((windows.length - 1) / 2), windows.length - 1] : windows.map((_, i) => i);
  for (const index of [...new Set(labels)]) {
    if (index < 0 || index >= windows.length) continue;
    const xSpan = width - pad * 2;
    const x = pad + (windows.length === 1 ? xSpan / 2 : (index / (windows.length - 1)) * xSpan);
    const t = svgElement('text', { x, y: height - 8, fill: '#708fa3', 'font-size': '9', 'text-anchor': 'middle' });
    t.textContent = windowLabel(windows[index]); svg.append(t);
  }
  const top = svgElement('text', { x: 5, y: pad + 4, fill: '#708fa3', 'font-size': '8' });
  top.textContent = ratioAxis ? percent(maxValue, 0) : String(maxValue); svg.append(top);
  const tooltip = text('div', '', 'chart-hover-tooltip');
  tooltip.hidden = true;
  const move = (event) => {
    const rect = svg.getBoundingClientRect();
    const index = nearestWindowIndex(event.clientX, rect, windows.length, pad, width);
    const xSpan = width - pad * 2;
    const x = pad + (windows.length === 1 ? xSpan / 2 : (index / (windows.length - 1)) * xSpan);
    hoverLine.setAttribute('x1', x); hoverLine.setAttribute('x2', x);
    hoverPoints.forEach((point, seriesIndex) => {
      const value = series[seriesIndex].values[index];
      const visible = typeof value === 'number' && Number.isFinite(value);
      point.setAttribute('visibility', visible ? 'visible' : 'hidden');
      if (!visible) return;
      point.setAttribute('cx', x);
      point.setAttribute('cy', height - pad - (value / maxValue) * (height - pad * 2));
    });
    hover.setAttribute('visibility', 'visible');
    const lines = [contextLabel, windowLabel(windows[index]), ...series.map((row) => {
      const value = row.values[index];
      const shown = typeof value === 'number' && Number.isFinite(value) ? (ratioAxis ? percent(value) : integer(value)) : '—';
      return `${row.shortLabel || row.label} ${shown}${ratioAxis ? '' : ' 架次'}`;
    })].filter(Boolean);
    tooltip.textContent = lines.join('\n'); tooltip.hidden = false;
    const containerRect = container.getBoundingClientRect();
    tooltip.style.left = `${Math.min(containerRect.width - 12, Math.max(8, event.clientX - containerRect.left + 10))}px`;
    tooltip.style.top = `${Math.max(8, event.clientY - containerRect.top - 12)}px`;
  };
  svg.addEventListener('pointermove', move);
  svg.addEventListener('pointerleave', () => { hover.setAttribute('visibility', 'hidden'); tooltip.hidden = true; });
  container.append(svg, tooltip);
}

function timelineKeys(mode) {
  if (mode === 'airport') return Object.keys(state.metrics.timeline.by_airport || {}).sort();
  if (mode === 'mission') return Object.keys(state.metrics.timeline.by_mission || {}).sort();
  if (mode === 'aircraft') return Object.keys(state.metrics.timeline.by_aircraft || {}).sort();
  return [];
}
function timelineLabel(mode, id) {
  if (mode === 'airport') return airportName(id);
  if (mode === 'mission') return missionName(id);
  return id;
}
function renderTimelineControls() {
  for (const button of refs.timelineModes.querySelectorAll('button')) button.classList.toggle('active', button.dataset.mode === state.timelineMode);
  const keys = timelineKeys(state.timelineMode);
  const needsSelect = state.timelineMode !== 'all';
  refs.timelineObjectSelect.classList.toggle('hidden', !needsSelect);
  refs.timelineObjectSelect.replaceChildren();
  if (needsSelect) {
    if (!keys.includes(state.timelineObjectId)) state.timelineObjectId = keys[0] || null;
    for (const id of keys) {
      const option = document.createElement('option'); option.value = id; option.textContent = timelineLabel(state.timelineMode, id);
      if (id === state.timelineObjectId) option.selected = true;
      refs.timelineObjectSelect.append(option);
    }
  } else state.timelineObjectId = null;
}
function renderTimeline() {
  renderTimelineControls();
  let block = null;
  let label = '全部任务';
  if (state.timelineMode === 'all') block = { departures: state.metrics.timeline.departures_total, returns: state.metrics.timeline.returns_total };
  else {
    const source = state.timelineMode === 'airport' ? state.metrics.timeline.by_airport : state.timelineMode === 'mission' ? state.metrics.timeline.by_mission : state.metrics.timeline.by_aircraft;
    block = source?.[state.timelineObjectId] || null;
    label = timelineLabel(state.timelineMode, state.timelineObjectId);
  }
  if (!block) { refs.timelineChart.replaceChildren(text('div', '当前维度没有对象', 'chart-empty')); return; }
  refs.timelineChart.setAttribute('aria-label', `${label}出动返航时序`);
  renderLineChart(refs.timelineChart, [
    { label: `${label} 出动`, shortLabel: '出动', values: block.departures || [], stroke: '#3f9fe7' },
    { label: `${label} 返航`, shortLabel: '返航', values: block.returns || [], stroke: '#65c987' },
  ], { contextLabel: state.timelineMode === 'all' ? null : label });
}

function renderResourceTimeline() {
  const rows = state.metrics.resources?.category_min_remaining_ratio_timeline || {};
  const data = [
    ['fuel', '#3f9fe7'], ['material', '#61b878'], ['munition', '#9a68d1'],
  ].map(([category, stroke]) => ({
    label: RESOURCE_LABELS[category], stroke,
    values: (rows[category] || []).map((item) => item?.ratio ?? null),
  }));
  renderLineChart(refs.resourceTimelineChart, data, { ratioAxis: true });
}

function aggregateTaskFlows(chains) {
  const outbound = new Map(), inbound = new Map();
  const add = (target, key, chain) => {
    const row = target.get(key) || { missionId: chain.mission_id, sorties: 0, aircraft: new Map(), chains: [] };
    row.sorties += chain.sorties; row.chains.push(chain);
    row.aircraft.set(chain.aircraft_type, (row.aircraft.get(chain.aircraft_type) || 0) + chain.sorties);
    target.set(key, row);
  };
  for (const chain of chains || []) {
    if (!chain?.origin_airport_id || !chain?.mission_id || !chain?.return_airport_id || typeof chain.sorties !== 'number') continue;
    add(outbound, `${chain.origin_airport_id}\u0000${chain.mission_id}`, chain);
    add(inbound, `${chain.mission_id}\u0000${chain.return_airport_id}`, chain);
  }
  for (const [key, row] of outbound) [row.originId, row.missionId] = key.split('\u0000');
  for (const [key, row] of inbound) [row.missionId, row.returnId] = key.split('\u0000');
  return { outbound: [...outbound.values()], inbound: [...inbound.values()] };
}
function flowRank(rows, field) {
  const totals = new Map();
  for (const row of rows) totals.set(row[field], (totals.get(row[field]) || 0) + row.sorties);
  return [...totals].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([id]) => id);
}
function flowY(index, count, height) { return count <= 1 ? height / 2 : 24 + index * ((height - 48) / (count - 1)); }
function flowWindowRange(chains, field) {
  const values = chains.map((row) => row[field]).filter(Number.isInteger);
  if (!values.length) return null;
  const lo = Math.min(...values), hi = Math.max(...values); return lo === hi ? windowLabel(lo) : `${windowLabel(lo)}–${windowLabel(hi)}`;
}
function flowAircraftLabel(row) { return [...row.aircraft].map(([id, sorties]) => `${id} ${sorties}`).join('、') || '—'; }

function renderSpatial() {
  refs.spatialChart.replaceChildren();
  const flows = aggregateTaskFlows(state.solution.sortie_chains || []);
  if (!flows.outbound.length && !flows.inbound.length) {
    refs.spatialChart.append(text('div', '当前 Run 没有可展示的任务流', 'chart-empty')); return;
  }
  const origins = flowRank(flows.outbound, 'originId');
  const missions = flowRank([...flows.outbound, ...flows.inbound], 'missionId');
  const returns = flowRank(flows.inbound, 'returnId');
  const width = 560, height = Math.max(180, Math.max(origins.length, missions.length, returns.length) * 30 + 34);
  const columns = { origin: 12, mission: 230, return: 424 };
  const nodeWidths = { origin: 124, mission: 104, return: 124 };
  const positions = {
    origin: new Map(origins.map((id, index) => [id, flowY(index, origins.length, height)])),
    mission: new Map(missions.map((id, index) => [id, flowY(index, missions.length, height)])),
    return: new Map(returns.map((id, index) => [id, flowY(index, returns.length, height)])),
  };
  const maxSorties = Math.max(1, ...flows.outbound.map((row) => row.sorties), ...flows.inbound.map((row) => row.sorties));
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, class: 'flow-diagram', 'aria-label': '出动机场到任务再到返航机场的聚合任务流' });
  const tooltip = text('div', '', 'flow-tooltip'); tooltip.hidden = true;
  const setHighlight = (missionId, active) => {
    for (const edge of svg.querySelectorAll('.flow-edge')) {
      edge.classList.toggle('related', active && edge.dataset.missionId === missionId);
      edge.classList.toggle('dimmed', active && edge.dataset.missionId !== missionId);
    }
  };
  const showFlowTooltip = (row, kind, event) => {
    const related = kind === 'outbound'
      ? [...new Set(flows.inbound.filter((item) => item.missionId === row.missionId).map((item) => airportDisplay(item.returnId)))]
      : [...new Set(flows.outbound.filter((item) => item.missionId === row.missionId).map((item) => airportDisplay(item.originId)))];
    const titleValue = kind === 'outbound'
      ? `${airportDisplay(row.originId)} → ${missionName(row.missionId)}`
      : `${missionName(row.missionId)} → ${airportDisplay(row.returnId)}`;
    tooltip.textContent = [titleValue, `${kind === 'outbound' ? '返航' : '出动'}关系：${related.join('、')}`, `架次：${integer(row.sorties)}`, `机型：${flowAircraftLabel(row)}`, `出动：${flowWindowRange(row.chains, 'depart_window') || '—'} · 返航：${flowWindowRange(row.chains, 'return_window') || '—'}`].join('\n');
    tooltip.hidden = false;
    const rect = refs.spatialChart.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(rect.width - 12, (event?.clientX || rect.left + rect.width / 2) - rect.left + 8))}px`;
    tooltip.style.top = `${Math.max(8, (event?.clientY || rect.top + rect.height / 2) - rect.top - 12)}px`;
  };
  const appendEdge = (row, kind) => {
    const outbound = kind === 'outbound';
    const x1 = outbound ? columns.origin + nodeWidths.origin : columns.mission + nodeWidths.mission;
    const x2 = outbound ? columns.mission : columns.return;
    const y1 = outbound ? positions.origin.get(row.originId) : positions.mission.get(row.missionId);
    const y2 = outbound ? positions.mission.get(row.missionId) : positions.return.get(row.returnId);
    const curve = Math.max(26, (x2 - x1) * .48);
    const edge = svgElement('path', {
      d: `M${x1} ${y1} C${x1 + curve} ${y1},${x2 - curve} ${y2},${x2} ${y2}`,
      class: `flow-edge flow-${kind}`, fill: 'none', tabindex: '0', role: 'button',
      'stroke-width': (1.2 + 3.8 * Math.sqrt(row.sorties / maxSorties)).toFixed(2),
      'data-mission-id': row.missionId,
    });
    const enter = (event) => { setHighlight(row.missionId, true); showFlowTooltip(row, kind, event); };
    const leave = () => { setHighlight(row.missionId, false); tooltip.hidden = true; };
    edge.addEventListener('pointerenter', enter); edge.addEventListener('pointermove', (event) => showFlowTooltip(row, kind, event)); edge.addEventListener('pointerleave', leave);
    edge.addEventListener('focus', enter); edge.addEventListener('blur', leave); edge.addEventListener('click', () => openDetail('mission', row.missionId));
    svg.append(edge);
  };
  flows.outbound.forEach((row) => appendEdge(row, 'outbound'));
  flows.inbound.forEach((row) => appendEdge(row, 'return'));

  const appendNode = (kind, id, label, openTab) => {
    const y = positions[kind].get(id), x = columns[kind], w = nodeWidths[kind];
    const group = svgElement('g', { class: `flow-node flow-${kind}`, tabindex: '0', role: 'button' });
    group.append(svgElement('rect', { x, y: y - 11, width: w, height: 22, rx: 3 }));
    const labelEl = svgElement('text', { x: x + 6, y: y + 3.5 }); labelEl.textContent = label; group.append(labelEl);
    const open = () => openDetail(openTab, id); group.addEventListener('click', open); group.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
    svg.append(group);
  };
  origins.forEach((id) => appendNode('origin', id, airportDisplay(id), 'airport'));
  missions.forEach((id) => appendNode('mission', id, missionName(id), 'mission'));
  returns.forEach((id) => appendNode('return', id, airportDisplay(id), 'airport'));
  refs.spatialChart.append(svg, tooltip);
}

function roleCell(row) {
  const wrap = document.createElement('div'); wrap.className = 'role-tags';
  const labels = [];
  if (row.is_core) labels.push('核心');
  if (row.is_selected_cluster) labels.push('组选');
  if (row.is_participating) labels.push('参与');
  if (!labels.length) labels.push('未承接');
  for (const label of labels) wrap.append(text('span', label, 'role-tag'));
  return wrap;
}
function appendCell(tr, value, titleValue = null) {
  const td = document.createElement('td');
  if (value instanceof Node) td.append(value); else td.textContent = value ?? '—';
  if (titleValue) td.title = titleValue;
  tr.append(td);
}
function renderStructureTables() {
  const airportRows = Object.entries(state.metrics.airports || {}).sort((a, b) => (b[1].departures_total - a[1].departures_total) || a[0].localeCompare(b[0]));
  refs.airportCountLabel.textContent = `全部 ${airportRows.length} 个机场`;
  refs.airportTableBody.replaceChildren();
  for (const [id, row] of airportRows) {
    const tr = document.createElement('tr'); tr.tabIndex = 0;
    appendCell(tr, airportDisplay(id), id); appendCell(tr, row.departures_total); appendCell(tr, row.returns_total); appendCell(tr, percent(row.departure_share)); appendCell(tr, roleCell(row));
    const open = () => openDetail('airport', id); tr.addEventListener('click', open); tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(); }); refs.airportTableBody.append(tr);
  }

  const missionRows = Object.entries(state.metrics.tasks || {}).sort((a, b) => a[0].localeCompare(b[0]));
  refs.missionCountLabel.textContent = `全部 ${missionRows.length} 项任务`;
  refs.missionTableBody.replaceChildren();
  for (const [id, row] of missionRows) {
    const tr = document.createElement('tr'); tr.tabIndex = 0;
    const origins = Object.keys(row.by_origin_airport || {}).map(airportName).join('、') || '—';
    appendCell(tr, state.missionById.get(id)?.name || id, id); appendCell(tr, row.required_total); appendCell(tr, row.scheduled_total); appendCell(tr, origins, origins);
    const open = () => openDetail('mission', id); tr.addEventListener('click', open); tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(); }); refs.missionTableBody.append(tr);
  }

  const aircraftRows = Object.entries(state.metrics.aircraft || {}).sort((a, b) => (b[1].scheduled_total - a[1].scheduled_total) || a[0].localeCompare(b[0]));
  refs.aircraftCountLabel.textContent = `全部 ${aircraftRows.length} 个机型`;
  refs.aircraftTableBody.replaceChildren();
  for (const [id, row] of aircraftRows) {
    const tr = document.createElement('tr'); tr.tabIndex = 0;
    const origins = Object.keys(row.by_origin_airport || {}).map(airportName).join('、') || '—';
    appendCell(tr, id); appendCell(tr, row.scheduled_total); appendCell(tr, percent(row.scheduled_share)); appendCell(tr, origins, origins);
    const open = () => openDetail('aircraft', id); tr.addEventListener('click', open); tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(); }); refs.aircraftTableBody.append(tr);
  }
}

function detailItem(label, value) {
  const div = document.createElement('div'); div.className = 'detail-item'; div.append(text('span', label), text('strong', value)); return div;
}
function detailSection(titleValue, children) {
  const section = document.createElement('section'); section.className = 'detail-section'; section.append(text('h3', titleValue)); const list = document.createElement('div'); list.className = 'detail-list';
  for (const child of children) list.append(child); section.append(list); return section;
}
function pill(value) { return text('span', value, 'detail-pill'); }
function renderAirportDetail(id) {
  const row = state.metrics.airports?.[id]; if (!row) return text('div', '没有机场结果');
  const root = document.createElement('div');
  const grid = document.createElement('div'); grid.className = 'detail-grid';
  grid.append(detailItem('机场', airportDisplay(id)), detailItem('出动架次', row.departures_total), detailItem('返航架次', row.returns_total), detailItem('承接占比', percent(row.departure_share)));
  root.append(grid);
  const flags = [row.is_core && '核心机场', row.is_selected_cluster && '组选机场', row.is_participating && '实际参与'].filter(Boolean);
  root.append(detailSection('运行角色', (flags.length ? flags : ['未承接任务']).map(pill)));
  const windows = state.metrics.time_axis.windows;
  const cap = row.capacity || {};
  const series = windows.map((w, i) => `${windowLabel(w)}: 可用=${cap.available?.[i] ?? '—'}, 起飞占用=${cap.used_departure?.[i] ?? '—'}, 到达占用=${cap.used_arrival?.[i] ?? '—'}, 利用率=${percent(cap.utilization?.[i])}`).join('　');
  root.append(detailSection('容量时序（逐窗事实）', [text('span', series, 'detail-series')]));
  return root;
}
function renderMissionDetail(id) {
  const row = state.metrics.tasks?.[id]; if (!row) return text('div', '没有任务结果');
  const root = document.createElement('div'); const grid = document.createElement('div'); grid.className = 'detail-grid';
  grid.append(detailItem('任务', missionName(id)), detailItem('需求架次', row.required_total), detailItem('已调度架次', row.scheduled_total), detailItem('任务窗', `${windowLabel(state.missionById.get(id)?.window_start_slot)}–${windowLabel(state.missionById.get(id)?.window_end_slot)} [start,end)`)); root.append(grid);
  root.append(detailSection('需求机型', Object.entries(row.required_by_aircraft || {}).map(([f, q]) => pill(`${f}: ${q}`))));
  root.append(detailSection('实际调度机型', Object.entries(row.scheduled_by_aircraft || {}).map(([f, q]) => pill(`${f}: ${q}`))));
  root.append(detailSection('承接机场', Object.entries(row.by_origin_airport || {}).map(([aid, q]) => pill(`${airportName(aid)}: ${q} 架次`))));
  return root;
}
function renderAircraftDetail(id) {
  const row = state.metrics.aircraft?.[id]; if (!row) return text('div', '没有机型结果');
  const root = document.createElement('div'); const grid = document.createElement('div'); grid.className = 'detail-grid';
  grid.append(detailItem('机型', id), detailItem('投入架次', row.scheduled_total), detailItem('投入占比', percent(row.scheduled_share)), detailItem('状态模型', state.metrics.aircraft_inventory?.state_model || '—')); root.append(grid);
  root.append(detailSection('出动机场', Object.entries(row.by_origin_airport || {}).map(([aid, q]) => pill(`${airportName(aid)}: ${q} 架次`))));
  const inventory = [];
  for (const [aid, types] of Object.entries(state.metrics.aircraft_inventory?.by_airport || {})) {
    const inv = types?.[id]; if (!inv) continue;
    inventory.push(pill(`${airportName(aid)} 初始=${inv.baseline_initial_quantity}`));
  }
  root.append(detailSection('冻结保有基线', inventory.length ? inventory : [pill('无该机型保有记录')]));
  return root;
}
function renderResourceDetail(category) {
  const root = document.createElement('div'); const fact = state.metrics.resources?.category_min_remaining_ratio?.[category];
  const grid = document.createElement('div'); grid.className = 'detail-grid';
  grid.append(detailItem('类别', RESOURCE_LABELS[category] || category), detailItem('最低余量率', fact ? percent(fact.ratio) : '—'), detailItem('限制机场', fact ? airportName(fact.airport_id) : '—'), detailItem('发生窗口', fact ? windowLabel(fact.window) : '—')); root.append(grid);
  if (fact) root.append(detailSection('最低点追溯', [pill(`资源 ${fact.resource_type_id}`), pill('范围：实际参与机场'), pill('分母：初始库存')]));
  const meta = state.metrics.resources?.resource_types || {};
  const ids = Object.entries(meta).filter(([, item]) => item.category === category).map(([rid, item]) => pill(`${item.name || rid}（${rid}，${item.unit || '单位未标注'}）`));
  root.append(detailSection('该类别资源类型', ids.length ? ids : [pill('无资源类型')]));
  return root;
}
function renderTechnicalDetail() {
  const t = state.metrics.technical || {}; const root = document.createElement('div'); const grid = document.createElement('div'); grid.className = 'detail-grid';
  grid.append(detailItem('完整 Run ID', state.runId), detailItem('Metrics Schema', state.metrics.schema_version), detailItem('Snapshot Hash', t.snapshot_hash), detailItem('Solver Status', t.solver_status ?? '—'), detailItem('Objective', formatDecimal(t.objective)), detailItem('Gap', formatPercent(t.gap, { digits: 2 })), detailItem('Solve Time', formatSeconds(t.solve_time_s)), detailItem('Algorithm Version', t.algorithm_version ?? '—'), detailItem('时间粒度', `${state.metrics.time_axis.slot_minutes} min/窗`)); root.append(grid);
  const section = document.createElement('section'); section.className = 'detail-section'; section.append(text('h3', '冻结 RunConfig'));
  const pre = text('pre', JSON.stringify(state.runConfig, null, 2), 'detail-series'); section.append(pre); root.append(section); return root;
}
function defaultDetailId(tab) {
  if (tab === 'airport') return Object.keys(state.metrics.airports || {}).sort()[0] || null;
  if (tab === 'mission') return Object.keys(state.metrics.tasks || {}).sort()[0] || null;
  if (tab === 'aircraft') return Object.keys(state.metrics.aircraft || {}).sort()[0] || null;
  if (tab === 'resource') return 'fuel';
  return null;
}
function renderDetail(tab) {
  for (const button of refs.detailTabs.querySelectorAll('[data-tab]')) button.classList.toggle('active', button.dataset.tab === tab);
  const id = state.detailSelection[tab] || defaultDetailId(tab);
  if (tab !== 'technical') state.detailSelection[tab] = id;
  let body;
  if (tab === 'airport') body = renderAirportDetail(id);
  else if (tab === 'mission') body = renderMissionDetail(id);
  else if (tab === 'aircraft') body = renderAircraftDetail(id);
  else if (tab === 'resource') body = renderResourceDetail(id);
  else body = renderTechnicalDetail();
  refs.detailBody.replaceChildren(body);
}
function openDetail(tab, id = null) {
  if (id) state.detailSelection[tab] = id;
  refs.detailDock.classList.add('open');
  renderDetail(tab);
}

function renderWorkspaceTabs() {
  for (const button of refs.auxTabs.querySelectorAll('[data-aux-mode]')) {
    button.classList.toggle('active', button.dataset.auxMode === state.singleAuxMode);
  }
  document.querySelectorAll('[data-aux-panel]').forEach((panel) => {
    panel.classList.toggle('hidden', panel.dataset.auxPanel !== state.singleAuxMode);
  });
  for (const button of refs.bottomTabs.querySelectorAll('[data-bottom-mode]')) {
    button.classList.toggle('active', button.dataset.bottomMode === state.singleBottomMode);
  }
  document.querySelectorAll('[data-bottom-panel]').forEach((panel) => {
    panel.classList.toggle('hidden', panel.dataset.bottomPanel !== state.singleBottomMode);
  });
}

function renderTechnicalSummary() {
  const technical = state.metrics.technical || {};
  const facts = [
    ['Metrics Schema', state.metrics.schema_version],
    ['Snapshot Hash', technical.snapshot_hash],
    ['Solver Status', technical.solver_status],
    ['Objective', formatDecimal(technical.objective)],
    ['Gap', formatPercent(technical.gap, { digits: 2 })],
    ['Solve Time', formatSeconds(technical.solve_time_s)],
    ['Algorithm Version', technical.algorithm_version],
    ['时间粒度', Number.isInteger(state.metrics.time_axis.slot_minutes) ? `${state.metrics.time_axis.slot_minutes} min/窗` : null],
  ];
  refs.technicalSummary.replaceChildren(...facts.map(([label, value]) => detailItem(label, value ?? '—')));
}

function bindInteractions() {
  refs.timelineModes.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-mode]'); if (!button) return;
    state.timelineMode = button.dataset.mode; state.timelineObjectId = null; renderTimeline();
  });
  refs.timelineObjectSelect.addEventListener('change', () => { state.timelineObjectId = refs.timelineObjectSelect.value; renderTimeline(); });
  refs.detailTabs.addEventListener('click', (event) => { const button = event.target.closest('button[data-tab]'); if (button) renderDetail(button.dataset.tab); });
  refs.detailCloseButton.addEventListener('click', () => refs.detailDock.classList.remove('open'));
  refs.openRuntimeButton.addEventListener('click', () => { window.location.href = `/runs/${encodeURIComponent(state.runId)}/runtime`; });
  refs.auxTabs.addEventListener('click', (event) => {
    const button = event.target.closest('[data-aux-mode]'); if (!button) return;
    state.singleAuxMode = button.dataset.auxMode; renderWorkspaceTabs();
  });
  refs.bottomTabs.addEventListener('click', (event) => {
    const button = event.target.closest('[data-bottom-mode]'); if (!button) return;
    state.singleBottomMode = button.dataset.bottomMode; renderWorkspaceTabs();
  });
}

function renderAll() {
  renderBadges(); renderSummary(); renderTimeline(); renderSpatial(); renderResourceTimeline(); renderStructureTables(); renderTechnicalSummary(); renderWorkspaceTabs();
  refs.loading.classList.add('hidden'); refs.content.classList.remove('hidden');
}

async function init() {
  bindInteractions();
  if (!state.runId) { showError(new Error('missing run id')); return; }
  try {
    const run = await apiFetch(`/api/runs/${encodeURIComponent(state.runId)}`);
    if (run.status !== 'succeeded') throw new ApiError(`单次运行仪表盘仅支持成功 Run；当前状态=${run.status}`, { status: 409, code: 'RUN_NOT_SUCCEEDED' });
    state.run = run; state.runConfig = run.run_config || {};
    const [situation, solution, metrics] = await Promise.all([
      apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/situation`),
      apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/solution`),
      apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/metrics`),
    ]);
    if (solution.run_id !== state.runId || metrics.run_id !== state.runId) throw new Error('Run result identity mismatch');
    state.situation = situation; state.solution = solution; state.metrics = metrics;
    buildIndexes(); renderAll();
  } catch (error) { showError(error); }
}

document.addEventListener('DOMContentLoaded', init);
