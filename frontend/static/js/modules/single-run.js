import { apiFetch, ApiError } from './api-client.js';

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
function missionName(id) {
  const mission = state.missionById.get(id);
  return mission ? `${mission.name}（${id}）` : id || '—';
}
function percent(ratio, digits = 1) {
  return typeof ratio === 'number' && Number.isFinite(ratio) ? `${(ratio * 100).toFixed(digits)}%` : '—';
}
function number(value, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
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
    [state.run.run_id, ''], ['成功', 'success'],
    [`情境 ${state.situation?.name || state.run.situation_id}`, ''],
    [`损毁场景 ${damage ? (damage.name || damage.damage_scenario_id) : (damageId || '无损毁')}`, ''],
    [`优化偏好 ${PREFERENCE_LABELS[state.runConfig?.preference_mode] || state.runConfig?.preference_mode || '—'}`, ''],
    [`组群 ${state.runConfig?.cluster_enabled ? `${state.runConfig.cluster_size} 个机场` : '未启用'}`, ''],
  ];
  for (const [value, cls] of values) refs.runBadges.append(text('span', value, `single-badge ${cls}`.trim()));
}

function renderSummary() {
  const m = state.metrics;
  const s = m.summary;
  const c = m.collaboration;
  const clusterEnabled = Boolean(state.runConfig?.cluster_enabled);
  refs.clusterPrimary.textContent = clusterEnabled ? `组选机场 ${s.selected_cluster_count} 个` : '未启用组选';
  refs.clusterMeta.innerHTML = `实际参与机场　${s.participating_airport_count} 个<br>核心机场　${s.core_airport_count} 个`;
  refs.clusterBadges.replaceChildren();
  if (c.selected_cluster.length) {
    for (const id of c.selected_cluster) refs.clusterBadges.append(text('span', airportName(id)));
  } else {
    refs.clusterBadges.append(text('span', '本 Run 无组选机场'));
  }

  refs.missionPrimary.textContent = `任务 ${s.mission_count} 项`;
  refs.missionMeta.innerHTML = `需求　${s.required_sorties_total} 架次<br>已调度　${s.scheduled_sorties_total} 架次<br>成功 Run：硬约束已满足`;

  refs.sortiePrimary.textContent = `出动 ${s.scheduled_sorties_total} 架次`;
  refs.sortieMeta.innerHTML = `返航　${s.returned_sorties_total} 架次<br>峰值出动量　${s.peak_departure_slot.sorties} 架次<br>峰值时段　${windowLabel(s.peak_departure_slot.window)}`;

  const maxAirport = s.max_airport_departure;
  refs.collaborationPrimary.textContent = `参与机场 ${s.participating_airport_count} 个`;
  refs.collaborationMeta.innerHTML = `最大承接　${airportName(maxAirport.airport_id)}<br>承接占比　${percent(maxAirport.share)}<br>集中度 HHI　${number(c.departure_hhi, 4)}`;

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

function renderLineChart(container, series, { ratioAxis = false } = {}) {
  container.replaceChildren();
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
  const windows = state.metrics.time_axis.windows || [];
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
  container.append(svg);
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
    { label: `${label} 出动`, values: block.departures || [], stroke: '#3f9fe7' },
    { label: `${label} 返航`, values: block.returns || [], stroke: '#65c987' },
  ]);
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

function renderSpatial() {
  refs.spatialChart.replaceChildren();
  const metrics = state.metrics;
  const selected = new Set(metrics.collaboration.selected_cluster || []);
  const participating = new Set(metrics.collaboration.participating_airports || []);
  const core = new Set(metrics.collaboration.core_airports || []);
  const airportIds = [...new Set([...selected, ...participating, ...core])].sort();
  const missionIds = [...new Set((state.solution.sortie_chains || []).map((x) => x.mission_id))].sort();
  const points = [];
  for (const id of airportIds) {
    const a = state.airportById.get(id);
    if (a) points.push({ type: 'airport', id, lon: Number(a.longitude), lat: Number(a.latitude) });
  }
  for (const id of missionIds) {
    const m = state.missionById.get(id);
    if (m) points.push({ type: 'mission', id, lon: Number(m.longitude), lat: Number(m.latitude) });
  }
  if (!points.length || points.some((p) => !Number.isFinite(p.lon) || !Number.isFinite(p.lat))) {
    refs.spatialChart.append(text('div', '冻结 Situation 缺少可展示的经纬坐标', 'chart-empty')); return;
  }
  const width = 520, height = 150, pad = 18;
  let lonMin = Math.min(...points.map((p) => p.lon)), lonMax = Math.max(...points.map((p) => p.lon));
  let latMin = Math.min(...points.map((p) => p.lat)), latMax = Math.max(...points.map((p) => p.lat));
  if (lonMax === lonMin) { lonMin -= .5; lonMax += .5; }
  if (latMax === latMin) { latMin -= .5; latMax += .5; }
  const project = (lon, lat) => ({
    x: pad + ((lon - lonMin) / (lonMax - lonMin)) * (width - pad * 2),
    y: height - pad - ((lat - latMin) / (latMax - latMin)) * (height - pad * 2),
  });
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}` });
  const pos = new Map(points.map((p) => [p.id, project(p.lon, p.lat)]));
  for (const chain of state.solution.sortie_chains || []) {
    const a = pos.get(chain.origin_airport_id), m = pos.get(chain.mission_id), r = pos.get(chain.return_airport_id);
    if (!a || !m || !r) continue;
    const poly = svgElement('polyline', { points: `${a.x},${a.y} ${m.x},${m.y} ${r.x},${r.y}`, fill: 'none', stroke: '#2e7bb7', 'stroke-width': '1.1', opacity: '.52', 'stroke-dasharray': '4 3' });
    const titleEl = svgElement('title'); titleEl.textContent = `${chain.path_id} · ${chain.sorties} 架次`; poly.append(titleEl); svg.append(poly);
  }
  for (const p of points) {
    const xy = pos.get(p.id);
    if (p.type === 'mission') {
      const rect = svgElement('rect', { x: xy.x - 3.5, y: xy.y - 3.5, width: 7, height: 7, fill: '#d7a552', stroke: '#f5d398', 'stroke-width': '.8' });
      const titleEl = svgElement('title'); titleEl.textContent = missionName(p.id); rect.append(titleEl); svg.append(rect);
    } else {
      const fill = core.has(p.id) ? '#f0b35d' : selected.has(p.id) ? '#3c9df2' : '#62c987';
      const circle = svgElement('circle', { cx: xy.x, cy: xy.y, r: core.has(p.id) ? 5 : 4, fill, stroke: '#dff2ff', 'stroke-width': '.7' });
      const titleEl = svgElement('title'); titleEl.textContent = `${airportName(p.id)} · ${core.has(p.id) ? '核心' : selected.has(p.id) ? '组选' : '参与'}`; circle.append(titleEl); svg.append(circle);
    }
  }
  refs.spatialChart.append(svg, text('span', '基于冻结经纬坐标生成的非比例结构示意；完整 GIS 在运行态势页展示', 'spatial-note'));
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
    appendCell(tr, airportName(id), id); appendCell(tr, row.departures_total); appendCell(tr, row.returns_total); appendCell(tr, percent(row.departure_share)); appendCell(tr, roleCell(row));
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
  grid.append(detailItem('机场', `${airportName(id)}（${id}）`), detailItem('出动架次', row.departures_total), detailItem('返航架次', row.returns_total), detailItem('承接占比', percent(row.departure_share)));
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
  grid.append(detailItem('Metrics Schema', state.metrics.schema_version), detailItem('Snapshot Hash', t.snapshot_hash), detailItem('Solver Status', t.solver_status ?? '—'), detailItem('Objective', t.objective ?? '—'), detailItem('Gap', t.gap ?? '—'), detailItem('Solve Time', t.solve_time_s != null ? `${t.solve_time_s}s` : '—'), detailItem('Algorithm Version', t.algorithm_version ?? '—'), detailItem('时间粒度', `${state.metrics.time_axis.slot_minutes} min/窗`)); root.append(grid);
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

function bindInteractions() {
  refs.timelineModes.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-mode]'); if (!button) return;
    state.timelineMode = button.dataset.mode; state.timelineObjectId = null; renderTimeline();
  });
  refs.timelineObjectSelect.addEventListener('change', () => { state.timelineObjectId = refs.timelineObjectSelect.value; renderTimeline(); });
  refs.detailTabs.addEventListener('click', (event) => { const button = event.target.closest('button[data-tab]'); if (button) renderDetail(button.dataset.tab); });
  refs.detailCloseButton.addEventListener('click', () => refs.detailDock.classList.remove('open'));
  refs.openRuntimeButton.addEventListener('click', () => { window.location.href = `/runs/${encodeURIComponent(state.runId)}/runtime`; });
}

function renderAll() {
  renderBadges(); renderSummary(); renderTimeline(); renderSpatial(); renderResourceTimeline(); renderStructureTables();
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
