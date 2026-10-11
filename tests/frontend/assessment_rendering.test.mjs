import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../../frontend/static/js/modules/results.js', import.meta.url), 'utf8');
const renderers = source.slice(source.includes('function compactAssessmentRun') ? source.indexOf('function compactAssessmentRun') : source.indexOf('function renderAssessmentHeatmap'), source.indexOf('function currentAssessmentEvaluation'));

function render(overview, curves = { series: [] }, runId = 'R1', curveRunId = null) {
  const refs = Object.fromEntries(['assessmentHeatmap', 'assessmentAirportSelect', 'assessmentCurveRunSelect', 'assessmentCurves', 'assessmentCurveLegend'].map(key => [key, { innerHTML: '' }]));
  const context = vm.createContext({
    state: { assessment: { overview, curves, airportId: 'AP1', runId, curveRunId } }, refs,
    esc: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'),
    assessmentScenarioLabel: value => value,
    assessmentAirportName: value => value,
    assessmentCellValue: cell => cell.matrix_value,
    assessmentHeatColor: () => '#123456',
    number: (value, digits) => Number(value).toFixed(digits),
    shortRunId: value => value, formatDecimal: value => String(value), colors: ['red', 'blue'],
  });
  vm.runInContext(`${renderers}\nrenderAssessmentHeatmap(); renderAssessmentCurves();`, context);
  return refs;
}

test('same display label yields two Run columns with distinct stored capacity facts', () => {
  const cell = value => ({ metric_kind: 'capacity_availability_v1', matrix_value: value, source_run_id: 'RUN', effective_windows: 2, unavailable_windows: 0 });
  const refs = render({ airport_ids: ['AP1'], scenario_keys: ['R1', 'R2'], scenario_labels: { R1: 'Same <label>', R2: 'Same <label>' }, run_cells: { AP1: { R1: cell(.2), R2: cell(.8) } }, cells: {} });
  assert.match(refs.assessmentHeatmap.innerHTML, /20\.0%/);
  assert.match(refs.assessmentHeatmap.innerHTML, /80\.0%/);
  assert.match(refs.assessmentHeatmap.innerHTML, /Same &lt;label&gt; · R1/);
  assert.match(refs.assessmentHeatmap.innerHTML, /Same &lt;label&gt; · R2/);
});

test('legacy fields and true empty data remain supported', () => {
  const legacy = render({ airport_ids: ['AP1'], scenario_types: ['medium'], cells: { AP1: { medium: { metric_kind: 'capacity_availability_v1', matrix_value: .4 } } } });
  assert.match(legacy.assessmentHeatmap.innerHTML, /40\.0%/);
  const empty = render({ airport_ids: [], scenario_keys: [], run_cells: {} });
  assert.match(empty.assessmentHeatmap.innerHTML, /暂无评估落库结果/);
  assert.match(empty.assessmentCurves.innerHTML, /当前机场暂无评估曲线/);
});

test('curves default to the evaluation Run with a single legend and real time window labels', () => {
  const refs = render({ airport_ids: ['AP1'], scenario_keys: [], run_cells: {} }, { series: [
    { source_run_id: 'R1', scenario_label: 'Frozen one', metric_kind: 'capacity_availability_v1', steps: [9, 14], values: [.1, .9] },
    { source_run_id: 'R2', scenario_label: 'Frozen two', metric_kind: 'capacity_availability_v1', steps: [1, 2], values: [.2, .8] },
  ] });
  assert.equal((refs.assessmentCurves.innerHTML.match(/<polyline/g) || []).length, 1);
  assert.match(refs.assessmentCurves.innerHTML, /起降容量可用比例/);
  assert.match(refs.assessmentCurveLegend.innerHTML, /Frozen one · R1/);
  assert.doesNotMatch(refs.assessmentCurveLegend.innerHTML, /Frozen two/);
  assert.match(refs.assessmentCurves.innerHTML, />T9</);
  assert.match(refs.assessmentCurves.innerHTML, />T14</);
});

test('21 Runs retain every matrix column, full identity titles and the evaluation marker', () => {
  const keys = Array.from({ length: 21 }, (_, i) => `RUN-${i}`);
  const label = '完整情境 · 重度损毁 · 组群关闭 · sortie_max';
  const refs = render({ airport_ids: ['AP1'], scenario_keys: keys,
    scenario_labels: Object.fromEntries(keys.map(k => [k, label])),
    run_cells: { AP1: Object.fromEntries(keys.map(k => [k, { metric_kind: 'capacity_availability_v1', matrix_value: .4, source_run_id: k }])) } },
    { series: keys.map(k => ({ source_run_id: k, scenario_label: label, steps: [9, 14, 19], values: [0, .5, 1] })) }, 'RUN-12');
  for (const key of keys) assert.match(refs.assessmentHeatmap.innerHTML, new RegExp(`${label} · ${key}`));
  assert.equal((refs.assessmentHeatmap.innerHTML.match(/data-run-id=/g) || []).length, 21);
  assert.match(refs.assessmentHeatmap.innerHTML, /data-run-id="RUN-12"[^>]*aria-current="true"/);
  assert.equal((refs.assessmentCurves.innerHTML.match(/<polyline/g) || []).length, 1);
  assert.equal((refs.assessmentCurveLegend.innerHTML.match(/<span/g) || []).length, 1);
  assert.equal((refs.assessmentCurveRunSelect.innerHTML.match(/<option/g) || []).length, 22);
});

test('explicit curve Run selection updates the series without changing evaluation or hiding missing facts', () => {
  const curves = { series: [
    { source_run_id: 'R1', scenario_label: 'Baseline', steps: [1, 2, 3], values: [0, null, 1] },
    { source_run_id: 'R2', scenario_label: 'Other', steps: [1, 2, 3], values: [.2, .4, .8] },
  ] };
  const refs = render({ airport_ids: ['AP1'] }, curves, 'R1', 'R2');
  assert.match(refs.assessmentCurveLegend.innerHTML, /Other · R2/);
  assert.doesNotMatch(refs.assessmentCurveLegend.innerHTML, /Baseline/);
  const missing = render({ airport_ids: ['AP1'] }, curves, 'R3');
  assert.match(missing.assessmentCurves.innerHTML, /所选 Run 暂无/);
  assert.equal(missing.assessmentCurveLegend.innerHTML, '');
  const gaps = render({ airport_ids: ['AP1'] }, curves, 'R1');
  assert.equal((gaps.assessmentCurves.innerHTML.match(/<polyline/g) || []).length, 2);
});
