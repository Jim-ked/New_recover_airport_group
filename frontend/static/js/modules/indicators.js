import { apiFetch, ApiError } from './api-client.js';
import { formatPercent } from './number-display.js';

const DEFAULT_EXPERT_ID = 'default';
const $ = (id) => document.getElementById(id);
const refs = {
  setSelect: $('indicatorSetSelect'),
  setStatus: $('indicatorSetStatus'),
  newSet: $('newIndicatorSet'),
  publish: $('publishIndicatorSet'),
  search: $('indicatorSearch'),
  searchButton: $('indicatorSearchButton'),
  openScore: $('openScoreDrawer'),
  columns: $('indicatorL3'),
  inspector: $('indicatorInspector'),
  inspectorBody: $('indicatorInspectorBody'),
  scoreDrawer: $('scoreDrawer'),
  scoreGroup: $('scoreGroupSelect'),
  scoreProgress: $('scoreGroupProgress'),
  scoreStatus: $('scoreStatus'),
  scoreRows: $('scoreRows'),
  saveScore: $('saveScoreDraft'),
  submitScores: $('submitScores'),
  msg: $('indicatorMessage'),
  setModal: $('indicatorSetModal'),
  newSetName: $('newSetName'),
  newSetVersion: $('newSetVersion'),
  newSetSource: $('newSetSource'),
  newSetDescription: $('newSetDescription'),
  newSetMessage: $('newSetMessage'),
  newSetCancel: $('newSetCancel'),
  newSetConfirm: $('newSetConfirm'),
};

const state = {
  me: null,
  sets: [],
  tree: null,
  search: {
    draft: '',
    applied: '',
  },
  expandedSecondaryByPrimary: new Map(),
  selectedNode: null,
  inspectorMode: null,
  inspectorParent: null,
  inspectorInitial: null,
  scoreExpert: null,
  scoreGroup: null,
  scoreRevision: 0,
  scoreStatus: 'draft',
  scoreMap: new Map(),
  scoreLoadedFor: null,
  scoreOpen: false,
  weightInfo: null,
};

const esc = (value) => String(value ?? '—').replace(
  /[&<>'"]/g,
  (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char],
);
const can = (permission) => Boolean(state.me?.permissions?.includes(permission));
const currentSet = () => state.tree?.indicator_set || null;
const nodes = (level) => (state.tree?.nodes || []).filter((node) => node.level === level);
const l3Enabled = () => nodes(3).filter((node) => node.enabled);

function showMessage(text, type = 'info') {
  refs.msg.textContent = text;
  refs.msg.className = `workspace-message ${type}`;
  refs.msg.classList.remove('hidden');
  clearTimeout(showMessage.timeout);
  showMessage.timeout = setTimeout(() => refs.msg.classList.add('hidden'), 4200);
}

function errorText(error) {
  return error instanceof ApiError
    ? `${error.message}${error.field ? `（${error.field}）` : ''}`
    : '操作失败';
}

function modal(element, open) {
  element.classList.toggle('open', open);
  element.setAttribute('aria-hidden', open ? 'false' : 'true');
}

function inline(element, text, type = 'error') {
  element.textContent = text;
  element.className = `inline-message ${type}`;
  element.classList.remove('hidden');
}

function hideInline(element) {
  element.className = 'inline-message hidden';
  element.textContent = '';
}

function directionLabel(value) {
  return ({ positive: '正向', negative: '负向', neutral: '中性' })[value] || '—';
}

function setDisplayName(set) {
  const name = String(set.name || '').trim();
  const version = String(set.version || '').trim();
  if (!version || name.toLocaleLowerCase().includes(version.toLocaleLowerCase())) return name;
  return `${name} ${version}`;
}

function setUrl(setId) {
  const url = new URL(location.href);
  if (setId) url.searchParams.set('set', setId);
  else url.searchParams.delete('set');
  history.replaceState(null, '', url);
}

function children(parentId, level) {
  return nodes(level)
    .filter((node) => node.parent_id === parentId)
    .sort((a, b) => a.display_order - b.display_order || a.name.localeCompare(b.name, 'zh-CN'));
}

function orderedRoots() {
  return nodes(1).sort((a, b) => a.display_order - b.display_order || a.name.localeCompare(b.name, 'zh-CN'));
}

function orderedL2() {
  return orderedRoots().flatMap((root) => children(root.id, 2));
}

function currentNode() {
  return (state.tree?.nodes || []).find((node) => node.id === state.selectedNode) || null;
}

function currentScoreGroup() {
  return nodes(2).find((node) => node.id === state.scoreGroup) || null;
}

function weightValue(node) {
  if (node.weight != null) return Number(node.weight);
  const weighted = state.weightInfo?.items?.find((item) => item.indicator_id === node.id)?.weight;
  return weighted == null ? null : Number(weighted);
}

function weightLabel(node, precision = 2) {
  const value = weightValue(node);
  return formatPercent(value, { digits: precision });
}

function setOptions() {
  refs.setSelect.innerHTML = state.sets
    .map((s) => `<option value="${esc(s.id)}" data-default="${s.is_default ? 'true' : 'false'}">${esc(setDisplayName(s))}</option>`)
    .join('');
  const published = state.sets.filter((set) => set.status === 'published');
  refs.newSetSource.innerHTML = published
    .map((set) => `<option value="${esc(set.id)}">${esc(setDisplayName(set))}</option>`)
    .join('');
}

function renderSetHeader() {
  const set = currentSet();
  if (!set) return;
  refs.setSelect.value = set.id;
  const showDraft = set.status === 'draft' && !set.is_default;
  refs.setStatus.textContent = '草稿';
  refs.setStatus.className = `badge warning${showDraft ? '' : ' hidden'}`;

  const writable = can('indicators.write');
  const canClone = writable && set.status === 'published';
  const canPublish = writable && set.status === 'draft' && !set.is_default;
  refs.newSet.classList.toggle('hidden', !canClone);
  refs.publish.classList.toggle('hidden', !canPublish);
}

function matchesSearch(node) {
  const query = state.search.applied.trim().toLocaleLowerCase();
  if (!query) return true;
  return `${node.name || ''} ${node.code || ''}`.toLocaleLowerCase().includes(query);
}

function initializeAccordionDefaults() {
  for (const root of orderedRoots()) {
    if (state.expandedSecondaryByPrimary.has(root.id)) continue;
    state.expandedSecondaryByPrimary.set(root.id, children(root.id, 2)[0]?.id || null);
  }
}

function applySearch() {
  state.search.draft = refs.search.value;
  state.search.applied = state.search.draft.trim();
  renderHierarchy();
}

function renderHierarchy() {
  const set = currentSet();
  if (!set) {
    refs.columns.innerHTML = '<div class="indicator-column-empty">正在加载指标体系…</div>';
    return;
  }
  const editableDraft = can('indicators.write') && set.status === 'draft' && !set.is_default;
  const searching = Boolean(state.search.applied.trim());
  refs.columns.innerHTML = orderedRoots().map((root) => {
    const groups = children(root.id, 2);
    const visibleGroups = groups.map((group) => ({
      group,
      rows: children(group.id, 3).filter(matchesSearch),
    })).filter(({ rows }) => !searching || rows.length > 0);

    const groupMarkup = visibleGroups.map(({ group, rows }) => {
      const expanded = searching || state.expandedSecondaryByPrimary.get(root.id) === group.id;
      const allRows = children(group.id, 3);
      const rowMarkup = expanded && rows.length
        ? rows.map((node) => {
          const showLock = editableDraft && node.is_core;
          return `<button class="indicator-row ${node.id === state.selectedNode ? 'selected' : ''} ${node.enabled ? '' : 'disabled'}" type="button" data-node-id="${esc(node.id)}" title="${esc(node.name)}">
            <span class="indicator-row-name">${esc(node.name)}</span>
            ${showLock ? '<span class="indicator-lock" aria-label="核心指标">🔒</span>' : ''}
            <span class="indicator-row-weight">${esc(weightLabel(node))}</span>
          </button>`;
        }).join('')
        : '<div class="indicator-group-empty">当前分组暂无三级指标</div>';
      return `<section class="indicator-group ${expanded ? 'expanded' : ''}" data-group-id="${esc(group.id)}">
        <button class="indicator-group-toggle" type="button" data-toggle-root="${esc(root.id)}" data-toggle-group="${esc(group.id)}" aria-expanded="${expanded ? 'true' : 'false'}" aria-controls="group-panel-${esc(group.id)}">
          <h3 title="${esc(group.name)}">${esc(group.name)}</h3><span class="indicator-group-count">${allRows.length}</span><span class="indicator-group-chevron" aria-hidden="true">⌄</span>
        </button>
        ${expanded ? `<div class="indicator-group-panel" id="group-panel-${esc(group.id)}"><div class="indicator-list">${rowMarkup}</div>${editableDraft ? `<button class="indicator-add-row" type="button" data-add-parent="${esc(group.id)}">+ 新增三级指标</button>` : ''}</div>` : ''}
      </section>`;
    }).join('');

    return `<section class="indicator-column" data-root-id="${esc(root.id)}">
      <header class="indicator-column-head"><h2>${esc(root.name)}</h2><p>${groups.length} 个二级指标</p></header>
      ${groupMarkup || `<div class="indicator-column-empty">${searching ? '无匹配指标' : '当前一级暂无二级指标'}</div>`}
    </section>`;
  }).join('');
}

function toggleGroup(rootId, groupId) {
  if (state.search.applied.trim()) return;
  const current = state.expandedSecondaryByPrimary.get(rootId);
  state.expandedSecondaryByPrimary.set(rootId, current === groupId ? null : groupId);
  renderHierarchy();
}

function inspectorFormValues() {
  const name = $('inspectorNodeName');
  if (!name) return null;
  return {
    name: name.value,
    code: $('inspectorNodeCode').value,
    direction: $('inspectorNodeDirection').value,
    unit: $('inspectorNodeUnit').value,
    description: $('inspectorNodeDescription').value,
    enabled: $('inspectorNodeEnabled').checked,
    node_kind: $('inspectorNodeKind').value,
  };
}

function inspectorDirty() {
  if (!['edit', 'create'].includes(state.inspectorMode) || !state.inspectorInitial) return false;
  return JSON.stringify(inspectorFormValues()) !== state.inspectorInitial;
}

function confirmDiscardInspector() {
  return !inspectorDirty() || confirm('当前修改尚未保存，确定放弃吗？');
}

function closeInspector(force = false) {
  if (!force && !confirmDiscardInspector()) return false;
  state.selectedNode = null;
  state.inspectorMode = null;
  state.inspectorParent = null;
  state.inspectorInitial = null;
  refs.inspector.classList.remove('open');
  refs.inspector.setAttribute('aria-hidden', 'true');
  refs.inspectorBody.innerHTML = '';
  renderHierarchy();
  return true;
}

function renderInspectorForm(mode) {
  const editing = mode === 'edit';
  const node = editing ? currentNode() : null;
  const parentId = editing ? node?.parent_id : state.inspectorParent;
  const parent = nodes(2).find((item) => item.id === parentId);
  if (!parent) {
    closeInspector(true);
    return;
  }

  refs.inspectorBody.innerHTML = `<header class="inspector-head">
    <div><h2 id="indicatorInspectorTitle">${editing ? '编辑三级指标' : '新增三级指标'}</h2><p>所属二级：${esc(parent.name)}</p></div>
    <button class="inspector-close" id="closeIndicatorInspector" type="button" aria-label="关闭指标详情">×</button>
  </header>
  <div class="inspector-form">
    <div class="field required"><label for="inspectorNodeName">名称</label><input id="inspectorNodeName" class="control" value="${esc(node?.name || '')}"></div>
    <div class="field required"><label for="inspectorNodeCode">业务编码</label><input id="inspectorNodeCode" class="control" value="${esc(node?.code || '')}" ${editing ? 'disabled' : ''}></div>
    <div class="compact-grid">
      <div class="field"><label for="inspectorNodeDirection">方向</label><select id="inspectorNodeDirection" class="control"><option value="">未设置</option><option value="positive" ${node?.direction === 'positive' ? 'selected' : ''}>正向</option><option value="negative" ${node?.direction === 'negative' ? 'selected' : ''}>负向</option><option value="neutral" ${node?.direction === 'neutral' ? 'selected' : ''}>中性</option></select></div>
      <div class="field"><label for="inspectorNodeUnit">单位</label><input id="inspectorNodeUnit" class="control" value="${esc(node?.unit || '')}"></div>
    </div>
    <div class="field"><label for="inspectorNodeDescription">说明</label><textarea id="inspectorNodeDescription" class="control textarea-control" rows="4">${esc(node?.description || '')}</textarea></div>
    <label class="check-line"><input id="inspectorNodeEnabled" type="checkbox" ${node?.enabled ?? true ? 'checked' : ''}>启用该指标</label>
    <details class="advanced-fields"><summary>高级属性</summary><div class="field"><label for="inspectorNodeKind">计算属性</label><select id="inspectorNodeKind" class="control"><option value="ABSTRACT" ${(node?.node_kind || 'ABSTRACT') === 'ABSTRACT' ? 'selected' : ''}>综合</option><option value="DIRECT" ${node?.node_kind === 'DIRECT' ? 'selected' : ''}>直接计算</option></select></div></details>
    <div id="inspectorMessage" class="inline-message hidden"></div>
    <footer class="inspector-form-actions"><button id="cancelInspectorEdit" class="btn ghost" type="button">取消</button><button id="saveInspectorNode" class="btn primary" type="button">保存</button></footer>
  </div>`;

  refs.inspector.classList.add('open');
  refs.inspector.setAttribute('aria-hidden', 'false');
  state.inspectorInitial = JSON.stringify(inspectorFormValues());
  $('closeIndicatorInspector').onclick = () => closeInspector();
  $('cancelInspectorEdit').onclick = () => {
    if (!confirmDiscardInspector()) return;
    if (editing) {
      state.inspectorMode = 'view';
      state.inspectorInitial = null;
      renderInspector();
    } else closeInspector(true);
  };
  $('saveInspectorNode').onclick = saveNode;
  setTimeout(() => $('inspectorNodeName')?.focus(), 0);
}

function renderInspector() {
  if (state.inspectorMode === 'create') {
    renderInspectorForm('create');
    return;
  }
  const node = currentNode();
  if (!node) {
    refs.inspector.classList.remove('open');
    refs.inspector.setAttribute('aria-hidden', 'true');
    refs.inspectorBody.innerHTML = '';
    return;
  }
  if (state.inspectorMode === 'edit') {
    renderInspectorForm('edit');
    return;
  }

  const set = currentSet();
  const parent = nodes(2).find((item) => item.id === node.parent_id);
  const root = nodes(1).find((item) => item.id === parent?.parent_id);
  const draft = set?.status === 'draft' && !set.is_default;
  const x = node;
  const editable = can('indicators.write') && draft && x.level===3 && x.editable && !x.is_core;
  refs.inspectorBody.innerHTML = `<header class="inspector-head">
    <div><h2 id="indicatorInspectorTitle">${esc(node.name)}</h2>${draft && node.is_core ? '<p class="inspector-core">🔒 核心指标</p>' : ''}</div>
    <button class="inspector-close" id="closeIndicatorInspector" type="button" aria-label="关闭指标详情">×</button>
  </header>
  <p class="inspector-definition">${esc(node.description || '尚未填写指标说明。')}</p>
  <dl class="inspector-grid">
    <dt>业务编码</dt><dd>${esc(node.code)}</dd>
    <dt>所属一级</dt><dd>${esc(root?.name || '—')}</dd>
    <dt>所属二级</dt><dd>${esc(parent?.name || '—')}</dd>
    <dt>单位</dt><dd>${esc(node.unit || '—')}</dd>
    <dt>方向</dt><dd>${directionLabel(node.direction)}</dd>
    <dt>状态</dt><dd>${node.enabled ? '启用' : '停用'}</dd>
    <dt>权重</dt><dd>${esc(weightLabel(node, 2))}</dd>
  </dl>
  ${editable ? '<div class="inspector-actions"><button id="deleteSelectedNode" class="btn danger" type="button">删除</button><button id="editSelectedNode" class="btn" type="button">编辑</button></div>' : ''}`;
  refs.inspector.classList.add('open');
  refs.inspector.setAttribute('aria-hidden', 'false');
  $('closeIndicatorInspector').onclick = () => closeInspector();
  const edit = $('editSelectedNode');
  const remove = $('deleteSelectedNode');
  if (edit) edit.onclick = () => { state.inspectorMode = 'edit'; renderInspector(); };
  if (remove) remove.onclick = deleteNode;
}

function selectIndicator(nodeId) {
  if (!confirmDiscardInspector()) return;
  if (state.scoreOpen) closeScoreDrawer();
  state.selectedNode = nodeId;
  state.inspectorMode = 'view';
  state.inspectorParent = null;
  state.inspectorInitial = null;
  renderHierarchy();
  renderInspector();
}

function beginCreateNode(parentId) {
  const set = currentSet();
  if (!(can('indicators.write') && set?.status === 'draft' && !set.is_default)) return;
  if (!confirmDiscardInspector()) return;
  if (state.scoreOpen) closeScoreDrawer();
  state.selectedNode = null;
  state.inspectorMode = 'create';
  state.inspectorParent = parentId;
  state.inspectorInitial = null;
  renderHierarchy();
  renderInspector();
}

function nodePayload() {
  const set = currentSet();
  const editing = state.inspectorMode === 'edit';
  const node = editing ? currentNode() : null;
  const parentId = node?.parent_id || state.inspectorParent;
  const code = $('inspectorNodeCode').value.trim();
  return {
    id: node?.id || `${set.id}:${code}`,
    indicator_set_id: set.id,
    parent_id: parentId,
    code,
    name: $('inspectorNodeName').value.trim(),
    level: 3,
    node_kind: $('inspectorNodeKind').value,
    unit: $('inspectorNodeUnit').value.trim() || null,
    direction: $('inspectorNodeDirection').value || null,
    weight: node?.weight ?? null,
    description: $('inspectorNodeDescription').value.trim() || null,
    is_core: false,
    editable: true,
    enabled: $('inspectorNodeEnabled').checked,
    display_order: node?.display_order ?? children(parentId, 3).length,
  };
}

async function saveNode() {
  const message = $('inspectorMessage');
  hideInline(message);
  const set = currentSet();
  const editing = state.inspectorMode === 'edit';
  const payload = nodePayload();
  if (!payload.name || !payload.code) {
    inline(message, '名称和业务编码不能为空。');
    return;
  }
  const save = $('saveInspectorNode');
  save.disabled = true;
  try {
    if (editing) {
      await apiFetch(`/api/indicators/${encodeURIComponent(payload.id)}`, {
        method: 'PUT',
        body: { indicator: payload, expected_set_revision: set.revision },
      });
    } else {
      await apiFetch('/api/indicators', {
        method: 'POST',
        body: { indicator: payload, expected_set_revision: set.revision },
      });
    }
    state.selectedNode = payload.id;
    state.inspectorMode = 'view';
    state.inspectorParent = null;
    state.inspectorInitial = null;
    await loadTree(set.id, { preserve: true, selectNode: payload.id });
    showMessage(editing ? '指标已更新。' : '三级指标已新增。', 'success');
  } catch (error) {
    inline(message, errorText(error));
  } finally {
    if (document.body.contains(save)) save.disabled = false;
  }
}

async function deleteNode() {
  const set = currentSet();
  const node = currentNode();
  if (!node || node.is_core || !confirm(`删除三级指标“${node.name}”？`)) return;
  try {
    await apiFetch(`/api/indicators/${encodeURIComponent(node.id)}`, {
      method: 'DELETE',
      body: { indicator_set_id: set.id, expected_set_revision: set.revision },
    });
    closeInspector(true);
    await loadTree(set.id, { preserve: true });
    showMessage('三级指标已删除。', 'success');
  } catch (error) {
    showMessage(errorText(error), 'error');
  }
}

function scoreCount(groupId) {
  const rows = children(groupId, 3).filter((node) => node.enabled);
  return { total: rows.length, done: rows.filter((node) => state.scoreMap.has(node.id)).length };
}

function renderScoreGroupOptions() {
  const groups = orderedL2();
  if (!groups.some((group) => group.id === state.scoreGroup)) state.scoreGroup = groups[0]?.id || null;
  refs.scoreGroup.innerHTML = orderedRoots().map((root) => `<optgroup label="${esc(root.name)}">${children(root.id, 2).map((group) => `<option value="${esc(group.id)}">${esc(group.name)}</option>`).join('')}</optgroup>`).join('');
  refs.scoreGroup.value = state.scoreGroup || '';
}

function scoreStatusLabel() {
  if (!state.scoreExpert) return '未配置';
  if (state.scoreRevision === 0 && state.scoreMap.size === 0) return '未评分';
  return state.scoreStatus==='submitted' ? '已提交' : '草稿';
}

function renderScoreHeader() {
  const group = currentScoreGroup();
  const progress = group ? scoreCount(group.id) : { done: 0, total: 0 };
  refs.scoreProgress.textContent = `已填写 ${progress.done} / ${progress.total}`;
  const label = scoreStatusLabel();
  refs.scoreStatus.textContent = label;
  refs.scoreStatus.className = `badge ${label === '已提交' ? 'success' : label === '草稿' ? 'warning' : ''}`;

  const overall = { total: l3Enabled().length, done: l3Enabled().filter((node) => state.scoreMap.has(node.id)).length };
  const writable = can('indicators.score') && Boolean(state.scoreExpert);
  refs.saveScore.disabled = !writable;
  refs.submitScores.disabled = !(writable && overall.total > 0 && overall.done === overall.total);
}

function renderScoreRows() {
  if (!state.scoreExpert) {
    refs.scoreRows.innerHTML = '<div class="score-empty">稳定专家记录不可用，请检查指标服务 bootstrap。</div>';
    renderScoreHeader();
    return;
  }
  const group = currentScoreGroup();
  const rows = group ? children(group.id, 3).filter((node) => node.enabled) : [];
  const writable = can('indicators.score');
  refs.scoreRows.innerHTML = rows.length
    ? rows.map((node) => `<label class="score-row"><span class="score-row-name" title="${esc(node.name)}">${esc(node.name)}</span><input class="control" type="number" min="0" max="100" step="1" inputmode="decimal" aria-label="${esc(node.name)}评分" data-score-id="${esc(node.id)}" value="${state.scoreMap.has(node.id) ? esc(state.scoreMap.get(node.id)) : ''}" ${writable ? '' : 'disabled'}></label>`).join('')
    : '<div class="score-empty">该分组没有启用的三级指标。</div>';

  refs.scoreRows.querySelectorAll('input[data-score-id]').forEach((input) => {
    input.oninput = () => {
      const raw = input.value.trim();
      const value = Number(raw);
      if (raw === '') {
        input.setCustomValidity('');
        state.scoreMap.delete(input.dataset.scoreId);
      } else if (!Number.isFinite(value) || value < 0 || value > 100) {
        input.setCustomValidity('评分必须在 0–100 之间');
        state.scoreMap.delete(input.dataset.scoreId);
      } else {
        input.setCustomValidity('');
        state.scoreMap.set(input.dataset.scoreId, value);
      }
      renderScoreHeader();
    };
  });
  renderScoreHeader();
}

function renderScoreDrawer() {
  renderScoreGroupOptions();
  renderScoreRows();
  syncScoreDrawerState();
}

function syncScoreDrawerState() {
  refs.scoreDrawer.classList.toggle('open', state.scoreOpen);
  refs.scoreDrawer.setAttribute('aria-hidden', state.scoreOpen ? 'false' : 'true');
  refs.openScore.classList.toggle('active', state.scoreOpen);
  refs.openScore.setAttribute('aria-expanded', state.scoreOpen ? 'true' : 'false');
}

function renderAll() {
  renderSetHeader();
  renderHierarchy();
  renderInspector();
  renderScoreDrawer();
}

async function loadSets(preferred = null) {
  const data = await apiFetch('/api/indicator-sets');
  state.sets = data.items || [];
  setOptions();
  const query = new URLSearchParams(location.search).get('set');
  const chosen = preferred || query || state.sets.find((set) => set.is_default)?.id || state.sets[0]?.id;
  if (chosen) await loadTree(chosen);
}

async function loadTree(setId, { preserve = false, selectNode = null } = {}) {
  const previous = preserve
    ? { selectedNode: state.selectedNode, scoreGroup: state.scoreGroup, expandedSecondaryByPrimary: new Map(state.expandedSecondaryByPrimary) }
    : { selectedNode: null, scoreGroup: null, expandedSecondaryByPrimary: new Map() };
  state.tree = await apiFetch(`/api/indicators?indicator_set_id=${encodeURIComponent(setId)}`);
  state.selectedNode = selectNode || previous.selectedNode;
  if (!currentNode()) state.selectedNode = null;
  state.scoreGroup = previous.scoreGroup;
  state.expandedSecondaryByPrimary = previous.expandedSecondaryByPrimary;
  initializeAccordionDefaults();
  state.scoreRevision = 0;
  state.scoreStatus = 'draft';
  state.scoreMap = new Map();
  state.scoreLoadedFor = null;
  state.weightInfo = null;
  setUrl(setId);
  if (currentSet().status === 'published') {
    try {
      state.weightInfo = await apiFetch(`/api/indicator-weights?indicator_set_id=${encodeURIComponent(setId)}`);
    } catch (_) {
      state.weightInfo = null;
    }
  }
  renderAll();
}

async function loadExperts() {
  const data = await apiFetch('/api/experts');
  const stableExpert = (data.items || []).find((expert) => expert.expert_id === DEFAULT_EXPERT_ID);
  state.scoreExpert = stableExpert?.expert_id || null;
  renderScoreDrawer();
}

async function loadScoreSheet() {
  const set = currentSet();
  state.scoreMap = new Map();
  state.scoreRevision = 0;
  state.scoreStatus = 'draft';
  state.scoreLoadedFor = null;
  if (state.scoreExpert && set) {
    const data = await apiFetch(`/api/expert-scores/${encodeURIComponent(state.scoreExpert)}?indicator_set_id=${encodeURIComponent(set.id)}`);
    state.scoreRevision = data.revision;
    state.scoreStatus = data.status;
    for (const row of data.scores || []) state.scoreMap.set(row.indicator_id, row.score);
    state.scoreLoadedFor = set.id;
  }
  renderScoreDrawer();
}

async function openScoreDrawer() {
  if (!closeInspector()) return;
  state.scoreOpen = true;
  renderScoreDrawer();
  if (state.scoreLoadedFor !== currentSet()?.id) await loadScoreSheet();
  if (!state.scoreOpen) return;
  setTimeout(() => {
    if (state.scoreOpen) refs.scoreGroup.focus();
  }, 0);
}

function closeScoreDrawer() {
  state.scoreOpen = false;
  syncScoreDrawerState();
}

async function toggleScoreDrawer() {
  if (state.scoreOpen) {
    closeScoreDrawer();
    return;
  }
  await openScoreDrawer();
}

function openNewSet() {
  if (!can('indicators.write')) return;
  if (state.scoreOpen) closeScoreDrawer();
  hideInline(refs.newSetMessage);
  refs.newSetName.value = '';
  refs.newSetVersion.value = '';
  refs.newSetDescription.value = '';
  const current = currentSet();
  const source = state.sets.find((set) => set.id === current?.id && set.status === 'published')
    || state.sets.find((set) => set.is_default && set.status === 'published')
    || state.sets.find((set) => set.status === 'published');
  refs.newSetSource.value = source?.id || '';
  modal(refs.setModal, true);
  setTimeout(() => refs.newSetName.focus(), 0);
}

async function createSet() {
  hideInline(refs.newSetMessage);
  const source = state.sets.find((set) => set.id === refs.newSetSource.value);
  if (!source || !refs.newSetName.value.trim() || !refs.newSetVersion.value.trim()) {
    inline(refs.newSetMessage, '请填写名称、标识并选择一个已发布指标集。');
    return;
  }
  refs.newSetConfirm.disabled = true;
  try {
    const tree = await apiFetch('/api/indicator-sets/drafts', {
      method: 'POST',
      body: {
        source_indicator_set_id: source.id,
        name: refs.newSetName.value.trim(),
        version: refs.newSetVersion.value.trim(),
        description: refs.newSetDescription.value.trim() || null,
        expected_revision: source.revision,
      },
    });
    modal(refs.setModal, false);
    await loadSets(tree.indicator_set.id);
    showMessage('已创建独立指标集草稿。', 'success');
  } catch (error) {
    inline(refs.newSetMessage, errorText(error));
  } finally {
    refs.newSetConfirm.disabled = false;
  }
}

async function publishSet() {
  const set = currentSet();
  if (!set || set.status !== 'draft' || !confirm(`发布“${set.name}”？发布后该指标集定义将只读，但不会替换系统默认指标集。`)) return;
  if (state.scoreOpen) closeScoreDrawer();
  try {
    await apiFetch(`/api/indicator-sets/${encodeURIComponent(set.id)}/publish`, {
      method: 'POST',
      body: { expected_revision: set.revision },
    });
    await loadSets(set.id);
    showMessage('自建指标集已发布；系统默认 V1.1 保持不变。', 'success');
  } catch (error) {
    showMessage(errorText(error), 'error');
  }
}

async function changeSet() {
  const target = refs.setSelect.value;
  if (!confirmDiscardInspector()) {
    refs.setSelect.value = currentSet()?.id || '';
    return;
  }
  closeInspector(true);
  try {
    await loadTree(target);
    if (state.scoreOpen) await loadScoreSheet();
  } catch (error) {
    showMessage(errorText(error), 'error');
  }
}

async function saveScores(status) {
  if (!state.scoreExpert) return;
  const invalid = refs.scoreRows.querySelector('input:invalid');
  if (invalid) {
    invalid.reportValidity();
    return;
  }
  const setId = currentSet().id;
  const all = l3Enabled();
  const rows = (status==='submitted' ? all : all.filter((node) => state.scoreMap.has(node.id)))
    .map((node) => ({ indicator_id: node.id, score: Number(state.scoreMap.get(node.id)) }));
  try {
    await apiFetch(`/api/expert-scores/${encodeURIComponent(state.scoreExpert)}`, {
      method: 'PUT',
      body: {
        indicator_set_id: setId,
        status,
        scores: rows,
        expected_revision: state.scoreRevision,
      },
    });
    const group = state.scoreGroup;
    await loadTree(setId, { preserve: true });
    state.scoreGroup = group;
    await loadScoreSheet();
    // Backend authority remains: 已提交专家均值, then normalization within 同一二级指标.
    showMessage(status === 'submitted' ? '评分已提交，权重已按归一化规则更新。' : '评分草稿已保存。', 'success');
  } catch (error) {
    showMessage(errorText(error), 'error');
  }
}

function bind() {
  refs.setSelect.onchange = changeSet;
  refs.newSet.onclick = openNewSet;
  refs.publish.onclick = publishSet;
  refs.newSetCancel.onclick = () => modal(refs.setModal, false);
  refs.newSetConfirm.onclick = createSet;
  refs.search.oninput = () => {
    state.search.draft = refs.search.value;
    if (!state.search.draft && state.search.applied) applySearch();
  };
  refs.search.onkeydown = (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    applySearch();
  };
  refs.searchButton.onclick = applySearch;
  refs.columns.onclick = (event) => {
    const toggle = event.target.closest('[data-toggle-group]');
    if (toggle) {
      toggleGroup(toggle.dataset.toggleRoot, toggle.dataset.toggleGroup);
      return;
    }
    const node = event.target.closest('[data-node-id]');
    if (node) selectIndicator(node.dataset.nodeId);
    const add = event.target.closest('[data-add-parent]');
    if (add) beginCreateNode(add.dataset.addParent);
  };
  refs.openScore.onclick = () => toggleScoreDrawer().catch((error) => showMessage(errorText(error), 'error'));
  refs.scoreGroup.onchange = () => {
    state.scoreGroup = refs.scoreGroup.value;
    renderScoreRows();
  };
  refs.saveScore.onclick = () => saveScores('draft');
  refs.submitScores.onclick = () => saveScores('submitted');
  refs.setModal.addEventListener('click', (event) => {
    if (event.target === refs.setModal) modal(refs.setModal, false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (refs.setModal.classList.contains('open')) modal(refs.setModal, false);
    else if (state.scoreOpen) closeScoreDrawer();
    else if (refs.inspector.classList.contains('open')) closeInspector();
  });
}

async function init() {
  try {
    state.search.draft = '';
    state.search.applied = '';
    refs.search.value = '';
    state.me = await apiFetch('/api/me');
    bind();
    await Promise.all([loadSets(), loadExperts()]);
  } catch (error) {
    showMessage(errorText(error), 'error');
    refs.columns.innerHTML = '<div class="indicator-column-empty">指标管理加载失败。</div>';
  }
}

init();
