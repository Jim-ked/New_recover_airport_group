import { apiFetch } from './api-client.js';
import { escapeHtml, page, refs, state } from './situation-state.js';

const WORLD_BOUNDS = [[-85.05112878, -180], [85.05112878, 180]];
const mapLayers = [];
const externalLayers = { airports: null, missions: null };
const externalCache = { airports: null, missions: null };
let map = null;
let fallback = false;
let callbacks = {};
let requestSignal = null;

export function configureMap(nextCallbacks) {
  callbacks = { ...callbacks, ...nextCallbacks };
  requestSignal = nextCallbacks.signal || requestSignal;
}

function clearMapLayers() {
  for (const layer of mapLayers) layer.remove?.();
  mapLayers.length = 0;
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

function addTileLayer() {
  const template = page.dataset.tileTemplate;
  if (!template) return;
  globalThis.L.tileLayer(template, {
    minZoom: 2,
    minNativeZoom: 2,
    maxNativeZoom: 7,
    maxZoom: 9,
    noWrap: true,
    bounds: WORLD_BOUNDS,
    keepBuffer: 4,
    updateWhenIdle: false,
    updateWhenZooming: true,
  }).addTo(map);
}

function airportMarkerClass(airportId) {
  const selected = state.selected?.type === 'airport' && state.selected.id === airportId;
  return `situation-airport-marker${selected ? ' selected' : ''}`;
}

function drawLeaflet() {
  clearMapLayers();
  if (!map || !state.working) return;
  const L = globalThis.L;
  const damaged = damagedAirportIds();

  for (const item of state.working.airports) {
    const airport = item.airport;
    const latitude = Number(airport.latitude);
    const longitude = Number(airport.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    const icon = L.divIcon({
      className: airportMarkerClass(airport.airport_id),
      html: '<span></span>',
      iconSize: [16, 16],
      iconAnchor: [8, 8],
    });
    const marker = L.marker([latitude, longitude], { icon, title: airport.airport_name });
    marker.on('click', () => callbacks.selectObject?.('airport', airport.airport_id));
    marker.addTo(map);
    mapLayers.push(marker);
    if (damaged.has(airport.airport_id)) {
      const ring = L.circleMarker([latitude, longitude], {
        radius: 12,
        color: '#dd7777',
        weight: 1.5,
        fillOpacity: 0,
        interactive: false,
      });
      ring.addTo(map);
      mapLayers.push(ring);
    }
  }

  if (state.mode === 'airport') {
    for (const airport of visibleCandidates()) {
      const latitude = Number(airport.latitude);
      const longitude = Number(airport.longitude);
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
      const chosen = state.tempAirportIds.has(airport.airport_id);
      const icon = L.divIcon({
        className: `situation-candidate-marker${chosen ? ' selected' : ''}`,
        html: '<span></span>',
        iconSize: [14, 14],
        iconAnchor: [7, 7],
      });
      const marker = L.marker([latitude, longitude], { icon, title: airport.airport_name });
      marker.on('click', () => callbacks.toggleCandidate?.(airport.airport_id));
      marker.addTo(map);
      mapLayers.push(marker);
    }
  }

  for (const mission of state.working.missions) {
    const latitude = Number(mission.latitude);
    const longitude = Number(mission.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    const selected = state.selected?.type === 'mission' && state.selected.id === mission.mission_id;
    const icon = L.divIcon({
      className: `situation-mission-marker${selected ? ' selected' : ''}`,
      html: '<span></span>',
      iconSize: [14, 14],
      iconAnchor: [7, 7],
    });
    const marker = L.marker([latitude, longitude], { icon, title: mission.name });
    marker.on('click', () => callbacks.selectObject?.('mission', mission.mission_id));
    marker.addTo(map);
    mapLayers.push(marker);
  }

  if (state.draftMissionCoord) {
    const { lat, lon } = state.draftMissionCoord;
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      const icon = L.divIcon({
        className: 'situation-mission-marker draft',
        html: '<span></span>',
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      const marker = L.marker([lat, lon], { icon, interactive: false });
      marker.addTo(map);
      mapLayers.push(marker);
    }
  }
}

function fallbackPoints() {
  if (!state.working) return [];
  return [
    ...state.working.airports.map((item) => ({
      type: 'airport',
      id: item.airport.airport_id,
      name: item.airport.airport_name,
      lat: Number(item.airport.latitude),
      lon: Number(item.airport.longitude),
    })),
    ...visibleCandidates().map((airport) => ({
      type: 'candidate',
      id: airport.airport_id,
      name: airport.airport_name,
      lat: Number(airport.latitude),
      lon: Number(airport.longitude),
      chosen: state.tempAirportIds.has(airport.airport_id),
    })),
    ...state.working.missions.map((mission) => ({
      type: 'mission',
      id: mission.mission_id,
      name: mission.name,
      lat: Number(mission.latitude),
      lon: Number(mission.longitude),
    })),
    ...(state.draftMissionCoord ? [{
      type: 'draft', id: 'draft-mission', name: '任务临时位置',
      lat: state.draftMissionCoord.lat, lon: state.draftMissionCoord.lon,
    }] : []),
  ];
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
    return `<button class="fallback-object ${point.type}${damage ? ' damage' : ''}${point.chosen ? ' selected' : ''}" style="left:${left}%;top:${top}%" data-type="${point.type}" data-id="${escapeHtml(point.id)}" title="${escapeHtml(point.name)}"><span class="fallback-label">${escapeHtml(point.name)}</span></button>`;
  }).join('');
  refs.fallbackObjects.querySelectorAll('button').forEach((button) => {
    button.addEventListener('click', () => {
      if (button.dataset.type === 'candidate') callbacks.toggleCandidate?.(button.dataset.id);
      else if (button.dataset.type !== 'draft') callbacks.selectObject?.(button.dataset.type, button.dataset.id);
    });
  });
}

export function drawMap() {
  if (fallback) drawFallback();
  else drawLeaflet();
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
  button.textContent = '请在地图点击位置…';
  map.once('click', (event) => {
    const longitude = document.getElementById('sitMissionLon');
    const latitude = document.getElementById('sitMissionLat');
    longitude.value = event.latlng.lng.toFixed(6);
    latitude.value = event.latlng.lat.toFixed(6);
    state.draftMissionCoord = { lon: event.latlng.lng, lat: event.latlng.lat };
    state.panelDraftDirty = true;
    button.textContent = '从地图取点';
    drawMap();
  });
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

export async function setCatalogLayer(kind, enabled) {
  clearExternalLayer(kind);
  if (!enabled || !map || !globalThis.L) return 0;
  const path = kind === 'airports' ? '/api/airports' : '/api/missions';
  externalCache[kind] ||= await fetchPaged(path);
  const group = globalThis.L.layerGroup();
  let count = 0;
  for (const row of externalCache[kind]) {
    const item = kind === 'missions' ? (row.mission || row) : row;
    const latitude = Number(item.latitude);
    const longitude = Number(item.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    const marker = globalThis.L.circleMarker([latitude, longitude], {
      radius: kind === 'airports' ? 3 : 3.5,
      weight: 1,
      color: kind === 'airports' ? '#68a5c9' : '#b2c1cd',
      fillColor: kind === 'airports' ? '#68a5c9' : '#b2c1cd',
      fillOpacity: 0.26,
      opacity: 0.72,
      className: `catalog-${kind === 'airports' ? 'airport' : 'mission'}-marker`,
    });
    const name = kind === 'airports' ? item.airport_name : item.name;
    const id = kind === 'airports' ? item.airport_id : item.mission_id;
    marker.bindTooltip(`${escapeHtml(name || id)}<br>${escapeHtml(id)}`, { direction: 'top' });
    group.addLayer(marker);
    count += 1;
  }
  group.addTo(map);
  externalLayers[kind] = group;
  return count;
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
    maxBounds: WORLD_BOUNDS,
    maxBoundsViscosity: 0.92,
    worldCopyJump: false,
    inertia: true,
    preferCanvas: true,
  });
  globalThis.L.control.zoom({ position: 'bottomleft' }).addTo(map);
  addFitControl();
  addTileLayer();
  map.setView([34, 108], 4);
  drawMap();
}

export function destroyMap() {
  clearMapLayers();
  clearExternalLayer('airports');
  clearExternalLayer('missions');
  if (map) {
    map.off();
    map.remove();
    map = null;
  }
  fallback = false;
  callbacks = {};
  requestSignal = null;
}
