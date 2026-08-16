import { apiFetch, ApiError } from './api-client.js';

const page = document.getElementById('runtimePage');
const state = {
  runId: page?.dataset.runId || '', runtime: null, metrics: null, run: null,
  frameIndex: 0, playing: false, timer: null, map: null, playbackMs: 900, trailWindows: 1,
  markers: new Map(), outboundLayers: [], returnLayers: [], routeById: new Map(),
  selected: { type: null, id: null }, detailTab: 'airport',
};
const $ = (id) => document.getElementById(id);
const refs = {
  map: $('runtimeMap'), kernel: $('runtimeKernelMessage'), badges: $('runtimeBadges'),
  inspector: $('runtimeInspectorBody'), inspectorWindow: $('inspectorWindow'), slider: $('runtimeSlider'),
  prev: $('runtimePrevButton'), next: $('runtimeNextButton'), play: $('runtimePlayButton'),
  windowLabel: $('runtimeWindowLabel'), windowFacts: $('runtimeWindowFacts'), damageStrip: $('runtimeDamageStrip'),
  controls: document.querySelector('.runtime-controls'), fit: $('fitRuntimeButton'), fitAll: $('fitAllRuntimeButton'), speed: $('runtimeSpeed'), trail: $('runtimeTrail'), error: $('runtimeError'),
  dock: $('runtimeDetailDock'), tabs: $('runtimeDetailTabs'), dockBody: $('runtimeDetailBody'), dockClose: $('runtimeDetailClose'),
};

function showError(error) {
  console.error(error);
  refs.error.textContent = error instanceof ApiError ? error.message : '运行态势加载失败';
  refs.error.classList.remove('hidden');
}
function escapeHtml(value) { return String(value ?? '—').replace(/[&<>'"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function percent(v) { return typeof v === 'number' && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : '—'; }
function windowLabel(v) { return Number.isInteger(v) ? `T${v}` : '—'; }
function frame() { return state.runtime?.frames?.[state.frameIndex] || null; }
function airport(id) { return state.runtime.airports.find((x) => x.airport_id === id); }
function mission(id) { return state.runtime.missions.find((x) => x.mission_id === id); }
function airportName(id) { return airport(id)?.airport_name || id; }
function missionName(id) { const m = mission(id); return m ? `${m.name}（${id}）` : id; }

function loadStyle(href) {
  if ([...document.styleSheets].some((x) => x.href && x.href.includes(href))) return Promise.resolve();
  const el = document.createElement('link'); el.rel = 'stylesheet'; el.href = href; document.head.append(el); return Promise.resolve();
}
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const existing = [...document.scripts].find((x) => x.src && x.src.includes(src));
    if (existing) { if (globalThis.L) resolve(); else existing.addEventListener('load', resolve, { once: true }); return; }
    const el = document.createElement('script'); el.src = src; el.onload = resolve; el.onerror = reject; document.head.append(el);
  });
}
async function ensureLeaflet() {
  if (globalThis.L) return true;
  try {
    await loadStyle('/static/vendor/leaflet/leaflet.css');
    await loadScript('/static/vendor/leaflet/leaflet.js');
    return Boolean(globalThis.L);
  } catch (_) { return false; }
}

function mapIcon(kind, item, damaged = false) {
  const L = globalThis.L;
  if (kind === 'mission') return L.divIcon({ className: 'runtime-mission-marker', html: '<span></span>', iconSize: [14,14], iconAnchor: [7,7] });
  const classes = ['runtime-airport-marker'];
  if (item.is_selected_cluster && layerEnabled('selected')) classes.push('selected');
  if (item.is_participating && layerEnabled('participating')) classes.push('participating');
  if (item.is_core && layerEnabled('core')) classes.push('core');
  if (damaged) classes.push('damage');
  return L.divIcon({ className: classes.join(' '), html: '<span></span>', iconSize: [18,18], iconAnchor: [9,9] });
}
function layerEnabled(name) { return refs.controls.querySelector(`[data-layer="${name}"]`)?.checked !== false; }
function shouldShowAirport(_item) { return layerEnabled('airports'); }
function clearMapLayers() {
  for (const marker of state.markers.values()) marker.remove();
  state.markers.clear();
  for (const layer of [...state.outboundLayers, ...state.returnLayers]) layer.remove();
  state.outboundLayers = []; state.returnLayers = [];
}
function stableLane(id) {
  let hash = 0;
  for (const ch of String(id || '')) hash = ((hash * 31) + ch.charCodeAt(0)) | 0;
  const lane = Math.abs(hash) % 5 - 2;
  return lane === 0 ? 1 : lane;
}
function quadraticLeg(start, end, pathId, direction = 1) {
  const [lat1, lon1] = start, [lat2, lon2] = end;
  const dx = lon2 - lon1, dy = lat2 - lat1;
  const length = Math.hypot(dx, dy) || 1;
  const lane = stableLane(pathId);
  const sign = lane < 0 ? -1 : 1;
  const curve = (0.065 + Math.abs(lane) * 0.012) * direction * sign;
  const mx = (lon1 + lon2) / 2, my = (lat1 + lat2) / 2;
  const cx = mx + (-dy / length) * length * curve;
  const cy = my + (dx / length) * length * curve;
  const points = [];
  for (let i = 0; i <= 18; i += 1) {
    const t = i / 18, u = 1 - t;
    const lon = u*u*lon1 + 2*u*t*cx + t*t*lon2;
    const lat = u*u*lat1 + 2*u*t*cy + t*t*lat2;
    points.push([lat, lon]);
  }
  return points;
}
function activitySets() {
  const end = state.frameIndex;
  const start = state.trailWindows === Infinity ? 0 : Math.max(0, end - state.trailWindows + 1);
  const departures = new Set(), returns = new Set();
  for (let i = start; i <= end; i += 1) {
    const item = state.runtime?.frames?.[i];
    for (const row of item?.departures || []) departures.add(row.path_id);
    for (const row of item?.returns || []) returns.add(row.path_id);
  }
  return { departures, returns };
}
function routeOptions({ color, active, kind }) {
  return {
    color, weight: active ? 3.4 : 1.25, opacity: active ? .92 : .28,
    dashArray: active ? '9 7' : '2 5',
    className: `runtime-route runtime-route-${kind}${active ? ' active-route' : ''}`,
  };
}

function drawMap() {
  if (!state.map || !globalThis.L) return;
  clearMapLayers();
  const L = globalThis.L; const f = frame();
  const damagedAirports = new Set((f?.damage_events || []).map((x) => x.airport_id));
  for (const item of state.runtime.airports) {
    if (!shouldShowAirport(item)) continue;
    if (!Number.isFinite(item.latitude) || !Number.isFinite(item.longitude)) continue;
    const marker = L.marker([item.latitude, item.longitude], { icon: mapIcon('airport', item, layerEnabled('damage') && damagedAirports.has(item.airport_id)) });
    marker.on('click', () => selectObject('airport', item.airport_id)); marker.addTo(state.map); state.markers.set(`airport:${item.airport_id}`, marker);
  }
  if (layerEnabled('missions')) for (const item of state.runtime.missions) {
    if (!Number.isFinite(item.latitude) || !Number.isFinite(item.longitude)) continue;
    const marker = L.marker([item.latitude, item.longitude], { icon: mapIcon('mission', item) });
    marker.on('click', () => selectObject('mission', item.mission_id)); marker.addTo(state.map); state.markers.set(`mission:${item.mission_id}`, marker);
  }
  if (!layerEnabled('routes')) return;
  const { departures: departing, returns: returning } = activitySets();
  for (const route of state.runtime.routes) {
    const a = airport(route.origin_airport_id), m = mission(route.mission_id), r = airport(route.return_airport_id);
    if (!a || !m || !r) continue;
    if (layerEnabled('outbound')) {
      const line = L.polyline(quadraticLeg([a.latitude,a.longitude],[m.latitude,m.longitude],route.path_id,1), routeOptions({ color:'#42a6f4', active:departing.has(route.path_id), kind:'outbound' }));
      line.on('click', () => selectObject('route', route.path_id)); line.addTo(state.map); state.outboundLayers.push(line);
    }
    if (layerEnabled('return')) {
      const line = L.polyline(quadraticLeg([m.latitude,m.longitude],[r.latitude,r.longitude],route.path_id,1), routeOptions({ color:'#65c987', active:returning.has(route.path_id), kind:'return' }));
      line.on('click', () => selectObject('route', route.path_id)); line.addTo(state.map); state.returnLayers.push(line);
    }
  }
}
function fitMap(scope = 'run') {
  if (!state.map || !globalThis.L) return;
  const allAirports = state.runtime.airports || [];
  const runAirports = allAirports.filter((x) => x.is_participating || x.is_selected_cluster || x.is_core);
  const airports = scope === 'all' || !runAirports.length ? allAirports : runAirports;
  const coords = [...airports, ...(state.runtime.missions || [])]
    .filter((x) => Number.isFinite(x.latitude) && Number.isFinite(x.longitude))
    .map((x) => [x.latitude, x.longitude]);
  if (coords.length) state.map.fitBounds(globalThis.L.latLngBounds(coords).pad(.12));
}
async function initMap() {
  if (!(await ensureLeaflet())) {
    refs.kernel.textContent = 'Leaflet 本地地图内核尚未装载。运行态势数据、时间窗与详情已就绪；前端构建阶段接入 /static/vendor/leaflet 与离线瓦片后即可显示正式地图。';
    refs.kernel.classList.remove('hidden'); return;
  }
  const L = globalThis.L; state.map = L.map(refs.map, { zoomControl: true, attributionControl: false, preferCanvas: false });
  const tileTemplate = page.dataset.tileTemplate;
  if (tileTemplate) L.tileLayer(tileTemplate, { maxZoom: 12, minZoom: 2, noWrap: true }).addTo(state.map);
  drawMap(); fitMap('run');
}

function renderBadges() {
  const damage = state.runtime.damage_scenario;
  refs.badges.innerHTML = [
    `<span>${escapeHtml(state.runId)}</span>`, `<span>${escapeHtml(state.run?.situation?.name || state.run?.situation?.situation_id)}</span>`,
    `<span>${damage ? `损毁 ${escapeHtml(damage.name || damage.damage_scenario_id)}` : '无损毁'}</span>`,
    `<span>${state.runtime.time_axis.slot_minutes} min/窗</span>`,
  ].join('');
}
function currentRoute(pathId) { return state.routeById.get(pathId); }
function renderFrame() {
  const f = frame(); if (!f) return;
  refs.slider.value = String(state.frameIndex); refs.windowLabel.textContent = windowLabel(f.window);
  refs.windowFacts.textContent = `出动 ${f.departures_total} 架次 · 返航 ${f.returns_total} 架次`;
  refs.inspectorWindow.textContent = windowLabel(f.window);
  const events = f.damage_events || [];
  refs.damageStrip.textContent = events.length ? events.map((e) => `${e.event_id} ${e.damage_type} / ${e.phase} / ${airportName(e.airport_id)}`).join('　') : '当前时间窗无有效损毁状态';
  drawMap(); renderInspector(); if (state.dock.classList.contains('open')) renderDetail(state.detailTab);
}
function setFrameIndex(index) { state.frameIndex = Math.max(0, Math.min(state.runtime.frames.length - 1, Number(index) || 0)); renderFrame(); }
function togglePlay() {
  state.playing = !state.playing; refs.play.textContent = state.playing ? '暂停' : '播放';
  if (state.timer) clearInterval(state.timer); state.timer = null;
  if (state.playing) state.timer = setInterval(() => { if (state.frameIndex >= state.runtime.frames.length - 1) { state.playing=false; refs.play.textContent='播放'; clearInterval(state.timer); state.timer=null; return; } setFrameIndex(state.frameIndex + 1); }, state.playbackMs);
}

function inspectorRows(rows) { return `<div class="inspector-grid">${rows.map(([k,v]) => `<b>${escapeHtml(k)}</b><span>${escapeHtml(v)}</span>`).join('')}</div>`; }
function renderInspector() {
  const f = frame();
  if (!state.selected.type) { refs.inspector.innerHTML = `当前窗 ${windowLabel(f.window)}：出动 ${f.departures_total}，返航 ${f.returns_total}。点击地图对象查看冻结事实。`; return; }
  if (state.selected.type === 'airport') {
    const id = state.selected.id, a = airport(id), row = f.airports[id] || {};
    refs.inspector.innerHTML = inspectorRows([['机场',airportName(id)],['角色',[a.is_core?'核心':null,a.is_selected_cluster?'组选':null,a.is_participating?'参与':null].filter(Boolean).join(' / ')||'未参与'],['可用容量',row.capacity_available],['出动占用',row.capacity_used_departure],['返航占用',row.capacity_used_arrival],['容量利用率',percent(row.capacity_utilization)],['离场附加延迟',`${row.departure_delay_slots || 0} 窗`],['返航附加延迟',`${row.return_delay_slots || 0} 窗`],['损毁事件',(row.damage_event_ids||[]).join(', ')||'无']]);
  } else if (state.selected.type === 'mission') {
    const m = mission(state.selected.id); refs.inspector.innerHTML = inspectorRows([['任务',missionName(m.mission_id)],['任务窗',`${windowLabel(m.window_start_slot)}–${windowLabel(m.window_end_slot)} [start,end)`],['当前窗',windowLabel(f.window)]]);
  } else {
    const r = currentRoute(state.selected.id); refs.inspector.innerHTML = inspectorRows([['Path ID',r.path_id],['出发',airportName(r.origin_airport_id)],['任务',missionName(r.mission_id)],['返场',airportName(r.return_airport_id)],['机型',r.aircraft_type],['出动窗',windowLabel(r.depart_window)],['返航窗',windowLabel(r.return_window)],['Ready',windowLabel(r.ready_window)],['架次',r.sorties]]);
  }
}
function selectObject(type, id) { state.selected = { type, id }; renderInspector(); if (type === 'airport') openDock('airport'); else if (type === 'mission') openDock('mission'); else openDock('technical'); }

function detailItems(items) { return `<div class="runtime-detail-grid">${items.map(([k,v]) => `<div class="runtime-detail-item"><small>${escapeHtml(k)}</small><strong>${escapeHtml(v)}</strong></div>`).join('')}</div>`; }
function pillRows(items) { return `<div class="runtime-pills">${items.map((x) => `<span class="runtime-pill">${escapeHtml(x)}</span>`).join('')}</div>`; }
function selectedAirportId() { return state.selected.type === 'airport' ? state.selected.id : (state.runtime.participating_airports[0] || state.runtime.airports[0]?.airport_id); }
function selectedMissionId() { return state.selected.type === 'mission' ? state.selected.id : (state.runtime.missions[0]?.mission_id); }
function renderDetail(tab) {
  state.detailTab = tab; for (const b of refs.tabs.querySelectorAll('[data-tab]')) b.classList.toggle('active', b.dataset.tab === tab);
  const f = frame(); let html = '';
  if (tab === 'airport') {
    const id = selectedAirportId(), row = state.metrics.airports?.[id], current = f.airports[id] || {};
    html = detailItems([['机场',airportName(id)],['累计出动',row?.departures_total],['累计返航',row?.returns_total],['承接占比',percent(row?.departure_share)],['当前容量',current.capacity_available],['当前利用率',percent(current.capacity_utilization)]]) + pillRows([row?.is_core?'核心机场':null,row?.is_selected_cluster?'最终组群':null,row?.is_participating?'实际参与':null].filter(Boolean));
  } else if (tab === 'mission') {
    const id = selectedMissionId(), row = state.metrics.tasks?.[id], m = mission(id);
    html = detailItems([['任务',missionName(id)],['需求架次',row?.required_total],['已调度',row?.scheduled_total],['任务窗',`${windowLabel(m?.window_start_slot)}–${windowLabel(m?.window_end_slot)}`]]) + pillRows(Object.entries(row?.by_origin_airport || {}).map(([aid,q]) => `${airportName(aid)} ${q} 架次`));
  } else if (tab === 'aircraft') {
    html = Object.entries(state.metrics.aircraft || {}).map(([id,row]) => detailItems([['机型',id],['投入架次',row.scheduled_total],['投入占比',percent(row.scheduled_share)],['状态模型',state.metrics.aircraft_inventory?.state_model || '—']])).join('');
  } else if (tab === 'resource') {
    const aid = selectedAirportId(), rows = f.airports[aid]?.resources || {}, meta = state.metrics.resources?.resource_types || {};
    html = `<strong>${escapeHtml(airportName(aid))} · ${windowLabel(f.window)}</strong>` + pillRows(Object.entries(rows).map(([rid,row]) => `${meta[rid]?.name || rid}: 余量 ${row.remaining} ${meta[rid]?.unit || ''} / 初始比 ${percent(row.remaining_ratio_initial)}`));
  } else {
    const route = state.selected.type === 'route' ? currentRoute(state.selected.id) : null;
    html = detailItems([['Runtime Schema',state.runtime.schema_version],['Metrics Schema',state.metrics.schema_version],['Snapshot Hash',state.metrics.technical?.snapshot_hash],['当前窗',windowLabel(f.window)],['当前 Path',route?.path_id || '—'],['Solver',state.metrics.technical?.solver_status || '—']]);
  }
  refs.dockBody.innerHTML = html || '当前没有可展示事实';
}
function openDock(tab) { state.dock.classList.add('open'); page.classList.add('detail-open'); renderDetail(tab); }

function bind() {
  refs.slider.addEventListener('input', () => setFrameIndex(refs.slider.value)); refs.prev.addEventListener('click', () => setFrameIndex(state.frameIndex - 1)); refs.next.addEventListener('click', () => setFrameIndex(state.frameIndex + 1)); refs.play.addEventListener('click', togglePlay);
  refs.controls.addEventListener('change', (event) => { if (event.target === refs.speed || event.target === refs.trail) return; drawMap(); }); refs.fit.addEventListener('click', () => fitMap('run')); refs.fitAll.addEventListener('click', () => fitMap('all'));
  refs.speed.addEventListener('change', () => { state.playbackMs = Number(refs.speed.value) || 900; if (state.playing) { togglePlay(); togglePlay(); } });
  refs.trail.addEventListener('change', () => { state.trailWindows = refs.trail.value === 'all' ? Infinity : Math.max(1, Number(refs.trail.value) || 1); drawMap(); });
  refs.tabs.addEventListener('click', (e) => { const b=e.target.closest('[data-tab]'); if (b) renderDetail(b.dataset.tab); }); refs.dockClose.addEventListener('click', () => { refs.dock.classList.remove('open'); page.classList.remove('detail-open'); });
}

async function init() {
  bind();
  try {
    const [run, runtime, metrics] = await Promise.all([
      apiFetch(`/api/runs/${encodeURIComponent(state.runId)}`),
      apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/runtime`),
      apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/metrics`),
    ]);
    if (run.status !== 'succeeded') throw new ApiError(`运行态势仅支持成功 Run；当前状态=${run.status}`, { status:409, code:'RUN_NOT_SUCCEEDED' });
    if (runtime.run_id !== state.runId || metrics.run_id !== state.runId) throw new Error('Run identity mismatch');
    state.run=run; state.runtime=runtime; state.metrics=metrics; state.routeById=new Map(runtime.routes.map((x)=>[x.path_id,x]));
    refs.slider.max=String(Math.max(0,runtime.frames.length-1)); renderBadges(); renderFrame(); await initMap();
  } catch (error) { showError(error); }
}

document.addEventListener('DOMContentLoaded', init);
