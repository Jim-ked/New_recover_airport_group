import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { formatRunIdentity, runIdentityMarkup } from '../../frontend/static/js/utils/run-identity.js';

const source = readFileSync(new URL('../../frontend/static/js/modules/results.js', import.meta.url), 'utf8');
const renderers = source.slice(source.indexOf('const recordModeLabels'), source.indexOf('function renderAssessmentWorkspace'));
const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
function render(items) {
  const refs = { assessmentComparisons: { innerHTML: '' } };
  const context = vm.createContext({ refs, state: { assessment: { comparisons: { items } } },
    esc, formatRunIdentity, runIdentityMarkup, shortRunId: value => value || '', displayValue: value => value == null ? '未记录' : String(value) });
  vm.runInContext(`${renderers}\nrenderAssessmentComparisons();`, context);
  return refs.assessmentComparisons.innerHTML;
}

test('record history exposes full Run IDs, author and provenance while retaining saved zero/null facts', () => {
  const html = render([{ id: 'CMP-1', comparison_mode: 'configuration', base_run_id: 'RUN-BASE-FULL', compared_run_id: 'RUN-OTHER-FULL',
    conclusion: '<script>untrusted</script>', created_by_user_id: 'U1', created_at: '2026-10-08', provenance_status: 'unavailable', provenance_reasons: ['source_missing'],
    result_json: { schema_version: 'comparison-record.v1', focus_comparison: { conclusions: ['Saved'],
      values: { fulfilled_sorties_total: { baseline: 0, current: null, delta: null } },
      objective_components: { J: { baseline: 0, current: 0, delta: null, recorded: true, definition_comparable: false, reasons: ['definition changed'] } } },
      pair_facts: {}, eligibility_evidence: {} } }]);
  assert.match(html, /title="RUN-BASE-FULL"/);
  assert.match(html, /RUN-OTHER-FULL/);
  assert.match(html, /U1/);
  assert.match(html, /unavailable/);
  assert.match(html, /<td>0<\/td><td>未记录<\/td>/);
  assert.match(html, /不可直接比较/);
  assert.match(html, /definition changed/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /重新计算|编辑|删除|分享/);
});

test('history handles empty, inaccessible and unreadable records without inventing facts', () => {
  assert.match(render([]), /暂无结果比较记录，可在结果分析中保存当前重点对照/);
  const hidden = render([{ id: 'CMP-1', comparison_mode: 'damage_effect', created_by_user_id: 'U1', provenance_status: 'unavailable' }]);
  assert.match(hidden, /无权查看/);
  assert.doesNotMatch(hidden, /<summary>查看保存事实/);
  const unreadable = render([{ comparison_mode: 'multi_scenario', base_run_id: 'A', compared_run_id: 'B', result_json: '{bad', provenance_status: 'unavailable' }]);
  assert.match(unreadable, /保存事实不可读取/);
});

test('long summaries are separated from full width detail and saved evidence remains readable', () => {
  const conclusion = '保存的正式长结论；'.repeat(80);
  const html = render([{ id: 'CMP-2', comparison_mode: 'damage_effect', base_run_id: 'BASE', compared_run_id: 'OTHER',
    conclusion, created_at: '2026-10-08T08:09:57+00:00', provenance_status: 'current',
    result_json: { schema_version: 'comparison-record.v1', focus_comparison: { conclusions: [conclusion], values: {} },
      pair_facts: { base: { scheduled: 0 } }, eligibility_evidence: { qualified: true } } }]);
  assert.match(html, /class="comparison-record-summary"/);
  assert.match(html, /class="comparison-record-meta"/);
  assert.match(html, /class="comparison-record-detail-body"/);
  assert.match(html, /来源有效 \(current\)/);
  assert.match(html, /2026-10-08 16:09/);
  assert.match(html, /qualified/);
  assert.doesNotMatch(html, /<pre>/);
});

test('each provenance status is displayed as returned', () => {
  for (const status of ['current', 'stale', 'unavailable']) {
    assert.match(render([{ provenance_status: status }]), new RegExp(`\\(${status}\\)`));
  }
});


test('saved identities display frozen scheme, time and trace as separate rows', () => {
  const html = render([{ comparison_mode: 'multi_scenario', base_run_id: 'A', compared_run_id: 'B',
    result_json: { schema_version: 'comparison-record.v1', labels: { runs: {
      A: { display_identity: { title: 'Saved Damage', situation_title: 'Situation · Saved Damage',
        subtitle: '出动优先 · 组群关闭 · 10-10 13:42', short_id: 'R-7193A5B2', detail: 'A full config' } }
    } } } }]);
  assert.match(html, /<b>Saved Damage<\/b><small>出动优先/);
  assert.match(html, /13:42<\/small><small class="run-trace">R-7193A5B2/);
  assert.doesNotMatch(html, /D0/);
});
