import { setCatalogLayer } from './situation-map.js';
import { escapeHtml, refs, state } from './situation-state.js';

let callbacks = {};

export function configurePanels(nextCallbacks) {
  callbacks = { ...callbacks, ...nextCallbacks };
}

export function setInspectorOpen(open) {
  refs.inspector.classList.toggle('closed', !open);
  refs.inspector.setAttribute('aria-hidden', String(!open));
  document.getElementById('situationPage').classList.toggle('inspector-collapsed', !open);
}

export function collapseOverview() {
  refs.overview.classList.remove('open');
  refs.overviewTrigger.setAttribute('aria-expanded', 'false');
}

function inspectorHasTask() {
  if (!state.working) return false;
  if (state.mode !== 'select' || state.selected) return true;
  return Boolean(refs.body.querySelector(
    '#applyAirport,#applyMission,#applyDamageScenario,#applySituationInfo',
  ));
}

function syncEmptyGuide() {
  if (!state.working) {
    refs.emptyGuide.classList.remove('hidden');
    refs.emptyTitle.textContent = '选择或新建情境';
    refs.emptyText.textContent = '从底部情境 Dock 打开已有情境，或创建一个新情境。';
    refs.emptyAction.textContent = '新建情境';
    refs.emptyAction.dataset.action = 'new';
    return;
  }
  if (!state.working.airports.length) {
    refs.emptyGuide.classList.remove('hidden');
    refs.emptyTitle.textContent = '先加入机场';
    refs.emptyText.textContent = '机场是任务和损毁配置的空间基础。';
    refs.emptyAction.textContent = '添加机场';
    refs.emptyAction.dataset.action = 'airport';
    return;
  }
  refs.emptyGuide.classList.add('hidden');
}

export function syncWorkspaceChrome() {
  setInspectorOpen(inspectorHasTask() && !refs.inspector.classList.contains('closed'));
  syncEmptyGuide();
  if (state.mode !== 'select' || state.selected) collapseOverview();
}

export function showConflict() {
  refs.conflict.classList.remove('hidden');
}

export function clearConflict() {
  refs.conflict.classList.add('hidden');
}

function renderSearchResults() {
  const query = refs.search.value.trim().toLowerCase();
  if (!query || !state.working) {
    refs.searchResults.classList.add('hidden');
    refs.searchResults.replaceChildren();
    return;
  }
  const rows = [];
  for (const item of state.working.airports) {
    const airport = item.airport;
    if (`${airport.airport_id} ${airport.airport_name}`.toLowerCase().includes(query)) {
      rows.push({ type: 'airport', id: airport.airport_id, name: airport.airport_name, detail: airport.airport_id });
    }
  }
  for (const mission of state.working.missions) {
    if (`${mission.mission_id} ${mission.name}`.toLowerCase().includes(query)) {
      rows.push({ type: 'mission', id: mission.mission_id, name: mission.name, detail: mission.mission_id });
    }
  }
  refs.searchResults.innerHTML = rows.slice(0, 30).map((row) => `
    <button class="search-result" type="button" data-type="${row.type}" data-id="${escapeHtml(row.id)}">
      <span><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.detail)}</small></span>
      <span class="search-result-type">${row.type === 'airport' ? '机场' : '任务'}</span>
    </button>
  `).join('') || '<div class="search-empty">没有匹配对象。</div>';
  refs.searchResults.classList.remove('hidden');
  refs.searchResults.querySelectorAll('.search-result').forEach((button) => {
    button.addEventListener('click', async () => {
      await callbacks.selectObject?.(button.dataset.type, button.dataset.id, { locate: true });
      closeSearch();
    });
  });
}

function closeSearch() {
  refs.searchPanel.classList.add('hidden');
  refs.searchResults.classList.add('hidden');
  document.getElementById('searchToggleButton').setAttribute('aria-expanded', 'false');
}

function initSearch() {
  const toggle = document.getElementById('searchToggleButton');
  toggle.addEventListener('click', () => {
    const opening = refs.searchPanel.classList.contains('hidden');
    refs.searchPanel.classList.toggle('hidden', !opening);
    toggle.setAttribute('aria-expanded', String(opening));
    if (opening) {
      refs.search.value = '';
      refs.search.focus();
    }
  });
  refs.search.addEventListener('input', renderSearchResults);
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#situationSearchPanel') && !event.target.closest('#searchToggleButton')) {
      closeSearch();
    }
  });
}

function initLayers() {
  const button = document.getElementById('layerScopeButton');
  const panel = document.getElementById('layerScopePanel');
  const status = document.getElementById('layerScopeStatus');
  const close = () => {
    panel.classList.add('hidden');
    button.setAttribute('aria-expanded', 'false');
  };
  button.addEventListener('click', () => {
    const opening = panel.classList.contains('hidden');
    panel.classList.toggle('hidden', !opening);
    button.setAttribute('aria-expanded', String(opening));
    if (opening) status.textContent = '当前情境对象始终显示。';
  });
  document.getElementById('closeLayerScope').addEventListener('click', close);
  for (const [id, kind, label] of [
    ['showAllAirports', 'airports', '基础机场'],
    ['showAllMissions', 'missions', '任务模板'],
  ]) {
    document.getElementById(id).addEventListener('change', async (event) => {
      try {
        status.textContent = event.target.checked ? `正在读取${label}…` : '';
        const count = await setCatalogLayer(kind, event.target.checked);
        status.textContent = event.target.checked ? `已显示 ${count} 个${label}。` : '已关闭该图层。';
      } catch (error) {
        event.target.checked = false;
        await setCatalogLayer(kind, false);
        status.textContent = error?.message || `${label}读取失败。`;
      }
    });
  }
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#layerScopePanel') && !event.target.closest('#layerScopeButton')) close();
  });
}

export function initPanels() {
  initSearch();
  initLayers();
  refs.emptyAction.addEventListener('click', () => {
    if (refs.emptyAction.dataset.action === 'airport') callbacks.setMode?.('airport');
    else refs.newBtn.click();
  });
  document.getElementById('overviewEditSituationInfo').addEventListener('click', () => {
    callbacks.editSituationInfo?.();
  });
  document.getElementById('keepLocalConflict').addEventListener('click', clearConflict);
  document.getElementById('reloadConflict').addEventListener('click', async () => {
    clearConflict();
    await callbacks.reloadSituation?.();
  });
  syncWorkspaceChrome();
}
