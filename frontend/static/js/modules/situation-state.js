let root = null;

export let page = null;

export const byId = (id) => root?.querySelector(`#${CSS.escape(id)}`)
  || document.getElementById(id);

function initialState() {
  return {
    me: null,
    list: [],
    working: null,
    savedHash: null,
    persisted: false,
    meta: null,
    dirty: false,
    panelDraftDirty: false,
    mode: 'select',
    selected: null,
    aircraft: [],
    resources: [],
    airportCatalog: [],
    missionCatalog: [],
    missionHistory: [],
    tempAirportIds: new Set(),
    candidateQuery: '',
    candidateRole: '',
    candidateRegion: '',
    draftMissionCoord: null,
  };
}

export const state = initialState();
export const refs = {};

export function bindSituationDom(nextRoot) {
  root = nextRoot;
  page = nextRoot;
  Object.assign(refs, {
    map: byId('situationMap'),
    fallback: byId('situationFallbackMap'),
    fallbackObjects: byId('fallbackObjects'),
    select: byId('situationSelect'),
    meta: byId('situationMeta'),
    saveState: byId('situationSaveState'),
    save: byId('saveSituationButton'),
    del: byId('deleteSituationButton'),
    newBtn: byId('newSituationButton'),
    tools: nextRoot.querySelector('.situation-tools'),
    searchPanel: byId('situationSearchPanel'),
    search: byId('situationSearch'),
    searchResults: byId('situationSearchResults'),
    inspector: byId('situationInspector'),
    inspectorTitle: byId('inspectorTitle'),
    inspectorSubtitle: byId('inspectorSubtitle'),
    panelDraftStatus: byId('panelDraftStatus'),
    body: byId('inspectorBody'),
    close: byId('closeInspector'),
    overview: byId('situationOverview'),
    overviewTrigger: byId('overviewTrigger'),
    overviewCounts: byId('overviewCounts'),
    overviewContent: byId('overviewContent'),
    msg: byId('situationMessage'),
    conflict: byId('situationConflictBar'),
    emptyGuide: byId('situationEmptyGuide'),
    emptyTitle: byId('situationEmptyTitle'),
    emptyText: byId('situationEmptyText'),
    emptyAction: byId('situationEmptyAction'),
    confirmModal: byId('situationConfirmModal'),
    confirmBody: byId('situationConfirmBody'),
    confirmCancel: byId('situationConfirmCancel'),
    confirmAction: byId('situationConfirmAction'),
  });
}

export function resetSituationState() {
  Object.assign(state, initialState());
}

export function releaseSituationDom() {
  root = null;
  page = null;
  for (const key of Object.keys(refs)) delete refs[key];
}

export function escapeHtml(value) {
  return String(value ?? '—').replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;',
  })[character]);
}

export const fieldValue = (value) => value == null ? '' : String(value);
export const numberValue = (value) => value === '' || value == null ? null : Number(value);
export const integerValue = (value) => value === '' || value == null ? null : Number.parseInt(value, 10);
export const clone = (value) => JSON.parse(JSON.stringify(value));
export const writable = () => Boolean(state.me?.permissions?.includes('situations.write'));

export function airportItem(airportId) {
  return state.working?.airports?.find((item) => item.airport.airport_id === airportId);
}

export function missionItem(missionId) {
  return state.working?.missions?.find((item) => item.mission_id === missionId);
}

export function damageScenario(scenarioId) {
  return state.working?.damage_scenarios?.find((item) => item.damage_scenario_id === scenarioId);
}
