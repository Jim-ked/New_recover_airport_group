import test from 'node:test';
import assert from 'node:assert/strict';
import {
  aggregateRuntimeRelations,
  filterRuntimeRoutes,
  quadraticRelationshipPoints,
  reconcileRuntimeSelection,
  runtimeMotionSpec,
  runtimeRoutePhase,
  summarizeRuntimeOverview,
  summarizeRuntimeRouteState,
  summarizeRuntimeView,
} from '../../frontend/static/js/modules/runtime-map-activity.js';

const route = (overrides = {}) => ({
  path_id: 'P1', origin_airport_id: 'A1', mission_id: 'M1',
  return_airport_id: 'A2', aircraft_type: 'T1', sorties: 2,
  depart_window: 3, return_window: 6, ready_window: 8,
  ...overrides,
});

const cluster = new Set(['A1', 'A3']);

test('route phase uses depart, return and ready boundaries exactly', () => {
  assert.equal(runtimeRoutePhase(route(), 2), 'upcoming');
  assert.equal(runtimeRoutePhase(route(), 3), 'executing');
  assert.equal(runtimeRoutePhase(route(), 5), 'executing');
  assert.equal(runtimeRoutePhase(route(), 6), 'maintenance');
  assert.equal(runtimeRoutePhase(route(), 7), 'maintenance');
  assert.equal(runtimeRoutePhase(route(), 8), 'available');
});

test('execution aggregates multiple path ids by origin and mission and retains drill-down', () => {
  const relations = aggregateRuntimeRelations([
    route(),
    route({ path_id: 'P2', aircraft_type: 'T2', sorties: 3, return_airport_id: 'A3' }),
  ], 'execution', 4, { clusterAirportIds: cluster });
  assert.equal(relations.length, 1);
  assert.equal(relations[0].sorties, 5);
  assert.deepEqual(relations[0].pathIds, ['P1', 'P2']);
  assert.deepEqual(relations[0].aircraft, { T1: 2, T2: 3 });
  assert.deepEqual(relations[0].returnAirports, { A2: 2, A3: 3 });
});

test('one airport can support multiple missions and multiple airports can support one mission', () => {
  const relations = aggregateRuntimeRelations([
    route(),
    route({ path_id: 'P2', mission_id: 'M2' }),
    route({ path_id: 'P3', origin_airport_id: 'A3' }),
  ], 'execution', 4, { clusterAirportIds: cluster });
  assert.deepEqual(relations.map((item) => item.key), [
    'execution:A1:M1', 'execution:A1:M2', 'execution:A3:M1',
  ]);
});

test('departure, execution, landing and maintenance views use their exact boundaries', () => {
  const source = [route()];
  assert.equal(aggregateRuntimeRelations(source, 'departure', 3).length, 1);
  assert.equal(aggregateRuntimeRelations(source, 'departure', 4).length, 0);
  assert.equal(aggregateRuntimeRelations(source, 'execution', 5).length, 1);
  assert.equal(aggregateRuntimeRelations(source, 'execution', 6).length, 0);
  const landing = aggregateRuntimeRelations(source, 'landing', 6);
  assert.equal(landing.length, 1);
  assert.equal(landing[0].fromType, 'mission');
  assert.equal(landing[0].toId, 'A2');
  const maintenance = aggregateRuntimeRelations(source, 'maintenance', 6);
  assert.equal(maintenance.length, 1);
  assert.equal(maintenance[0].airportId, 'A2');
  assert.equal(aggregateRuntimeRelations(source, 'maintenance', 8).length, 0);
});

test('cross-airport execution belongs to origin while maintenance belongs to return airport', () => {
  assert.deepEqual(summarizeRuntimeRouteState([route()], 4, 'A1'), { executing: 2, maintenance: 0 });
  assert.deepEqual(summarizeRuntimeRouteState([route()], 4, 'A2'), { executing: 0, maintenance: 0 });
  assert.deepEqual(summarizeRuntimeRouteState([route()], 6, 'A1'), { executing: 0, maintenance: 0 });
  assert.deepEqual(summarizeRuntimeRouteState([route()], 6, 'A2'), { executing: 0, maintenance: 2 });
});

test('multiple filters intersect before aggregation', () => {
  const source = [
    route(),
    route({ path_id: 'P2', aircraft_type: 'T2', sorties: 3 }),
    route({ path_id: 'P3', origin_airport_id: 'A4', return_airport_id: 'A4' }),
    route({ path_id: 'P4', origin_airport_id: 'A3', mission_id: 'M2' }),
  ];
  const options = {
    clusterAirportIds: cluster,
    filters: { airportScope: 'cluster', missionId: 'M1', aircraftType: 'T1', returnMode: 'cross' },
  };
  const filtered = filterRuntimeRoutes(source, 'execution', 4, options);
  assert.deepEqual(filtered.map((item) => item.path_id), ['P1']);
  const relations = aggregateRuntimeRelations(source, 'execution', 4, options);
  assert.equal(relations[0].sorties, 2);
  assert.deepEqual(relations[0].pathIds, ['P1']);
});

test('landing and maintenance airport scope uses return airport', () => {
  const source = [route({ origin_airport_id: 'A4', return_airport_id: 'A1' })];
  const options = { clusterAirportIds: cluster, filters: { airportScope: 'cluster' } };
  assert.equal(filterRuntimeRoutes(source, 'landing', 6, options).length, 1);
  assert.equal(filterRuntimeRoutes(source, 'maintenance', 7, options).length, 1);
});

test('no-cluster runs ignore unavailable cluster partition filters', () => {
  const source = [route({ origin_airport_id: 'A4' })];
  assert.deepEqual(filterRuntimeRoutes(source, 'execution', 4, {
    clusterAirportIds: new Set(), filters: { airportScope: 'outside' },
  }).map((item) => item.path_id), ['P1']);
  const overview = summarizeRuntimeOverview(source, 4, new Set());
  assert.equal(overview.hasClusterPartition, false);
  assert.equal(overview.clusterExecuting, null);
  assert.equal(overview.outsideExecuting, null);
});

test('core identity does not imply selected-cluster membership', () => {
  const source = [route({ origin_airport_id: 'CORE-OUTSIDE' })];
  const relations = aggregateRuntimeRelations(source, 'execution', 4, { clusterAirportIds: cluster });
  assert.equal(relations[0].isClusterRelation, false);
});

test('display filters do not change full-run overview totals', () => {
  const source = [route(), route({ path_id: 'P2', origin_airport_id: 'A4', sorties: 3 })];
  const before = summarizeRuntimeOverview(source, 4, cluster);
  const filtered = filterRuntimeRoutes(source, 'execution', 4, {
    clusterAirportIds: cluster, filters: { airportScope: 'cluster' },
  });
  assert.equal(filtered.length, 1);
  assert.deepEqual(summarizeRuntimeOverview(source, 4, cluster), before);
  assert.equal(before.executing, 5);
  assert.equal(before.clusterExecuting, 2);
  assert.equal(before.outsideExecuting, 3);
});

test('active-view cluster partition uses origin for execution and departure and return for landing and maintenance', () => {
  const source = [
    route({ path_id: 'P1', origin_airport_id: 'A1', return_airport_id: 'A4', sorties: 2 }),
    route({ path_id: 'P2', origin_airport_id: 'A4', return_airport_id: 'A3', sorties: 3 }),
  ];
  assert.deepEqual(summarizeRuntimeView(source, 3, 'departure', cluster), {
    view: 'departure', total: 5, cluster: 2, outside: 3, hasClusterPartition: true,
  });
  assert.deepEqual(summarizeRuntimeView(source, 6, 'landing', cluster), {
    view: 'landing', total: 5, cluster: 3, outside: 2, hasClusterPartition: true,
  });
  assert.deepEqual(summarizeRuntimeView(source, 7, 'maintenance', cluster), {
    view: 'maintenance', total: 5, cluster: 3, outside: 2, hasClusterPartition: true,
  });
});

test('no-cluster active view hides contribution values instead of inferring members', () => {
  assert.deepEqual(summarizeRuntimeView([route()], 4, 'execution', new Set()), {
    view: 'execution', total: 2, cluster: null, outside: null, hasClusterPartition: false,
  });
});

test('quadratic relation geometry is stable, bounded, curved and preserves endpoints', () => {
  const start = [30, 100];
  const end = [36, 112];
  const first = quadraticRelationshipPoints(start, end, 'execution:A1:M1');
  const again = quadraticRelationshipPoints(start, end, 'execution:A1:M1');
  assert.deepEqual(first, again);
  assert.deepEqual(first[0], start);
  assert.deepEqual(first.at(-1), end);
  assert.ok(first.length >= 17);
  const midpoint = first[Math.floor(first.length / 2)];
  const straightMidpoint = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
  const deviation = Math.hypot(midpoint[0] - straightMidpoint[0], midpoint[1] - straightMidpoint[1]);
  const distance = Math.hypot(end[0] - start[0], end[1] - start[1]);
  assert.ok(deviation >= distance * 0.035);
  assert.ok(deviation <= distance * 0.1);
});

test('relationship motion follows business direction and maintenance has no animated line', () => {
  for (const view of ['execution', 'departure', 'landing']) {
    assert.deepEqual(runtimeMotionSpec(view), { animated: true, direction: 'forward', dashOffset: -28 });
  }
  assert.deepEqual(runtimeMotionSpec('maintenance'), { animated: false, direction: null, dashOffset: 0 });
});

test('selection survives time and view changes only while its semantic object remains valid', () => {
  const source = [route()];
  const execution = aggregateRuntimeRelations(source, 'execution', 4);
  assert.deepEqual(reconcileRuntimeSelection({ type: 'relation', id: execution[0].key }, execution, source), {
    type: 'relation', id: execution[0].key,
  });
  assert.deepEqual(reconcileRuntimeSelection({ type: 'relation', id: execution[0].key }, [], source), {
    type: null, id: null,
  });
  assert.deepEqual(reconcileRuntimeSelection({ type: 'path', id: 'P1' }, [], source), {
    type: 'path', id: 'P1',
  });
  assert.deepEqual(reconcileRuntimeSelection({ type: 'airport', id: 'A1' }, [], source), {
    type: 'airport', id: 'A1',
  });
});
