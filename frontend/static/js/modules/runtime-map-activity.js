export function runtimeRoutePhase(route, window) {
  const current = Number(window);
  if (!Number.isFinite(current)) return 'unknown';
  if (current < Number(route.depart_window)) return 'upcoming';
  if (current < Number(route.return_window)) return 'executing';
  if (current < Number(route.ready_window)) return 'maintenance';
  return 'available';
}

export function summarizeRuntimeRouteState(routes, window, airportId = null, aircraftType = null) {
  let executing = 0;
  let maintenance = 0;
  for (const route of Array.isArray(routes) ? routes : []) {
    if (aircraftType && route.aircraft_type !== aircraftType) continue;
    const sorties = Number(route.sorties) || 0;
    if ((!airportId || route.origin_airport_id === airportId)
        && Number(route.depart_window) <= Number(window)
        && Number(window) < Number(route.return_window)) executing += sorties;
    if ((!airportId || route.return_airport_id === airportId)
        && Number(route.return_window) <= Number(window)
        && Number(window) < Number(route.ready_window)) maintenance += sorties;
  }
  return { executing, maintenance };
}

const RUNTIME_VIEWS = new Set(['execution', 'departure', 'landing', 'maintenance']);

function normalizedClusterIds(value) {
  return value instanceof Set ? value : new Set(value || []);
}

function matchesRuntimeView(route, view, window) {
  const current = Number(window);
  if (view === 'departure') return Number(route.depart_window) === current;
  if (view === 'landing') return Number(route.return_window) === current;
  if (view === 'maintenance') {
    return Number(route.return_window) <= current && current < Number(route.ready_window);
  }
  return Number(route.depart_window) <= current && current < Number(route.return_window);
}

export function filterRuntimeRoutes(routes, view, window, options = {}) {
  const mode = RUNTIME_VIEWS.has(view) ? view : 'execution';
  const filters = options.filters || {};
  const clusterAirportIds = normalizedClusterIds(options.clusterAirportIds);
  const hasClusterPartition = clusterAirportIds.size > 0;
  const scopeField = mode === 'landing' || mode === 'maintenance'
    ? 'return_airport_id'
    : 'origin_airport_id';

  return (Array.isArray(routes) ? routes : []).filter((item) => {
    if (!matchesRuntimeView(item, mode, window)) return false;
    if (filters.missionId && item.mission_id !== filters.missionId) return false;
    if (filters.aircraftType && item.aircraft_type !== filters.aircraftType) return false;
    if (filters.returnMode === 'same' && item.origin_airport_id !== item.return_airport_id) return false;
    if (filters.returnMode === 'cross' && item.origin_airport_id === item.return_airport_id) return false;
    if (hasClusterPartition && filters.airportScope === 'cluster'
        && !clusterAirportIds.has(item[scopeField])) return false;
    if (hasClusterPartition && filters.airportScope === 'outside'
        && clusterAirportIds.has(item[scopeField])) return false;
    return true;
  });
}

function addBreakdown(target, key, sorties) {
  target[key] = (target[key] || 0) + sorties;
}

function relationDefinition(route, view) {
  if (view === 'landing') return {
    key: `landing:${route.mission_id}:${route.return_airport_id}`,
    fromType: 'mission', fromId: route.mission_id,
    toType: 'airport', toId: route.return_airport_id,
    airportId: route.return_airport_id,
  };
  if (view === 'maintenance') return {
    key: `maintenance:${route.return_airport_id}`,
    fromType: null, fromId: null, toType: 'airport', toId: route.return_airport_id,
    airportId: route.return_airport_id,
  };
  return {
    key: `${view}:${route.origin_airport_id}:${route.mission_id}`,
    fromType: 'airport', fromId: route.origin_airport_id,
    toType: 'mission', toId: route.mission_id,
    airportId: route.origin_airport_id,
  };
}

function stableHash(value) {
  let hash = 0;
  for (const char of String(value || '')) hash = ((hash * 31) + char.charCodeAt(0)) | 0;
  return Math.abs(hash);
}

export function quadraticRelationshipPoints(start, end, relationKey, segments = 20) {
  const [lat1, lon1] = start || [];
  const [lat2, lon2] = end || [];
  if (![lat1, lon1, lat2, lon2].every(Number.isFinite)) return [];
  const dx = lon2 - lon1;
  const dy = lat2 - lat1;
  const length = Math.hypot(dx, dy);
  if (!length) return [[lat1, lon1], [lat2, lon2]];
  const hash = stableHash(relationKey);
  const sign = hash % 2 ? 1 : -1;
  const curveRatio = Math.min(.18, Math.max(.08, .09 + ((hash >> 1) % 5) * .018));
  const offset = length * curveRatio * sign;
  const controlLon = (lon1 + lon2) / 2 + (-dy / length) * offset;
  const controlLat = (lat1 + lat2) / 2 + (dx / length) * offset;
  const count = Math.max(16, Math.min(32, Number(segments) || 20));
  const points = [];
  for (let index = 0; index <= count; index += 1) {
    const ratio = index / count;
    const inverse = 1 - ratio;
    points.push([
      inverse * inverse * lat1 + 2 * inverse * ratio * controlLat + ratio * ratio * lat2,
      inverse * inverse * lon1 + 2 * inverse * ratio * controlLon + ratio * ratio * lon2,
    ]);
  }
  return points;
}

export function runtimeMotionSpec(view) {
  const animated = view === 'execution' || view === 'departure' || view === 'landing';
  return { animated, direction: animated ? 'forward' : null, dashOffset: animated ? -28 : 0 };
}

export function aggregateRuntimeRelations(routes, view, window, options = {}) {
  const mode = RUNTIME_VIEWS.has(view) ? view : 'execution';
  const clusterAirportIds = normalizedClusterIds(options.clusterAirportIds);
  const groups = new Map();
  for (const route of filterRuntimeRoutes(routes, mode, window, options)) {
    const definition = relationDefinition(route, mode);
    let relation = groups.get(definition.key);
    if (!relation) {
      relation = {
        ...definition,
        view: mode,
        sorties: 0,
        routes: [],
        pathIds: [],
        aircraft: {},
        returnAirports: {},
        isClusterRelation: clusterAirportIds.has(definition.airportId),
      };
      groups.set(definition.key, relation);
    }
    const sorties = Number(route.sorties) || 0;
    relation.sorties += sorties;
    relation.routes.push(route);
    relation.pathIds.push(route.path_id);
    addBreakdown(relation.aircraft, route.aircraft_type, sorties);
    addBreakdown(relation.returnAirports, route.return_airport_id, sorties);
  }
  return [...groups.values()].map((relation) => ({
    ...relation,
    routes: relation.routes.slice().sort((a, b) => String(a.path_id).localeCompare(String(b.path_id))),
    pathIds: relation.pathIds.slice().sort((a, b) => String(a).localeCompare(String(b))),
  })).sort((a, b) => a.key.localeCompare(b.key));
}

export function summarizeRuntimeOverview(routes, window, clusterAirportIds = new Set()) {
  const source = Array.isArray(routes) ? routes : [];
  const cluster = normalizedClusterIds(clusterAirportIds);
  const hasClusterPartition = cluster.size > 0;
  let executing = 0;
  let departures = 0;
  let landings = 0;
  let maintenance = 0;
  let clusterExecuting = 0;
  let outsideExecuting = 0;
  for (const route of source) {
    const sorties = Number(route.sorties) || 0;
    if (matchesRuntimeView(route, 'execution', window)) {
      executing += sorties;
      if (hasClusterPartition && cluster.has(route.origin_airport_id)) clusterExecuting += sorties;
      else if (hasClusterPartition) outsideExecuting += sorties;
    }
    if (matchesRuntimeView(route, 'departure', window)) departures += sorties;
    if (matchesRuntimeView(route, 'landing', window)) landings += sorties;
    if (matchesRuntimeView(route, 'maintenance', window)) maintenance += sorties;
  }
  return {
    executing,
    departures,
    landings,
    maintenance,
    hasClusterPartition,
    clusterExecuting: hasClusterPartition ? clusterExecuting : null,
    outsideExecuting: hasClusterPartition ? outsideExecuting : null,
  };
}

export function summarizeRuntimeView(routes, window, view, clusterAirportIds = new Set()) {
  const mode = RUNTIME_VIEWS.has(view) ? view : 'execution';
  const clusterAirportIdsNormalized = normalizedClusterIds(clusterAirportIds);
  const hasClusterPartition = clusterAirportIdsNormalized.size > 0;
  const airportField = mode === 'landing' || mode === 'maintenance'
    ? 'return_airport_id'
    : 'origin_airport_id';
  let total = 0;
  let cluster = 0;
  let outside = 0;
  for (const route of Array.isArray(routes) ? routes : []) {
    if (!matchesRuntimeView(route, mode, window)) continue;
    const sorties = Number(route.sorties) || 0;
    total += sorties;
    if (!hasClusterPartition) continue;
    if (clusterAirportIdsNormalized.has(route[airportField])) cluster += sorties;
    else outside += sorties;
  }
  return {
    view: mode,
    total,
    cluster: hasClusterPartition ? cluster : null,
    outside: hasClusterPartition ? outside : null,
    hasClusterPartition,
  };
}

export function reconcileRuntimeSelection(selection, relations, routes) {
  const current = selection && typeof selection === 'object'
    ? selection
    : { type: null, id: null };
  if (current.type === 'relation') {
    return (relations || []).some((item) => item.key === current.id)
      ? current
      : { type: null, id: null };
  }
  if (current.type === 'path') {
    return (routes || []).some((item) => item.path_id === current.id)
      ? current
      : { type: null, id: null };
  }
  return current;
}
