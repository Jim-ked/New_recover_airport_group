import test from 'node:test';
import assert from 'node:assert/strict';
import { formatRunIdentity, shortRunId, repeatedRunIds } from '../../frontend/static/js/utils/run-identity.js';

const run = { run_id: 'RUN-7193a5b2afbe44729dd96b7691840a92', situation_id: 'S1',
  situation: { name: '测试情境' }, damage_scenario: { name: '资源损毁方案' },
  started_at: '2026-10-10 05:42:01', run_config: { damage_scenario_id: 'D1', preference_mode: 'sortie_max',
    cluster_enabled: true, cluster_size: 4, core_airports: ['AP179'], algorithm_seed: 42 } };

test('multi identity separates title, configuration/time and trace without a D label', () => {
  const identity = formatRunIdentity(run, { multi: true });
  assert.equal(identity.title, '资源损毁方案');
  assert.match(identity.subtitle, /出动优先.*组群开启\(K=4\).*10-10 13:42/);
  assert.equal(identity.short_id, 'R-7193A5B2');
  assert.doesNotMatch(JSON.stringify(identity), /D0|版本1|最佳|最新/);
  assert.match(identity.detail, /RUN-7193a5b2.*AP179.*42/s);
});

test('non-multi identity, custom alpha and missing metadata remain truthful', () => {
  assert.equal(formatRunIdentity(run).title, '测试情境 · 资源损毁方案');
  const custom = formatRunIdentity({ ...run, started_at: null, created_at: null,
    run_config: { ...run.run_config, preference_mode: 'custom', alpha: [0.2, 0.3, 0.5] } });
  assert.match(custom.subtitle, /自定义.*0.2.*0.3.*0.5.*时间未记录/);
  assert.equal(shortRunId('R0'), 'R0');
  assert.match(formatRunIdentity({ run_id: 'OLD' }).subtitle, /偏好未记录.*组群未记录.*时间未记录/);
});

test('repeated configurations use backend normalized keys, independent of dates or IDs', () => {
  const a = { ...run, display_identity: { configuration_key: 'same' } };
  const b = { ...run, run_id: 'RUN-OTHER', started_at: '2026-10-11 01:00:00', display_identity: { configuration_key: 'same' } };
  const c = { ...run, run_id: 'RUN-DIFFERENT', display_identity: { configuration_key: 'different' } };
  assert.deepEqual([...repeatedRunIds([a, b, c])], [a.run_id, b.run_id]);
});
