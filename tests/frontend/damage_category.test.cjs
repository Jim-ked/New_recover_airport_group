const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/static/js/modules/situations.js'), 'utf8');

for (const category of ['low', 'medium', 'high', 'custom']) {
  test(`applying ${category} writes custom only after successful canonicalization`, async () => {
    const original = { damage_scenario_id: 'D1', name: 'Old', category, events: [] };
    const state = { working: { damage_scenarios: [original] } };
    let reject = true;
    const context = vm.createContext({
      state, writable: () => true,
      $: id => ({ value: id === 'damageScenarioId' ? 'D1' : id === 'damageScenarioName' ? 'Edited' : category }),
      deep: value => JSON.parse(JSON.stringify(value)),
      refs: { body: { querySelectorAll: () => [] } }, eventFromCard: () => {},
      canonicalizeWorking: async candidate => { if (reject) throw Error('invalid'); return candidate; },
      clearPanelDraft() {}, markDirty() {}, showMessage() {}, renderDamageEditor() {}, errText: String,
    });
    vm.runInContext(source.slice(source.indexOf('async function applyDamageScenario('), source.indexOf('\nfunction visibleCandidateAirports(')), context);
    await context.applyDamageScenario('D1');
    assert.equal(state.working.damage_scenarios[0].category, category);
    reject = false;
    await context.applyDamageScenario('D1');
    assert.equal(state.working.damage_scenarios[0].category, 'custom');
    assert.equal(original.category, category);
  });
}
