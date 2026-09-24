import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DAMAGE_PRESETS, generateDamageEvents, numberDamageEvents} from '../../frontend/static/js/modules/damage-prefill.js';

const airport = (id, capacity = 20, complete = true) => ({airport: {airport_id: id, airport_name: id}, operational_profile: {capacity_per_window: capacity, configuration_complete: complete}});
const airports = [airport('A3', 30), airport('A1', 10), airport('A2', 20)];
const params = (extra = {}) => ({kind: 'medium', scope: 'all', airportIds: [], count: 2, start: 7, end: 35, seed: 'seed-1', offset: 2, stages: structuredClone(DAMAGE_PRESETS.medium.stages), ...extra});

test('same seed reproduces, candidate order is irrelevant, selected distinct seeds differ', () => {
  const a = generateDamageEvents(params(), airports);
  assert.deepEqual(a, generateDamageEvents(params(), [...airports].reverse()));
  assert.notDeepEqual(a.events, generateDamageEvents(params({seed:'seed-2'}), airports).events);
  assert.equal('event_id' in a.events[0], false);
  assert.equal('sequence' in a.events[0], false);
});

test('all presets obey integer capacity, time range, common time and stage continuity', () => {
  for (const [kind, preset] of Object.entries(DAMAGE_PRESETS)) {
    for (let seed = 0; seed < 60; seed++) {
      const p = params({kind, seed:String(seed), stages:structuredClone(preset.stages)});
      const result = generateDamageEvents(p, airports);
      for (const item of airports) {
        const events = result.events.filter(e => e.target.airport_id === item.airport.airport_id);
        assert.equal(events.length, preset.stages.length);
        assert.ok(Math.abs(events[0].start_slot - result.commonStart) <= p.offset);
        events.forEach((e, index) => {
          const stage = preset.stages[index];
          const cap = e.effect.remaining_capacity_per_window;
          assert.ok(Number.isInteger(cap));
          assert.ok(e.start_slot >= p.start && e.end_slot <= p.end);
          assert.ok(e.end_slot - e.start_slot >= stage.duration[0]);
          assert.ok(e.end_slot - e.start_slot <= stage.duration[1]);
          assert.equal(e.recovery_mode, 'instant');
          if (stage.ratio) {
            const base = item.operational_profile.capacity_per_window;
            assert.ok(cap > 0 && cap < base);
            assert.ok(100*cap >= base*stage.ratio[0] && 100*cap <= base*stage.ratio[1]);
          } else assert.equal(cap, 0);
          if (index) assert.equal(events[index-1].end_slot, e.start_slot);
        });
        if (kind === 'extreme') assert.ok(events[2].effect.remaining_capacity_per_window > events[0].effect.remaining_capacity_per_window);
      }
    }
  }
});

test('ineligible airports have reasons; manual selection does not silently skip; random count is strict', () => {
  const candidates = [...airports, airport('small',1), airport('incomplete',20,false), airport('zero',0), airport('fraction',2.5)];
  const result = generateDamageEvents(params(), candidates);
  assert.equal(result.ineligible.length,4);
  assert.ok(result.ineligible.every(x => x.reason));
  assert.throws(() => generateDamageEvents(params({scope:'manual',airportIds:['A1','small']}),candidates), /small/);
  assert.throws(() => generateDamageEvents(params({scope:'manual',airportIds:['missing']}),candidates), /missing/);
  assert.throws(() => generateDamageEvents(params({scope:'random',count:4}),candidates));
  const random = generateDamageEvents(params({scope:'random',count:2}),candidates);
  assert.equal(new Set(random.events.map(e => e.target.airport_id)).size,2);
});

test('extreme considers the complete capacity pair set, even with overlapping ratio ranges', () => {
  const stages = [{ratio:[40,60],duration:[2,2]}, {ratio:null,duration:[1,1]}, {ratio:[40,50],duration:[2,2]}];
  for(let seed=0;seed<60;seed++) {
    const result = generateDamageEvents(params({kind:'extreme',stages,seed:String(seed),end:12}),[airport('A',10)]);
    assert.deepEqual(result.events.map(e=>e.effect.remaining_capacity_per_window), [4,0,5]);
  }
  stages[2].ratio=[10,30];
  assert.throws(() => generateDamageEvents(params({kind:'extreme',stages}),[airport('A',10)]));
});

test('decimal percentage endpoints are exact, without rounding across the interval', () => {
  const p = params({stages:[{ratio:[64.4,64.4],duration:[2,2]}]});
  const result = generateDamageEvents(p,[airport('A',250)]);
  assert.equal(result.events[0].effect.remaining_capacity_per_window,161);
  assert.throws(()=>generateDamageEvents(params({stages:[{ratio:[64.4001,64.7999],duration:[2,2]}]}),[airport('A',250)]));
});

test('short ranges constrain durations without truncating; no implicit default or negative windows', () => {
  for (const [kind, preset] of Object.entries(DAMAGE_PRESETS)) {
    const minimum = preset.stages.reduce((n,s)=>n+s.duration[0],0);
    const p=params({kind,stages:structuredClone(preset.stages),end:7+minimum,offset:100});
    const result=generateDamageEvents(p,airports);
    assert.ok(result.events.every(e=>e.start_slot>=7&&e.end_slot<=p.end));
    assert.throws(()=>generateDamageEvents({...p,end:p.end-1},airports));
  }
  for(const extra of [{start:''},{end:null},{start:-1},{offset:-1},{offset:1.5},{seed:''}]) {
    assert.throws(()=>generateDamageEvents(params(extra),airports));
  }
});

test('allocation leaves existing malformed drafts untouched and is independent of generation', () => {
  const events=generateDamageEvents(params(),airports).events;
  const existing=[{event_id:'P1',sequence:'10'},{event_id:' manual ',sequence:''},{event_id:'bad',sequence:'invalid'}];
  const before=structuredClone(existing);
  const assigned=numberDamageEvents(events,existing);
  assert.deepEqual(existing,before);
  assert.equal(assigned[0].event_id,'P2');
  assert.equal(assigned[0].sequence,11);
  assert.equal(new Set(assigned.map(e=>e.event_id)).size,assigned.length);
  assert.deepEqual(assigned.map(({event_id,sequence,...e})=>e),events);
  assert.deepEqual(events,generateDamageEvents(params(),airports).events);
});
