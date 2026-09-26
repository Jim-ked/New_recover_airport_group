import { apiFetch } from './api-client.js';
import { airportMapLabel, airportMapTooltip, airportRoleClass } from './airport-display.js';
import { createLocalBasemap, destroyLocalBasemap } from './local-basemap.js';
import { createMapLabelLayout } from './map-label-layout.js';
import { createMapReference, destroyMapReference } from './map-reference.js';
import { escapeHtml, page, refs, state } from './situation-state.js';

const WORLD_BOUNDS = [[-85.05112878, -180], [85.05112878, 180]];
const CATALOG_MARKER_PANE = 'catalogMarkerPane';
const mapLayers = [];
const candidateMarkers = new Map();
const externalLayers = { airports: null, missions: null };
const externalCache = { airports: null, missions: null };
const externalEnabled = { airports: false, missions: false };
let map = null;
let basemapLayers = [];
let fallback = false;
let callbacks = {};
let requestSignal = null;
let labelLayout = null;
let mapReference = null;
let missionPickHandler = null;
let catalogInteractionEnabled = true;

const LABEL_PRIORITY = { selected: 100, focused: 90, preview: 88, primary: 85, airport: 80, mission: 75 };

export function configureMap(nextCallbacks) {
  callbacks = { ...callbacks, ...nextCallbacks };
  requestSignal = nextCallbacks.signal || requestSignal;
}

function clearMapLayers() {
  for (const layer of mapLayers) layer.remove?.();
  mapLayers.length = 0;
  for (const marker of candidateMarkers.values()) marker.remove?.();
  candidateMarkers.clear();
  refs.fallbackObjects?.replaceChildren();
}

function damagedAirportIds() {
  return new Set(
    (state.working?.damage_scenarios || [])
      .flatMap((scenario) => scenario.events || [])
      .map((event) => event.target?.airport_id)
      .filter(Boolean),
  );
}

function visibleCandidates() {
  return callbacks.visibleCandidateAirports?.() || [];
}

function currentObjectDisplayState(type, objectId) {
  const selected = state.selected?.type === type && state.selected.id === objectId;
  const focused = !selected && state.mapFocus?.type === type && state.mapFocus.id === objectId;
  const primary = (type === 'mission' && state.mode === 'mission')
    || (type === 'airport' && state.mode === 'damage');
  return { selected, focused, primary };
}

function markerZIndexOffset(type, objectId) {
  if (type !== 'candidate' && currentObjectDisplayState(type, objectId).selected) return 1000;
  const focused = type === 'candidate' ? state.candidateFocusId === objectId : currentObjectDisplayState(type, objectId).focused;
  if (focused) return 700;
  if (type === 'candidate' && state.tempAirportIds.has(objectId)) return 500;
  if (type === 'candidate' && ['airport', 'candidate-detail'].includes(state.mode)) return 300;
  if (type === 'mission' && state.mode === 'mission') return 300;
  if (type === 'airport' && state.mode === 'damage') return 300;
  return 100;
}

function annotateMarker(marker, type, objectId) {
  const element = marker.getElement?.();
  if (!element) return;
  element.dataset.objectType = type;
  element.dataset.objectId = objectId;
}

function addFitControl() {
  const L = globalThis.L;
  const FitControl = L.Control.extend({
    options: { position: 'bottomleft' },
    onAdd() {
      const box = L.DomUtil.create('div', 'leaflet-control leaflet-bar leaflet-control-fit');
      const button = L.DomUtil.create('button', 'leaflet-fit-button', box);
      button.type = 'button';
      button.textContent = '适应范围';
      button.title = '适应当前情境范围';
      L.DomEvent.disableClickPropagation(box);
      L.DomEvent.on(button, 'click', fitMap);
      return box;
    },
  });
  new FitControl().addTo(map);
}

function airportMarkerClass(airport, damaged) {
  const airportId = airport.airport_id;
  const display = currentObjectDisplayState('airport', airportId);
  return [
    'situation-airport-marker',
    airportRoleClass(airport.role),
    display.selected ? 'map-state-selected' : '',
    display.focused ? 'map-state-focused' : '',
    display.primary ? 'map-state-primary' : '',
    damaged ? 'has-damage-config' : '',
  ]
    .filter(Boolean).join(' ');
}

function bindMarkerActivation(marker, singleClick, doubleClick) {
  let clickTimer = null;
  marker.on('click', () => {
    clearTimeout(clickTimer);
    clickTimer = setTimeout(singleClick, 220);
  });
  marker.on('dblclick', (event) => {
    clearTimeout(clickTimer);
    if (event.originalEvent) globalThis.L.DomEvent.stop(event.originalEvent);
    doubleClick();
  });
}

function bindPermanentLabel(marker, text, priority, forceVisible, labels, emphasis = '') {
  marker.bindTooltip(escapeHtml(text), {
    permanent: true,
    direction: 'right',
    offset: [7, 0],
    className: `situation-map-label${emphasis ? ` ${emphasis}` : ''}`,
  });
  const element = marker.getTooltip()?.getElement();
  if (element) labels.push({ element, priority, forceVisible });
}

function candidateIcon(airport) {
  const queued = state.tempAirportIds.has(airport.airport_id);
  const focused = state.candidateFocusId === airport.airport_id;
  return globalThis.L.divIcon({
    className: `situation-candidate-marker ${airportRoleClass(airport.role)}${queued ? ' candidate-queued' : ''}${focused ? ' map-state-focused' : ''}`,
    html: '<span></span>',
    iconSize: [20, 20],
    iconAnchor: [10, 10],
  });
}

function candidateVisualKey(airport) {
  return `${airportRoleClass(airport.role)}:${state.tempAirportIds.has(airport.airport_id)}:${state.candidateFocusId === airport.airport_id}`;
}

function syncCandidateTooltip(marker, airportId) {
  if (state.candidateFocusId === airportId) marker.openTooltip();
  else marker.closeTooltip();
}

function syncCandidateLeafletMarkers({ refreshCatalog = true } = {}) {
  if (!map || !state.working) return;
  const visible = new Map(visibleCandidates().map((airport) => [airport.airport_id, airport]));
  for (const [airportId, marker] of candidateMarkers) {
    if (visible.has(airportId)) continue;
    marker.remove();
    candidateMarkers.delete(airportId);
  }
  for (const [airportId, airport] of visible) {
    const latitude = Number(airport.latitude);
    const longitude = Number(airport.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    let marker = candidateMarkers.get(airportId);
    if (!marker) {
      marker = globalThis.L.marker([latitude, longitude], {
        icon: candidateIcon(airport),
        zIndexOffset: markerZIndexOffset('candidate', airportId),
      });
      marker.bindTooltip(escapeHtml(airportMapTooltip(airport)), {
        direction: 'top',
        className: 'map-object-tooltip',
      });
      bindMarkerActivation(
        marker,
        () => callbacks.highlightObject?.('candidate', airportId),
        () => callbacks.openCandidateDetails?.(airportId),
      );
      marker._candidateVisualKey = candidateVisualKey(airport);
      marker.addTo(map);
      annotateMarker(marker, 'candidate', airportId);
      syncCandidateTooltip(marker, airportId);
      candidateMarkers.set(airportId, marker);
    } else if (marker._candidateVisualKey !== candidateVisualKey(airport)) {
      marker.setIcon(candidateIcon(airport));
      marker.setZIndexOffset(markerZIndexOffset('candidate', airportId));
      marker._candidateVisualKey = candidateVisualKey(airport);
      annotateMarker(marker, 'candidate', airportId);
      syncCandidateTooltip(marker, airportId);
    }
  }
  if (refreshCatalog) refreshCatalogLayers('airports');
}

function drawLeaflet() {
  clearMapLayers();
  if (!map || !state.working) return;
  const L = globalThis.L;
  const damaged = damagedAirportIds();
  const labels = [];

  for (const item of state.working.airports) {
    const airport = item.airport;
    const latitude = Number(airport.latitude);
    const longitude = Number(airport.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    const display = currentObjectDisplayState('airport', airport.airport_id);
    const isDamaged = damaged.has(airport.airport_id);
    const icon = L.divIcon({
      className: airportMarkerClass(airport, isDamaged),
      html: '<span></span>',
      iconSize: [24, 24],
      iconAnchor: [12, 12],
    });
    const marker = L.marker([latitude, longitude], {
      icon,
      zIndexOffset: markerZIndexOffset('airport', airport.airport_id),
    });
    bindMarkerActivation(
      marker,
      () => callbacks.highlightObject?.('airport', airport.airport_id),
      () => callbacks.selectObject?.('airport', airport.airport_id, { locate: true }),
    );
    marker.addTo(map);
    annotateMarker(marker, 'airport', airport.airport_id);
    const airportElement = marker.getElement();
    if (airportElement) {
      airportElement.setAttribute('aria-label', `${airportMapTooltip(airport)}${isDamaged ? '，存在损毁事件配置' : ''}`);
      if (isDamaged) airportElement.dataset.damageConfig = 'true';
    }
    mapLayers.push(marker);
    bindPermanentLabel(marker, airportMapLabel(airport),
      display.selected ? LABEL_PRIORITY.selected
        : display.focused ? LABEL_PRIORITY.focused
          : display.primary ? LABEL_PRIORITY.primary : LABEL_PRIORITY.airport,
      display.selected,
      labels,
      display.selected ? 'map-label-selected' : display.focused ? 'map-label-focused' : display.primary ? 'map-label-primary' : '');
  }

  syncCandidateLeafletMarkers({ refreshCatalog: false });

  for (const mission of state.working.missions) {
    const latitude = Number(mission.latitude);
    const longitude = Number(mission.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    const display = currentObjectDisplayState('mission', mission.mission_id);
    const icon = L.divIcon({
      className: `situation-mission-marker${display.selected ? ' map-state-selected' : ''}${display.focused ? ' map-state-focused' : ''}${display.primary ? ' map-state-primary' : ''}`,
      html: '<span></span>',
      iconSize: [14, 14],
      iconAnchor: [7, 7],
    });
    const marker = L.marker([latitude, longitude], {
      icon,
      zIndexOffset: markerZIndexOffset('mission', mission.mission_id),
    });
    bindMarkerActivation(
      marker,
      () => callbacks.selectObject?.('mission', mission.mission_id, { locate: true }),
      () => callbacks.selectObject?.('mission', mission.mission_id, { locate: true }),
    );
    marker.addTo(map);
    annotateMarker(marker, 'mission', mission.mission_id);
    if (marker.getElement()) marker.getElement().dataset.missionId = mission.mission_id;
    mapLayers.push(marker);
    bindPermanentLabel(marker, mission.name,
      display.selected ? LABEL_PRIORITY.selected
        : display.focused ? LABEL_PRIORITY.focused
          : display.primary ? LABEL_PRIORITY.primary : LABEL_PRIORITY.mission,
      display.selected,
      labels,
      display.selected ? 'map-label-selected' : display.focused ? 'map-label-focused' : display.primary ? 'map-label-primary' : '');
  }

  const previewMission = state.missionSourceSelection?.mission;
  if (previewMission) {
    const latitude = Number(previewMission.latitude);
    const longitude = Number(previewMission.longitude);
    if (Number.isFinite(latitude) && Number.isFinite(longitude)) {
      const icon = L.divIcon({
        className: 'situation-mission-marker mission-source-preview',
        html: '<span></span>',
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      const marker = L.marker([latitude, longitude], { icon, interactive: false });
      marker.addTo(map);
      if (marker.getElement()) marker.getElement().dataset.missionId = previewMission.mission_id;
      mapLayers.push(marker);
      bindPermanentLabel(marker, `${previewMission.name}（预览）`, LABEL_PRIORITY.preview, false, labels, 'map-label-preview');
    }
  }

  if (state.draftMissionCoord) {
    const { lat, lon } = state.draftMissionCoord;
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      const icon = L.divIcon({
        className: 'situation-mission-marker mission-location-draft',
        html: '<span></span>',
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      const marker = L.marker([lat, lon], { icon, interactive: false });
      marker.addTo(map);
      mapLayers.push(marker);
    }
  }
  labelLayout?.setItems(labels);
  refreshCatalogLayers();
}

function fallbackPoints() {
  if (!state.working) return [];
  return [
    ...state.working.airports.map((item) => ({
      type: 'airport',
      id: item.airport.airport_id,
      name: item.airport.airport_name,
      role: item.airport.role,
      lat: Number(item.airport.latitude),
      lon: Number(item.airport.longitude),
      ...currentObjectDisplayState('airport', item.airport.airport_id),
    })),
    ...visibleCandidates().map((airport) => ({
      type: 'candidate',
      id: airport.airport_id,
      name: airport.airport_name,
      role: airport.role,
      lat: Number(airport.latitude),
      lon: Number(airport.longitude),
      queued: state.tempAirportIds.has(airport.airport_id),
      focused: state.candidateFocusId === airport.airport_id,
    })),
    ...state.working.missions.map((mission) => ({
      type: 'mission',
      id: mission.mission_id,
      name: mission.name,
      lat: Number(mission.latitude),
      lon: Number(mission.longitude),
      ...currentObjectDisplayState('mission', mission.mission_id),
    })),
    ...(state.missionSourceSelection?.mission ? [{
      type: 'mission-preview',
      id: state.missionSourceSelection.mission.mission_id,
      name: `${state.missionSourceSelection.mission.name}（预览）`,
      lat: Number(state.missionSourceSelection.mission.latitude),
      lon: Number(state.missionSourceSelection.mission.longitude),
    }] : []),
    ...(state.draftMissionCoord ? [{
      type: 'draft', id: 'draft-mission', name: '任务临时位置',
      lat: state.draftMissionCoord.lat, lon: state.draftMissionCoord.lon,
    }] : []),
  ];
}

function fallbackZIndex(point) {
  if (point.type === 'draft') return 900;
  if (point.type === 'mission-preview') return 800;
  if (['airport', 'candidate', 'mission'].includes(point.type)) {
    return markerZIndexOffset(point.type, point.id);
  }
  return 100;
}

function drawFallback() {
  refs.fallback.classList.remove('hidden');
  refs.map.classList.add('hidden');
  const points = fallbackPoints().filter((point) => Number.isFinite(point.lat) && Number.isFinite(point.lon));
  if (!points.length) {
    refs.fallbackObjects.replaceChildren();
    return;
  }
  let minLat = Math.min(...points.map((point) => point.lat));
  let maxLat = Math.max(...points.map((point) => point.lat));
  let minLon = Math.min(...points.map((point) => point.lon));
  let maxLon = Math.max(...points.map((point) => point.lon));
  if (maxLat === minLat) { maxLat += 1; minLat -= 1; }
  if (maxLon === minLon) { maxLon += 1; minLon -= 1; }
  const damaged = damagedAirportIds();
  refs.fallbackObjects.innerHTML = points.map((point) => {
    const left = 10 + 80 * (point.lon - minLon) / (maxLon - minLon);
    const top = 12 + 76 * (maxLat - point.lat) / (maxLat - minLat);
    const damage = point.type === 'airport' && damaged.has(point.id);
    const roleClass = point.type === 'airport' || point.type === 'candidate' ? ` ${airportRoleClass(point.role)}` : '';
    const title = `${point.name}${damage ? '；存在损毁事件配置' : ''}`;
    return `<button class="fallback-object ${point.type}${roleClass}${damage ? ' has-damage-config' : ''}${point.selected ? ' map-state-selected' : ''}${point.focused ? ' map-state-focused' : ''}${point.primary ? ' map-state-primary' : ''}${point.queued ? ' candidate-queued' : ''}" style="left:${left}%;top:${top}%;z-index:${fallbackZIndex(point)}" data-type="${point.type}" data-id="${escapeHtml(point.id)}" title="${escapeHtml(title)}"><span class="fallback-shape"></span><span class="fallback-label">${escapeHtml(point.name)}</span></button>`;
  }).join('');
  refs.fallbackObjects.querySelectorAll('button').forEach((button) => {
    let clickTimer = null;
    button.addEventListener('click', () => {
      clearTimeout(clickTimer);
      clickTimer = setTimeout(() => {
        if (button.dataset.type === 'mission') callbacks.selectObject?.('mission', button.dataset.id, { locate: true });
        else if (!['draft', 'mission-preview'].includes(button.dataset.type)) callbacks.highlightObject?.(button.dataset.type, button.dataset.id);
      }, 220);
    });
    button.addEventListener('dblclick', () => {
      clearTimeout(clickTimer);
      if (button.dataset.type === 'candidate') callbacks.openCandidateDetails?.(button.dataset.id);
      else if (!['draft', 'mission-preview'].includes(button.dataset.type)) callbacks.selectObject?.(button.dataset.type, button.dataset.id, { locate: true });
    });
  });
}

export function drawMap() {
  if (fallback) drawFallback();
  else drawLeaflet();
}

export function updateCandidateMarkers() {
  if (fallback) drawFallback();
  else syncCandidateLeafletMarkers();
}

export function fitMap() {
  if (!map || !state.working) return;
  const coordinates = [
    ...state.working.airports.map((item) => [Number(item.airport.latitude), Number(item.airport.longitude)]),
    ...state.working.missions.map((mission) => [Number(mission.latitude), Number(mission.longitude)]),
  ].filter((pair) => pair.every(Number.isFinite));
  if (coordinates.length) map.fitBounds(globalThis.L.latLngBounds(coordinates).pad(0.12));
}

export function focusObject(type, objectId) {
  if (!map || !state.working) return;
  const value = type === 'airport'
    ? state.working.airports.find((item) => item.airport.airport_id === objectId)?.airport
    : type === 'candidate'
      ? visibleCandidates().find((item) => item.airport_id === objectId)
      : type === 'mission-preview'
        ? state.missionSourceSelection?.mission
        : state.working.missions.find((item) => item.mission_id === objectId);
  const latitude = Number(value?.latitude);
  const longitude = Number(value?.longitude);
  if (Number.isFinite(latitude) && Number.isFinite(longitude)) {
    map.setView([latitude, longitude], Math.max(map.getZoom(), 7), { animate: true });
  }
}

export function beginMissionLocationPick() {
  const button = document.getElementById('pickMissionLocation');
  if (!map || fallback) {
    callbacks.message?.('地图当前不可用，请直接输入经纬度。', 'error');
    return;
  }
  cancelMissionLocationPick();
  setCatalogInteractivity(false);
  button.textContent = '请在地图点击位置…';
  missionPickHandler = (event) => {
    const longitude = document.getElementById('sitMissionLon');
    const latitude = document.getElementById('sitMissionLat');
    if (!longitude || !latitude) {
      cancelMissionLocationPick();
      return;
    }
    longitude.value = event.latlng.lng.toFixed(6);
    latitude.value = event.latlng.lat.toFixed(6);
    state.draftMissionCoord = { lon: event.latlng.lng, lat: event.latlng.lat };
    callbacks.markPanelDraft?.();
    button.textContent = '从地图取点';
    missionPickHandler = null;
    setCatalogInteractivity(true);
    drawMap();
  };
  map.once('click', missionPickHandler);
}

export function cancelMissionLocationPick() {
  if (map && missionPickHandler) map.off('click', missionPickHandler);
  missionPickHandler = null;
  setCatalogInteractivity(true);
  const button = document.getElementById('pickMissionLocation');
  if (button) button.textContent = '从地图取点';
}

async function fetchPaged(path) {
  let offset = 0;
  let total = 1;
  const items = [];
  while (offset < total) {
    const separator = path.includes('?') ? '&' : '?';
    const response = await apiFetch(`${path}${separator}limit=500&offset=${offset}`, { signal: requestSignal });
    items.push(...(response.items || []));
    total = Number(response.total || 0);
    offset += 500;
  }
  return items;
}

function clearExternalLayer(kind) {
  externalLayers[kind]?.remove?.();
  externalLayers[kind] = null;
}

function catalogExcludedIds(kind) {
  if (!state.working) return new Set();
  if (kind === 'missions') return new Set(state.working.missions.map((item) => item.mission_id));
  const ids = new Set(state.working.airports.map((item) => item.airport.airport_id));
  for (const airport of visibleCandidates()) ids.add(airport.airport_id);
  return ids;
}

function setCatalogInteractivity(enabled) {
  catalogInteractionEnabled = enabled;
  for (const group of Object.values(externalLayers)) {
    group?.eachLayer?.((marker) => {
      const element = marker.getElement?.();
      if (element) element.style.pointerEvents = enabled ? '' : 'none';
    });
  }
}

function renderCatalogLayer(kind) {
  clearExternalLayer(kind);
  if (!externalEnabled[kind] || !map || !globalThis.L || !externalCache[kind]) return 0;
  const excluded = catalogExcludedIds(kind);
  const group = globalThis.L.layerGroup();
  let count = 0;
  for (const row of externalCache[kind]) {
    const item = kind === 'missions' ? (row.mission || row) : row;
    const objectId = kind === 'missions' ? item.mission_id : item.airport_id;
    if (excluded.has(objectId)) continue;
    const latitude = Number(item.latitude);
    const longitude = Number(item.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    const markerClass = kind === 'airports'
      ? `catalog-airport-marker ${airportRoleClass(item.role)}`
      : 'catalog-mission-marker';
    const marker = globalThis.L.marker([latitude, longitude], {
      pane: CATALOG_MARKER_PANE,
      icon: globalThis.L.divIcon({
        className: markerClass,
        html: '<span></span>',
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      }),
    });
    const tooltip = kind === 'airports' ? airportMapTooltip(item) : item.name;
    marker.bindTooltip(escapeHtml(tooltip), { direction: 'top', className: 'map-object-tooltip' });
    marker.on('add', () => {
      annotateMarker(marker, kind === 'airports' ? 'airport-reference' : 'mission-reference', objectId);
      const element = marker.getElement();
      if (element && !catalogInteractionEnabled) element.style.pointerEvents = 'none';
    });
    group.addLayer(marker);
    count += 1;
  }
  group.addTo(map);
  externalLayers[kind] = group;
  return count;
}

function refreshCatalogLayers(kind = null) {
  if (kind) return renderCatalogLayer(kind);
  renderCatalogLayer('airports');
  renderCatalogLayer('missions');
  return 0;
}

export async function setCatalogLayer(kind, enabled) {
  externalEnabled[kind] = enabled;
  clearExternalLayer(kind);
  if (!enabled || !map || !globalThis.L) return 0;
  const path = kind === 'airports' ? '/api/airports' : '/api/missions';
  externalCache[kind] ||= await fetchPaged(path);
  if (!externalEnabled[kind]) return 0;
  return renderCatalogLayer(kind);
}

export async function initMap() {
  if (!globalThis.L) {
    fallback = true;
    drawFallback();
    return;
  }
  fallback = false;
  refs.fallback.classList.add('hidden');
  refs.map.classList.remove('hidden');
  map = globalThis.L.map(refs.map, {
    attributionControl: false,
    zoomControl: false,
    maxZoom: 15,
    maxBounds: WORLD_BOUNDS,
    maxBoundsViscosity: 0.92,
    worldCopyJump: false,
    inertia: true,
    preferCanvas: true,
  });
  const catalogPane = map.createPane(CATALOG_MARKER_PANE);
  catalogPane.style.zIndex = '580';
  globalThis.L.control.zoom({ position: 'bottomleft' }).addTo(map);
  addFitControl();
  basemapLayers = createLocalBasemap(map, page.dataset.tileTemplate);
  mapReference = createMapReference(map);
  labelLayout = createMapLabelLayout(map);
  map.setView([34, 108], 4);
  drawMap();
}

export function destroyMap() {
  cancelMissionLocationPick();
  clearMapLayers();
  clearExternalLayer('airports');
  clearExternalLayer('missions');
  externalEnabled.airports = false;
  externalEnabled.missions = false;
  destroyLocalBasemap(basemapLayers);
  basemapLayers = [];
  destroyMapReference(mapReference);
  mapReference = null;
  labelLayout?.destroy();
  labelLayout = null;
  if (map) {
    map.off();
    map.remove();
    map = null;
  }
  fallback = false;
  callbacks = {};
  requestSignal = null;
}
