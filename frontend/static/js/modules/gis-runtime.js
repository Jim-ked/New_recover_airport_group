import { apiFetch, ApiError } from './api-client.js';
import { airportMapLabel, airportMapTooltip, airportRoleLabel, shortAirportName as compactAirportName } from './airport-display.js';
import { createLocalBasemap } from './local-basemap.js';
import { createMapLabelLayout } from './map-label-layout.js';
import { createMapReference } from './map-reference.js';
import { formatInteger } from './number-display.js';
import {
  aggregateRuntimeRelations,
  quadraticRelationshipPoints,
  reconcileRuntimeSelection,
  runtimeMotionSpec,
  runtimeRoutePhase,
  summarizeRuntimeOverview,
  summarizeRuntimeRouteState,
  summarizeRuntimeView,
} from './runtime-map-activity.js';

const page = document.getElementById('runtimePage');
const state = {
  runId: page?.dataset.runId || '', runtime: null, run: null,
  frameIndex: 0, playing: false, timer: null, map: null, playbackMs: 900,
  markers: new Map(), routeLayers: [], routeById: new Map(), currentRelations: [],
  selected: { type: null, id: null }, selectedRelationKey: null,
  selectedAircraftType: null, view: 'execution',
  filters: { airportScope: 'all', missionId: '', aircraftType: '', returnMode: 'all' },
  mapErrorShown: false, labelLayout: null, mapReference: null,
};
const LABEL_PRIORITY = { selected: 110, core: 90, selectedCluster: 82, participating: 72, relation: 66, mission: 60, ordinary: 20 };
const VIEW_LABELS = { execution: '执行态势', departure: '出动态势', landing: '落地态势', maintenance: '整备态势' };
const VIEW_PRIMARY = {
  execution: ['当前执行', 'executing'],
  departure: ['本窗出动', 'departures'],
  landing: ['本窗落地', 'landings'],
  maintenance: ['当前整备', 'maintenance'],
};
const $ = (id) => document.getElementById(id);
const refs = {
  map: $('runtimeMap'), kernel: $('runtimeKernelMessage'), badges: $('runtimeBadges'),
  inspector: $('runtimeInspectorBody'), inspectorWindow: $('inspectorWindow'), inspectorTitle: $('inspectorTitle'), inspectorBack: $('runtimeInspectorBack'),
  slider: $('runtimeSlider'), prev: $('runtimePrevButton'), next: $('runtimeNextButton'), play: $('runtimePlayButton'),
  windowLabel: $('runtimeWindowLabel'),
  viewSwitch: $('runtimeViewSwitch'), layerToggle: $('runtimeLayersToggle'), controls: $('runtimeLayerPanel'),
  legendToggle: $('runtimeLegendToggle'), legend: $('runtimeLegendPanel'), fit: $('fitRuntimeButton'), fitAll: $('fitAllRuntimeButton'),
  resetFilters: $('runtimeResetFilters'), airportScope: $('runtimeAirportScope'), scopeHint: $('runtimeScopeHint'),
  missionFilter: $('runtimeMissionFilter'), aircraftFilter: $('runtimeAircraftFilter'), returnFilter: $('runtimeReturnFilter'),
  speed: $('runtimeSpeed'), error: $('runtimeError'),
};

function assertRuntimeDom() {
  const missing = Object.entries(refs).filter(([, value]) => !value).map(([name]) => name);
  if (!page) missing.unshift('page');
  if (missing.length) throw new Error(`Runtime DOM contract missing: ${missing.join(', ')}`);
}

function showError(error) {
  console.error(error);
  refs.error.textContent = error instanceof ApiError ? error.message : '运行态势加载失败';
  refs.error.classList.remove('hidden');
}
function showMapError(error) {
  if (state.mapErrorShown) return;
  state.mapErrorShown = true;
  console.error(error);
  refs.kernel.textContent = '地图加载失败；运行结果数据已正常读取，时间轴与对象信息仍可用。';
  refs.kernel.classList.remove('hidden');
}
function setMapPanelOpen(panel, toggle, open) {
  panel.hidden = !open;
  toggle.setAttribute('aria-expanded', String(open));
}
function closeMapPanels() {
  setMapPanelOpen(refs.controls, refs.layerToggle, false);
  setMapPanelOpen(refs.legend, refs.legendToggle, false);
}
function toggleMapPanel(panel, toggle) {
  const open = panel.hidden;
  closeMapPanels();
  setMapPanelOpen(panel, toggle, open);
}
function escapeHtml(value) { return String(value ?? '—').replace(/[&<>'"]/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c])); }
function windowLabel(value) { return Number.isInteger(value) ? `T${value}` : '—'; }
function frame() { return state.runtime?.frames?.[state.frameIndex] || null; }
function airport(id) { return state.runtime.airports.find((item) => item.airport_id === id); }
function mission(id) { return state.runtime.missions.find((item) => item.mission_id === id); }
function airportName(id) { return airport(id)?.airport_name || id; }
function shortRunId(id) { const match = /^RUN-([a-f0-9]{8})/i.exec(String(id || '')); return match ? `R-${match[1].toUpperCase()}` : String(id || '—'); }
function airportNumber(id) { const match = /^AP(\d+)$/i.exec(String(id || '')); return match ? match[1].padStart(3, '0') : ''; }
function shortAirportName(id) { return compactAirportName(airportName(id)); }
function airportDisplay(id) { const number = airportNumber(id); return `${number ? `${number} ` : ''}${shortAirportName(id)}`; }
function missionName(id) { return mission(id)?.name || id; }
function clusterAirportIds() { return new Set(state.runtime.airports.filter((item) => item.is_selected_cluster).map((item) => item.airport_id)); }
function hasClusterPartition() { return clusterAirportIds().size > 0; }
function currentFilters() { return { ...state.filters }; }

function loadStyle(href) {
  if ([...document.styleSheets].some((item) => item.href && item.href.includes(href))) return Promise.resolve();
  const element = document.createElement('link'); element.rel = 'stylesheet'; element.href = href; document.head.append(element); return Promise.resolve();
}
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const existing = [...document.scripts].find((item) => item.src && item.src.includes(src));
    if (existing) { if (globalThis.L) resolve(); else existing.addEventListener('load', resolve, { once: true }); return; }
    const element = document.createElement('script'); element.src = src; element.onload = resolve; element.onerror = reject; document.head.append(element);
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

function damagePhaseLabel(value) {
  return ({ active: '损毁生效中', recovering: '恢复中', applied: '损毁已生效' })[value] || value;
}
function damageTypeLabel(value) {
  return ({ capacity_damage: '容量损毁', aircraft_damage: '航空器损毁', resource_damage: '资源损毁' })[value] || value;
}
function mapIcon(kind, item, damagePhase = null) {
  const L = globalThis.L;
  const selected = state.selected.type === kind && state.selected.id === item[`${kind}_id`];
  if (kind === 'mission') return L.divIcon({
    className: `runtime-mission-marker${selected ? ' selected-object' : ''}`,
    html: '<span></span>', iconSize: [28, 28], iconAnchor: [14, 14],
  });
  const classes = ['runtime-airport-marker'];
  if (item.is_participating) classes.push('participating');
  if (item.is_selected_cluster) classes.push('selected-cluster');
  if (item.is_core) classes.push('core');
  if (!item.is_participating && !item.is_selected_cluster && !item.is_core) classes.push('ordinary');
  if (selected) classes.push('selected-object');
  const damageKind = damagePhase === 'recovering' ? 'recovering' : (damagePhase ? 'damage' : null);
  const status = damageKind
    ? `<i class="runtime-damage-state ${damageKind}" title="${escapeHtml(damagePhaseLabel(damagePhase))}"></i>`
    : '';
  return L.divIcon({
    className: classes.join(' '), html: `<span></span>${status}`,
    iconSize: [28, 28], iconAnchor: [14, 14],
  });
}

function bindMapLabel(marker, text, priority, forceVisible, labels) {
  marker.bindTooltip(escapeHtml(text), { permanent: true, direction: 'right', offset: [8, 0], className: 'runtime-map-label' });
  const element = marker.getTooltip()?.getElement();
  if (element) labels.push({ element, priority, forceVisible });
}
function airportLabelPriority(item, relatedAirports) {
  if (state.selected.type === 'airport' && state.selected.id === item.airport_id) return LABEL_PRIORITY.selected;
  if (item.is_core) return LABEL_PRIORITY.core;
  if (item.is_selected_cluster) return LABEL_PRIORITY.selectedCluster;
  if (item.is_participating || relatedAirports.has(item.airport_id)) return LABEL_PRIORITY.participating;
  return LABEL_PRIORITY.ordinary;
}
function layerEnabled(name) { return refs.controls.querySelector(`[data-layer="${name}"]`)?.checked !== false; }
function clearMapLayers() {
  for (const marker of state.markers.values()) marker.remove();
  state.markers.clear();
  for (const layer of state.routeLayers) layer.remove();
  state.routeLayers = [];
}
function pointAt(points, ratio = .5) { return points[Math.round((points.length - 1) * ratio)]; }
function objectCoordinates(type, id) {
  const item = type === 'airport' ? airport(id) : mission(id);
  return item && Number.isFinite(item.latitude) && Number.isFinite(item.longitude) ? [item.latitude, item.longitude] : null;
}
function relationIsSelected(relation) {
  return state.selected.type === 'relation' && state.selected.id === relation.key;
}
function relationIsRelated(relation) {
  if (state.selected.type === 'airport') return relation.airportId === state.selected.id || relation.fromId === state.selected.id || relation.toId === state.selected.id;
  if (state.selected.type === 'mission') return relation.fromId === state.selected.id || relation.toId === state.selected.id;
  if (state.selected.type === 'relation') return relationIsSelected(relation);
  if (state.selected.type === 'path') return relation.pathIds.includes(state.selected.id);
  return true;
}
function relationOptions(relation) {
  const selected = relationIsSelected(relation) || (state.selected.type === 'path' && relation.pathIds.includes(state.selected.id));
  const related = relationIsRelated(relation);
  const baseWeight = Math.max(1.8, Math.min(5, 1.4 + Math.log2(Math.max(1, relation.sorties)) * .72));
  return {
    color: selected ? '#bfe8f7' : '#6f91a3',
    weight: selected ? baseWeight + 1.4 : baseWeight,
    opacity: selected ? 1 : (related ? .86 : .18),
    interactive: false,
    className: `runtime-relation-link runtime-relation-base${selected ? ' relation-selected' : ''}`,
  };
}
function relationMotionOptions(relation) {
  const selected = relationIsSelected(relation) || (state.selected.type === 'path' && relation.pathIds.includes(state.selected.id));
  const related = relationIsRelated(relation);
  return {
    color: selected ? '#e5f7ff' : '#a8cad9',
    weight: selected ? 2.3 : 1.7,
    opacity: selected ? 1 : (related ? .78 : .12),
    dashArray: '4 10',
    interactive: false,
    className: `runtime-relation-motion${selected ? ' relation-selected' : ''}`,
  };
}
function relationCountMarker(points, relation, labels) {
  const L = globalThis.L; const selected = relationIsSelected(relation);
  const marker = L.marker(pointAt(points), {
    icon: L.divIcon({
      className: `runtime-relation-count${selected ? ' selected' : ''}`,
      html: `<span>${formatInteger(relation.sorties)}</span>`, iconSize: [0, 0], iconAnchor: [0, 0],
    }),
    interactive: false, keyboard: false,
  });
  marker.addTo(state.map);
  const element = marker.getElement()?.querySelector('span');
  if (element) labels.push({
    element,
    priority: selected ? LABEL_PRIORITY.selected : (relation.isClusterRelation ? LABEL_PRIORITY.relation + 2 : LABEL_PRIORITY.relation),
    forceVisible: selected,
  });
  state.routeLayers.push(marker);
  return element;
}
function relationTooltip(relation) {
  const direction = relation.view === 'landing'
    ? `${missionName(relation.fromId)} → ${airportDisplay(relation.toId)}`
    : `${airportDisplay(relation.fromId)} → ${missionName(relation.toId)}`;
  return `${VIEW_LABELS[relation.view]} · ${direction} · ${formatInteger(relation.sorties)} 架次`;
}
function drawRelation(relation, labels) {
  const start = objectCoordinates(relation.fromType, relation.fromId);
  const end = objectCoordinates(relation.toType, relation.toId);
  if (!start || !end) return;
  const L = globalThis.L; const points = quadraticRelationshipPoints(start, end, relation.key);
  const line = L.polyline(points, relationOptions(relation));
  const motion = runtimeMotionSpec(relation.view).animated
    ? L.polyline(points, relationMotionOptions(relation))
    : null;
  const hit = L.polyline(points, { color: '#fff', weight: 12, opacity: 0, interactive: true, bubblingMouseEvents: false, className: 'runtime-relation-hit' });
  const select = (event) => {
    if (event.originalEvent) L.DomEvent.stopPropagation(event.originalEvent);
    selectRelation(relation.key);
  };
  hit.on('click', select);
  hit.bindTooltip(escapeHtml(relationTooltip(relation)), { sticky: true, className: 'map-object-tooltip' });
  line.addTo(state.map);
  if (motion) motion.addTo(state.map);
  hit.addTo(state.map);
  line.getElement()?.setAttribute('data-relation-key', relation.key);
  if (motion) {
    motion.getElement()?.setAttribute('data-relation-key', relation.key);
    motion.getElement()?.setAttribute('data-flow-direction', 'forward');
  }
  const hitElement = hit.getElement();
  if (hitElement) {
    hitElement.setAttribute('data-relation-key', relation.key);
    hitElement.setAttribute('role', 'button');
    hitElement.setAttribute('tabindex', '0');
    hitElement.setAttribute('aria-label', relationTooltip(relation));
    L.DomEvent.on(hitElement, 'keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectRelation(relation.key); }
    });
  }
  state.routeLayers.push(line, ...(motion ? [motion] : []), hit);
  const countElement = relationCountMarker(points, relation, labels);
  hit.on('mouseover', () => { line.bringToFront(); motion?.bringToFront(); countElement?.classList.remove('map-label-hidden'); });
  hit.on('mouseout', () => state.labelLayout?.layout());
  if (relationIsSelected(relation)) { line.bringToFront(); motion?.bringToFront(); }
}
function drawMaintenanceCount(relation, labels) {
  const coordinates = objectCoordinates('airport', relation.airportId);
  if (!coordinates) return;
  const marker = globalThis.L.marker(coordinates, {
    icon: globalThis.L.divIcon({ className: 'runtime-maintenance-highlight', html: `<span>${formatInteger(relation.sorties)}</span>`, iconSize: [0, 0], iconAnchor: [0, 0] }),
    interactive: false, keyboard: false, zIndexOffset: 360,
  });
  marker.addTo(state.map); state.routeLayers.push(marker);
  const element = marker.getElement()?.querySelector('span');
  if (element) labels.push({ element, priority: relationIsSelected(relation) ? LABEL_PRIORITY.selected : LABEL_PRIORITY.relation, forceVisible: relationIsSelected(relation) });
}
function selectedPath() { return state.selected.type === 'path' ? state.routeById.get(state.selected.id) : null; }
function drawSelectedPath() {
  const route = selectedPath();
  if (!route) return;
  const origin = objectCoordinates('airport', route.origin_airport_id);
  const target = objectCoordinates('mission', route.mission_id);
  const returning = objectCoordinates('airport', route.return_airport_id);
  if (!origin || !target || !returning) return;
  const legs = [[origin, target, 'out'], [target, returning, 'return']];
  for (const [start, end, suffix] of legs) {
    const points = quadraticRelationshipPoints(start, end, `${route.path_id}:${suffix}`);
    const line = globalThis.L.polyline(points, { color: '#bfe8f7', weight: 4.2, opacity: 1, dashArray: '7 5', className: 'runtime-path-drilldown' });
    line.bindTooltip(escapeHtml(`${route.path_id} · ${formatInteger(route.sorties)} 架次 · 业务关系示意`), { sticky: true, className: 'map-object-tooltip' });
    line.addTo(state.map); line.bringToFront(); state.routeLayers.push(line);
  }
}

function routeState(window, airportId = null, aircraftType = null) {
  return summarizeRuntimeRouteState(state.runtime.routes, window, airportId, aircraftType);
}
function aircraftStateForAirport(airportId, aircraftType = null) {
  const current = frame(); const rows = current?.airports?.[airportId]?.aircraft || {};
  const types = aircraftType ? [aircraftType] : Object.keys(rows);
  const available = types.reduce((total, type) => total + Number(rows[type]?.available_after_departure || 0), 0);
  return { available, ...routeState(current.window, airportId, aircraftType) };
}
function overview() { return summarizeRuntimeOverview(state.runtime.routes, frame().window, clusterAirportIds()); }
function activeViewOverview() { return summarizeRuntimeView(state.runtime.routes, frame().window, state.view, clusterAirportIds()); }
function aircraftLabel(value) { return ({ fighter: '战斗机', bomber: '轰炸机', transport: '运输机' })[value] || value; }

function drawMap() {
  if (!state.map || !globalThis.L) return;
  clearMapLayers();
  const L = globalThis.L; const currentFrame = frame(); const labels = [];
  state.currentRelations = aggregateRuntimeRelations(state.runtime.routes, state.view, currentFrame.window, {
    clusterAirportIds: clusterAirportIds(), filters: currentFilters(),
  });
  state.selected = reconcileRuntimeSelection(state.selected, state.currentRelations, state.runtime.routes);
  if (state.selected.type !== 'relation' && state.selected.type !== 'path') state.selectedRelationKey = null;

  const damagePhases = new Map(); const phasePriority = { active: 3, recovering: 2, applied: 1 };
  for (const event of currentFrame.damage_events || []) {
    const existing = damagePhases.get(event.airport_id);
    if (!existing || (phasePriority[event.phase] || 0) > (phasePriority[existing] || 0)) damagePhases.set(event.airport_id, event.phase);
  }
  const relatedAirports = new Set(state.currentRelations.map((item) => item.airportId));
  if (layerEnabled('airports')) for (const item of state.runtime.airports) {
    if (!Number.isFinite(item.latitude) || !Number.isFinite(item.longitude)) continue;
    const marker = L.marker([item.latitude, item.longitude], {
      icon: mapIcon('airport', item, damagePhases.get(item.airport_id)),
      title: `${airportMapTooltip(item)} · ${airportRoleLabel(item.role)}`,
    });
    marker.on('click', () => selectObject('airport', item.airport_id)); marker.addTo(state.map); state.markers.set(`airport:${item.airport_id}`, marker);
    const priority = airportLabelPriority(item, relatedAirports);
    if (priority > LABEL_PRIORITY.ordinary) {
      bindMapLabel(marker, airportMapLabel(item), priority, state.selected.type === 'airport' && state.selected.id === item.airport_id, labels);
    } else {
      marker.bindTooltip(escapeHtml(airportMapTooltip(item)), { direction: 'top', className: 'map-object-tooltip' });
    }
  }
  if (layerEnabled('missions')) for (const item of state.runtime.missions) {
    if (!Number.isFinite(item.latitude) || !Number.isFinite(item.longitude)) continue;
    const selected = state.selected.type === 'mission' && state.selected.id === item.mission_id;
    const marker = L.marker([item.latitude, item.longitude], { icon: mapIcon('mission', item) });
    marker.on('click', () => selectObject('mission', item.mission_id)); marker.addTo(state.map); state.markers.set(`mission:${item.mission_id}`, marker);
    bindMapLabel(marker, item.name, selected ? LABEL_PRIORITY.selected : LABEL_PRIORITY.mission, selected, labels);
  }
  if (state.view === 'maintenance') state.currentRelations.forEach((item) => drawMaintenanceCount(item, labels));
  else state.currentRelations.forEach((item) => drawRelation(item, labels));
  drawSelectedPath();
  state.labelLayout?.setItems(labels);
}

function fitMap(scope = 'run') {
  if (!state.map || !globalThis.L) return;
  const allAirports = state.runtime.airports || [];
  const runAirports = allAirports.filter((item) => item.is_participating || item.is_selected_cluster || item.is_core);
  const airports = scope === 'all' || !runAirports.length ? allAirports : runAirports;
  const coordinates = [...airports, ...(state.runtime.missions || [])]
    .filter((item) => Number.isFinite(item.latitude) && Number.isFinite(item.longitude))
    .map((item) => [item.latitude, item.longitude]);
  if (coordinates.length) state.map.fitBounds(globalThis.L.latLngBounds(coordinates).pad(.12));
}
async function initMap() {
  if (!(await ensureLeaflet())) {
    refs.kernel.textContent = 'Leaflet 本地地图内核尚未装载。运行态势数据、时间窗与对象信息仍可用。';
    refs.kernel.classList.remove('hidden'); return;
  }
  const L = globalThis.L;
  state.map = L.map(refs.map, { zoomControl: false, attributionControl: false, preferCanvas: false, maxZoom: 15 });
  createLocalBasemap(state.map, page.dataset.tileTemplate);
  state.mapReference = createMapReference(state.map);
  state.labelLayout = createMapLabelLayout(state.map);
  drawMap(); fitMap('run');
}

function renderBadges() {
  const damage = state.runtime.damage_scenario;
  refs.badges.textContent = [
    shortRunId(state.runId), state.run?.situation?.name || state.run?.situation?.situation_id,
    damage ? `损毁 ${damage.name || damage.damage_scenario_id}` : '无损毁', `${state.runtime.time_axis.slot_minutes} min/窗`,
  ].join(' · ');
}
function renderFrame() {
  const currentFrame = frame(); if (!currentFrame) return;
  refs.slider.value = String(state.frameIndex); refs.windowLabel.textContent = windowLabel(currentFrame.window);
  refs.inspectorWindow.textContent = windowLabel(currentFrame.window);
  drawMap(); renderInspector();
}
function setFrameIndex(index) { state.frameIndex = Math.max(0, Math.min(state.runtime.frames.length - 1, Number(index) || 0)); renderFrame(); }
function togglePlay() {
  state.playing = !state.playing; refs.play.textContent = state.playing ? '暂停' : '播放';
  if (state.timer) clearInterval(state.timer); state.timer = null;
  if (state.playing) state.timer = setInterval(() => {
    if (state.frameIndex >= state.runtime.frames.length - 1) {
      state.playing = false; refs.play.textContent = '播放'; clearInterval(state.timer); state.timer = null; return;
    }
    setFrameIndex(state.frameIndex + 1);
  }, state.playbackMs);
}

function inspectorRows(rows) { return `<div class="inspector-grid">${rows.map(([key, value]) => `<b>${escapeHtml(key)}</b><span>${escapeHtml(value)}</span>`).join('')}</div>`; }
function aircraftTypeOptions(airportId) {
  const rows = frame()?.airports?.[airportId]?.aircraft || {};
  return Object.keys(rows).sort().map((type) => `<option value="${escapeHtml(type)}"${state.selectedAircraftType === type ? ' selected' : ''}>${escapeHtml(aircraftLabel(type))}</option>`).join('');
}
function damageSummary(airportId) {
  const events = (frame()?.damage_events || []).filter((event) => event.airport_id === airportId);
  return events.length ? events.map((event) => `${damageTypeLabel(event.damage_type)}（${damagePhaseLabel(event.phase)}）`).join('；') : '当前无影响';
}
function eventSorties(airportId, aircraftType, direction) {
  const currentWindow = frame().window;
  return state.runtime.routes.reduce((total, route) => {
    if (aircraftType && route.aircraft_type !== aircraftType) return total;
    const matches = direction === 'departure'
      ? route.origin_airport_id === airportId && Number(route.depart_window) === Number(currentWindow)
      : route.return_airport_id === airportId && Number(route.return_window) === Number(currentWindow);
    return total + (matches ? Number(route.sorties) || 0 : 0);
  }, 0);
}
function renderOverallInspector() {
  const totals = overview();
  const active = activeViewOverview();
  const [primaryLabel, primaryKey] = VIEW_PRIMARY[state.view];
  const secondary = [
    ['当前执行', totals.executing, 'executing'],
    ['本窗出动', totals.departures, 'departures'],
    ['本窗落地', totals.landings, 'landings'],
    ['当前整备', totals.maintenance, 'maintenance'],
  ].filter(([, , key]) => key !== primaryKey);
  refs.inspectorTitle.textContent = '总体运行态势';
  const clusterPercent = active.total > 0 ? Math.max(0, Math.min(100, active.cluster / active.total * 100)) : 0;
  const contribution = active.hasClusterPartition
    ? `<section class="runtime-contribution" aria-label="组群任务承担"><h3>组群任务承担</h3><div class="runtime-contribution-values"><span>组群 <b>${formatInteger(active.cluster)}</b></span><span>群外 <b>${formatInteger(active.outside)}</b></span></div><div class="runtime-contribution-bar" role="img" aria-label="组群 ${formatInteger(active.cluster)}，群外 ${formatInteger(active.outside)}"><i style="width:${clusterPercent.toFixed(2)}%"></i></div></section>`
    : '';
  refs.inspector.innerHTML = `<div class="runtime-current-state"><strong>${escapeHtml(primaryLabel)} <b>${formatInteger(totals[primaryKey])}</b></strong><div class="runtime-secondary-state">${secondary.map(([label, value]) => `<span>${escapeHtml(label)} <b>${formatInteger(value)}</b></span>`).join('')}</div></div>${contribution}`;
}
function relationMissionBreakdown(relations, aircraftType = null) {
  const values = {};
  for (const relation of relations) for (const route of relation.routes || []) {
    if (aircraftType && route.aircraft_type !== aircraftType) continue;
    values[route.mission_id] = (values[route.mission_id] || 0) + (Number(route.sorties) || 0);
  }
  return breakdownText(values, missionName);
}
function renderAirportInspector() {
  const id = state.selected.id;
  const item = airport(id); const row = frame().airports[id] || {}; const types = Object.keys(row.aircraft || {}).sort();
  if (!types.includes(state.selectedAircraftType)) state.selectedAircraftType = null;
  const current = aircraftStateForAirport(id, state.selectedAircraftType);
  const relations = state.currentRelations.filter((relation) => relation.airportId === id);
  const activeAmount = relations.reduce((total, relation) => total + (relation.routes || []).reduce((sum, route) => (
    !state.selectedAircraftType || route.aircraft_type === state.selectedAircraftType
      ? sum + (Number(route.sorties) || 0)
      : sum
  ), 0), 0);
  const [primaryLabel] = VIEW_PRIMARY[state.view];
  const roles = [item?.is_core ? '核心' : null, item?.is_selected_cluster ? '组群' : null, item?.is_participating ? '实际参与' : null].filter(Boolean).join(' / ') || '未参与';
  refs.inspectorTitle.textContent = airportDisplay(id);
  refs.inspector.innerHTML = `<div class="runtime-object-primary"><span>${escapeHtml(primaryLabel)}</span><b>${formatInteger(activeAmount)}</b></div><select class="runtime-inspector-select" data-inspector-select="aircraft" aria-label="选择机型"><option value="">全部机型</option>${aircraftTypeOptions(id)}</select>${inspectorRows([['机场性质', airportRoleLabel(item?.role)], ['运行身份', roles], ['相关任务', relationMissionBreakdown(relations, state.selectedAircraftType)], ['当前可用航空器', current.available], ['本窗出动', eventSorties(id, state.selectedAircraftType, 'departure')], ['本窗落地', eventSorties(id, state.selectedAircraftType, 'return')], ['本站整备', current.maintenance], ['当前损毁', damageSummary(id)]])}`;
}
function missionStatus(item) {
  const currentWindow = Number(frame().window);
  if (!Number.isFinite(Number(item?.window_start_slot)) || !Number.isFinite(Number(item?.window_end_slot))) return '未记录';
  if (currentWindow < Number(item.window_start_slot)) return '尚未开始';
  if (currentWindow <= Number(item.window_end_slot)) return '任务窗口内';
  return '任务窗口已结束';
}
function renderMissionInspector() {
  const id = state.selected.id;
  const item = mission(id);
  const relations = state.currentRelations.filter((relation) => (relation.routes || []).some((route) => route.mission_id === id));
  const amounts = {};
  for (const relation of relations) for (const route of relation.routes || []) {
    if (route.mission_id !== id) continue;
    const airportId = state.view === 'landing' || state.view === 'maintenance' ? route.return_airport_id : route.origin_airport_id;
    amounts[airportId] = (amounts[airportId] || 0) + (Number(route.sorties) || 0);
  }
  const total = Object.values(amounts).reduce((sum, value) => sum + value, 0);
  const [primaryLabel] = VIEW_PRIMARY[state.view];
  refs.inspectorTitle.textContent = missionName(id);
  refs.inspector.innerHTML = `<div class="runtime-object-primary"><span>${escapeHtml(primaryLabel)}</span><b>${formatInteger(total)}</b></div>${inspectorRows([['当前状态', missionStatus(item)], ['任务时段', `${windowLabel(item?.window_start_slot)}–${windowLabel(item?.window_end_slot)}`], [state.view === 'landing' || state.view === 'maintenance' ? '相关返回机场' : '相关出发机场', breakdownText(amounts, airportDisplay)]])}`;
}
function routePhaseLabel(route) {
  return ({ upcoming: '尚未开始', executing: '执行中', maintenance: '整备中', available: '已再次可用' })[runtimeRoutePhase(route, frame().window)] || '未记录';
}
function breakdownText(values, formatter = (value) => value) {
  return Object.entries(values || {}).sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => `${formatter(key)} ${formatInteger(value)}`).join('；') || '无';
}
function relationByKey(key) { return state.currentRelations.find((item) => item.key === key); }
function renderRelationshipInspector() {
  const path = selectedPath();
  const relation = relationByKey(state.selected.type === 'relation' ? state.selected.id : state.selectedRelationKey);
  refs.inspectorTitle.textContent = path ? '独立航次组' : '总体投入关系';
  if (path) {
    const relationSummary = relation ? `<p class="inspector-hint">所属当前关系：${escapeHtml(relationTooltip(relation))}</p>` : '<p class="inspector-hint">该航次当前不属于所选态势视图。</p>';
    refs.inspector.innerHTML = `${relationSummary}${inspectorRows([['Path ID', path.path_id], ['完整业务链', `${airportDisplay(path.origin_airport_id)} → ${missionName(path.mission_id)} → ${airportDisplay(path.return_airport_id)}`], ['机型', aircraftLabel(path.aircraft_type)], ['架次数量', formatInteger(path.sorties)], ['出动时间', windowLabel(path.depart_window)], ['落地时间', windowLabel(path.return_window)], ['再次可用', windowLabel(path.ready_window)], ['当前阶段', routePhaseLabel(path)]])}${relation ? `<div class="runtime-breakdown"><button type="button" class="runtime-inspector-select" data-relation-id="${escapeHtml(relation.key)}">返回总体关系</button></div>` : ''}`;
    return;
  }
  const current = relation;
  if (!current) {
    refs.inspector.innerHTML = `<p class="inspector-hint">${VIEW_LABELS[state.view]}下没有符合当前筛选条件的关系。</p>`;
    return;
  }
  const endpoints = current.view === 'maintenance'
    ? airportDisplay(current.airportId)
    : (current.view === 'landing' ? `${missionName(current.fromId)} → ${airportDisplay(current.toId)}` : `${airportDisplay(current.fromId)} → ${missionName(current.toId)}`);
  refs.inspector.innerHTML = `${inspectorRows([['当前视图', VIEW_LABELS[current.view]], ['总体关系', endpoints], ['汇总架次', formatInteger(current.sorties)], ['机型构成', breakdownText(current.aircraft, aircraftLabel)], ['返回机场分布', breakdownText(current.returnAirports, airportDisplay)], ['构成航次组', `${current.pathIds.length} 组`]])}<details class="runtime-breakdown"><summary>查看独立航次组（${current.pathIds.length}）</summary><div class="runtime-breakdown-list">${current.routes.map((route) => `<button type="button" data-path-id="${escapeHtml(route.path_id)}"><span>${escapeHtml(route.path_id)} · ${escapeHtml(aircraftLabel(route.aircraft_type))}</span><strong>${formatInteger(route.sorties)}</strong></button>`).join('')}</div></details>`;
}
function renderInspector() {
  refs.inspectorBack.hidden = !state.selected.type;
  if (state.selected.type === 'airport') renderAirportInspector();
  else if (state.selected.type === 'mission') renderMissionInspector();
  else if (state.selected.type === 'relation' || state.selected.type === 'path') renderRelationshipInspector();
  else renderOverallInspector();
}
function selectObject(type, id) {
  state.selected = { type, id }; state.selectedRelationKey = null; state.selectedAircraftType = null;
  drawMap(); renderInspector();
}
function selectRelation(key) {
  state.selected = { type: 'relation', id: key }; state.selectedRelationKey = key;
  drawMap(); renderInspector();
}
function selectPath(pathId, relationKey = state.selectedRelationKey) {
  state.selected = { type: 'path', id: pathId }; state.selectedRelationKey = relationKey;
  drawMap(); renderInspector();
}
function clearSelection() {
  state.selected = { type: null, id: null };
  state.selectedRelationKey = null;
  state.selectedAircraftType = null;
  drawMap(); renderInspector();
}

function updateViewControls() {
  for (const button of refs.viewSwitch.querySelectorAll('[data-runtime-view]')) button.classList.toggle('active', button.dataset.runtimeView === state.view);
  refs.scopeHint.textContent = state.view === 'landing' || state.view === 'maintenance'
    ? '落地与整备按返回机场筛选'
    : '执行与出动按出发机场筛选';
}
function setView(view) {
  if (!VIEW_LABELS[view] || state.view === view) return;
  state.view = view; updateViewControls(); drawMap(); renderInspector();
}
function applyFiltersFromControls() {
  state.filters = {
    airportScope: refs.airportScope.value,
    missionId: refs.missionFilter.value,
    aircraftType: refs.aircraftFilter.value,
    returnMode: refs.returnFilter.value,
  };
  refs.layerToggle.textContent = Object.values(state.filters).some((value) => value && value !== 'all') ? '筛选 · 已启用' : '筛选';
  drawMap(); renderInspector();
}
function resetFilters() {
  refs.airportScope.value = 'all'; refs.missionFilter.value = ''; refs.aircraftFilter.value = ''; refs.returnFilter.value = 'all';
  applyFiltersFromControls();
}
function populateFilterControls() {
  refs.missionFilter.insertAdjacentHTML('beforeend', state.runtime.missions.map((item) => `<option value="${escapeHtml(item.mission_id)}">${escapeHtml(missionName(item.mission_id))}</option>`).join(''));
  const aircraftTypes = [...new Set(state.runtime.routes.map((item) => item.aircraft_type))].sort();
  refs.aircraftFilter.insertAdjacentHTML('beforeend', aircraftTypes.map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(aircraftLabel(type))}</option>`).join(''));
  if (!hasClusterPartition()) {
    refs.airportScope.querySelectorAll('option:not([value="all"])').forEach((option) => { option.disabled = true; });
    refs.scopeHint.textContent = '当前 Run 未记录选定组群，机场范围筛选不可用';
  }
}

function bind() {
  refs.slider.addEventListener('input', () => setFrameIndex(refs.slider.value));
  refs.prev.addEventListener('click', () => setFrameIndex(state.frameIndex - 1)); refs.next.addEventListener('click', () => setFrameIndex(state.frameIndex + 1)); refs.play.addEventListener('click', togglePlay);
  refs.speed.addEventListener('change', () => { state.playbackMs = Number(refs.speed.value) || 900; if (state.playing) { togglePlay(); togglePlay(); } });
  refs.viewSwitch.addEventListener('click', (event) => { const button = event.target.closest('[data-runtime-view]'); if (button) setView(button.dataset.runtimeView); });
  refs.controls.addEventListener('change', (event) => {
    if (event.target.matches('[data-layer]')) { drawMap(); return; }
    if (event.target.matches('select')) applyFiltersFromControls();
  });
  refs.resetFilters.addEventListener('click', resetFilters); refs.fit.addEventListener('click', () => fitMap('run')); refs.fitAll.addEventListener('click', () => fitMap('all'));
  refs.layerToggle.addEventListener('click', () => toggleMapPanel(refs.controls, refs.layerToggle)); refs.legendToggle.addEventListener('click', () => toggleMapPanel(refs.legend, refs.legendToggle));
  if (state.map) state.map.on('click', (event) => {
    const target = event.originalEvent && event.originalEvent.target;
    if (target instanceof Element && target.closest('.leaflet-marker-icon, .leaflet-interactive')) return;
    closeMapPanels();
    if (state.selected.type) clearSelection();
  });
  refs.inspectorBack.addEventListener('click', clearSelection);
  refs.inspector.addEventListener('change', (event) => {
    const kind = event.target.dataset.inspectorSelect;
    if (kind === 'aircraft') { state.selectedAircraftType = event.target.value || null; renderInspector(); }
  });
  refs.inspector.addEventListener('click', (event) => {
    const pathButton = event.target.closest('[data-path-id]');
    if (pathButton) selectPath(pathButton.dataset.pathId);
    const relationButton = event.target.closest('[data-relation-id]');
    if (relationButton) selectRelation(relationButton.dataset.relationId);
  });
}

async function init() {
  try { assertRuntimeDom(); } catch (error) { console.error(error); return; }
  try {
    const [run, runtime] = await Promise.all([
      apiFetch(`/api/runs/${encodeURIComponent(state.runId)}`),
      apiFetch(`/api/runs/${encodeURIComponent(state.runId)}/runtime`),
    ]);
    if (run.status !== 'succeeded') throw new ApiError(`运行态势仅支持成功 Run；当前状态=${run.status}`, { status: 409, code: 'RUN_NOT_SUCCEEDED' });
    if (runtime.run_id !== state.runId) throw new Error('Run identity mismatch');
    state.run = run; state.runtime = runtime; state.routeById = new Map(runtime.routes.map((item) => [item.path_id, item]));
    refs.slider.max = String(Math.max(0, runtime.frames.length - 1));
    populateFilterControls(); updateViewControls();
  } catch (error) { showError(error); return; }
  try { await initMap(); } catch (error) { showMapError(error); }
  renderBadges(); renderFrame(); bind();
}

document.addEventListener('DOMContentLoaded', init);
