import { apiFetch, ApiError, saveBlob } from './api-client.js';

const STAGE_GROUPS = [
  { key: 'data_preparation', label: '数据准备' },
  // Candidate generation and LP quick evaluation may interleave in the backend.
  { key: 'candidate_activity', label: '候选搜索与快速评估' },
  { key: 'exact_optimization', label: '精确求解' },
  { key: 'persistence', label: '结果持久化' },
];

const PREFERENCE_LABELS = {
  sortie_max: '出动架次优先',
  resource_min: '资源消耗优先',
  time_min: '时间代价优先',
  custom: '自定义权重',
};

const STATUS_LABELS = {
  queued: '排队中',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
};

const state = {
  situations: [],
  situationDetail: null,
  validation: null,
  validationFingerprint: null,
  runs: [],
  currentRun: null,
  events: [],
  afterSeq: 0,
  eventTimer: null,
  listTimer: null,
  view: 'overview',
  inspectingRunId: null,
  permissions: new Set(),
  accountReady: false,
  logAutoScroll: true,
};

const $ = (id) => document.getElementById(id);
const refs = {
  pageMessage: $('pageMessage'),
  situation: $('situationSelect'),
  situationMeta: $('situationMeta'),
  damage: $('damageSelect'),
  preference: $('preferenceSelect'),
  customAlpha: $('customAlpha'),
  alphaSortie: $('alphaSortie'),
  alphaResource: $('alphaResource'),
  alphaTime: $('alphaTime'),
  clusterEnabled: $('clusterEnabled'),
  clusterSize: $('clusterSize'),
  coreAirportOptions: $('coreAirportOptions'),
  aircraftWeightOptions: $('aircraftWeightOptions'),
  mipTimeLimit: $('mipTimeLimit'),
  advancedSummary: $('advancedSummary'),
  validationSummary: $('validationSummary'),
  validationChecks: $('validationChecks'),
  validateButton: $('validateButton'),
  submitButton: $('submitButton'),
  currentTitle: $('currentTitle'),
  returnLiveButton: $('returnLiveButton'),
  currentEmpty: $('currentEmpty'),
  currentContent: $('currentContent'),
  runMeta: $('runMeta'),
  progressStage: $('progressStage'),
  progressLabel: $('progressLabel'),
  stageFlow: $('stageFlow'),
  activityBar: $('activityBar'),
  logTools: $('logTools'),
  logAutoScroll: $('logAutoScroll'),
  logLatestButton: $('logLatestButton'),
  logCopyButton: $('logCopyButton'),
  logExportButton: $('logExportButton'),
  logBox: $('logBox'),
  overviewTab: $('overviewTab'),
  logTab: $('logTab'),
  cancelButton: $('cancelButton'),
  queueCount: $('queueCount'),
  queueBody: $('queueBody'),
  historyCount: $('historyCount'),
  historyBody: $('historyBody'),
};

function showMessage(text, kind = 'error') {
  refs.pageMessage.textContent = text;
  refs.pageMessage.className = `inline-message ${kind}`;
}

function clearMessage() {
  refs.pageMessage.textContent = '';
  refs.pageMessage.className = 'inline-message hidden';
}

function can(permission) {
  return state.permissions.has(permission);
}

function applyRunPermissionState() {
  const executable = !state.accountReady || can('runs.execute');
  refs.validateButton.disabled = !executable;
  if (!executable) {
    refs.submitButton.disabled = true;
    refs.validateButton.title = '当前账号只有查看权限';
    refs.submitButton.title = '当前账号只有查看权限';
  } else {
    refs.validateButton.removeAttribute('title');
    refs.submitButton.removeAttribute('title');
  }
}

function valueNumber(input, field) {
  const text = input.value.trim();
  if (!text) throw new ApiError(`请填写${field}`, { code: 'FORM_INCOMPLETE' });
  const value = Number(text);
  if (!Number.isFinite(value) || value <= 0) throw new ApiError(`${field}必须为正数`, { code: 'FORM_INVALID' });
  return value;
}

function selectedCoreAirports() {
  return [...refs.coreAirportOptions.querySelectorAll('input[type="checkbox"]:checked')].map((el) => el.value);
}

function aircraftWeights() {
  const result = {};
  refs.aircraftWeightOptions.querySelectorAll('input[data-aircraft-weight]').forEach((input) => {
    const text = input.value.trim();
    if (!text) return;
    const value = Number(text);
    if (!Number.isFinite(value) || value <= 0) {
      throw new ApiError(`机型 ${input.dataset.aircraftWeight} 的权重必须为正数`, { code: 'FORM_INVALID' });
    }
    result[input.dataset.aircraftWeight] = value;
  });
  return result;
}

function buildRunConfig() {
  const situationId = refs.situation.value;
  if (!situationId) throw new ApiError('请选择情境', { code: 'FORM_INCOMPLETE' });
  const mode = refs.preference.value;
  if (!mode) throw new ApiError('请选择优化偏好', { code: 'FORM_INCOMPLETE' });

  const clusterEnabled = refs.clusterEnabled.checked;
  const config = {
    damage_scenario_id: refs.damage.value || null,
    preference_mode: mode,
    cluster_enabled: clusterEnabled,
    cluster_size: null,
    core_airports: [],
    aircraft_type_weight: aircraftWeights(),
    mip_time_limit_s: valueNumber(refs.mipTimeLimit, '求解时限'),
  };
  if (clusterEnabled) {
    const size = Number(refs.clusterSize.value);
    if (!Number.isInteger(size) || size < 1 || size > 8) {
      throw new ApiError('组群规模必须为 1–8 的整数', { code: 'FORM_INVALID' });
    }
    config.cluster_size = size;
    config.core_airports = selectedCoreAirports();
  }
  if (mode === 'custom') {
    config.alpha = [
      valueNumber(refs.alphaSortie, '出动权重'),
      valueNumber(refs.alphaResource, '资源权重'),
      valueNumber(refs.alphaTime, '时间权重'),
    ];
  }
  return { situation_id: situationId, run_config: config };
}

function formFingerprint() {
  try {
    return JSON.stringify(buildRunConfig());
  } catch (_) {
    return null;
  }
}

function invalidateValidation() {
  state.validation = null;
  state.validationFingerprint = null;
  refs.submitButton.disabled = true;
  refs.validationSummary.textContent = '配置已修改，待重新校验';
  refs.validationSummary.className = 'fold-summary validation-dirty';
}

function setFold(targetId) {
  const all = ['advancedFold', 'validationFold'];
  for (const id of all) {
    const el = $(id);
    const open = id === targetId ? !el.classList.contains('open') : false;
    el.classList.toggle('open', open);
    el.querySelector('.fold-toggle')?.setAttribute('aria-expanded', String(open));
  }
}

function collectAircraftTypes(situation) {
  const ids = new Set();
  for (const item of situation?.airports || []) {
    for (const row of item?.operational_profile?.aircraft_support || []) ids.add(row.aircraft_type_id);
  }
  for (const mission of situation?.missions || []) {
    for (const row of mission?.aircraft_requirements || []) ids.add(row.aircraft_type_id);
  }
  return [...ids].sort();
}

function renderSituationDetail(situation) {
  const airports = situation?.airports || [];
  const missions = situation?.missions || [];
  refs.situationMeta.textContent = `${airports.length} 个机场 · ${missions.length} 个任务`;

  refs.damage.replaceChildren();
  const noDamage = document.createElement('option');
  noDamage.value = '';
  noDamage.textContent = '无损毁';
  refs.damage.append(noDamage);
  for (const scenario of situation?.damage_scenarios || []) {
    const option = document.createElement('option');
    option.value = scenario.damage_scenario_id;
    option.textContent = scenario.name || scenario.damage_scenario_id;
    refs.damage.append(option);
  }
  refs.damage.disabled = false;

  refs.coreAirportOptions.replaceChildren();
  if (!airports.length) {
    refs.coreAirportOptions.className = 'choice-grid empty-box';
    refs.coreAirportOptions.textContent = '当前情境没有机场';
  } else {
    refs.coreAirportOptions.className = 'choice-grid';
    for (const item of airports) {
      const airport = item.airport;
      const label = document.createElement('label');
      label.className = 'choice-item';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = airport.airport_id;
      input.disabled = !refs.clusterEnabled.checked;
      input.addEventListener('change', () => {
        const checked = selectedCoreAirports();
        if (checked.length > 2) {
          input.checked = false;
          showMessage('核心机场最多选择 2 个', 'warning');
        }
        invalidateValidation();
        updateAdvancedSummary();
      });
      const text = document.createElement('span');
      text.textContent = `${airport.airport_name}（${airport.airport_id}）`;
      label.append(input, text);
      refs.coreAirportOptions.append(label);
    }
  }

  refs.aircraftWeightOptions.replaceChildren();
  const types = collectAircraftTypes(situation);
  if (!types.length) {
    refs.aircraftWeightOptions.className = 'weight-grid empty-box';
    refs.aircraftWeightOptions.textContent = '当前情境没有可配置机型';
  } else {
    refs.aircraftWeightOptions.className = 'weight-grid';
    for (const id of types) {
      const row = document.createElement('label');
      row.className = 'weight-row';
      const text = document.createElement('span');
      text.textContent = id;
      const input = document.createElement('input');
      input.className = 'control';
      input.type = 'number';
      input.min = '0.000001';
      input.step = '0.1';
      input.placeholder = '1.0';
      input.dataset.aircraftWeight = id;
      input.addEventListener('input', () => { invalidateValidation(); updateAdvancedSummary(); });
      row.append(text, input);
      refs.aircraftWeightOptions.append(row);
    }
  }
  updateClusterControls();
  updateAdvancedSummary();
}

async function loadSituations() {
  refs.situation.disabled = true;
  const payload = await apiFetch('/api/situations?limit=500');
  state.situations = payload.items || [];
  refs.situation.replaceChildren();
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = state.situations.length ? '请选择已保存情境' : '没有可运行的已保存情境';
  refs.situation.append(placeholder);
  for (const row of state.situations) {
    const option = document.createElement('option');
    option.value = row.situation_id;
    option.textContent = row.name || row.situation_id;
    refs.situation.append(option);
  }
  refs.situation.disabled = !state.situations.length;
}

async function onSituationChanged() {
  invalidateValidation();
  refs.damage.disabled = true;
  refs.damage.innerHTML = '<option value="">正在读取…</option>';
  refs.situationMeta.textContent = '';
  state.situationDetail = null;
  const id = refs.situation.value;
  if (!id) {
    refs.damage.innerHTML = '<option value="">请先选择情境</option>';
    refs.coreAirportOptions.textContent = '启用组选并选择情境后可设置';
    refs.aircraftWeightOptions.textContent = '选择情境后按涉及机型生成；留空表示 1.0';
    return;
  }
  try {
    const payload = await apiFetch(`/api/situations/${encodeURIComponent(id)}`);
    state.situationDetail = payload.situation;
    renderSituationDetail(payload.situation);
  } catch (error) {
    handleError(error);
    refs.damage.innerHTML = '<option value="">情境读取失败</option>';
  }
}

function updateClusterControls() {
  const enabled = refs.clusterEnabled.checked;
  refs.clusterSize.disabled = !enabled;
  refs.coreAirportOptions.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.disabled = !enabled; });
  if (!enabled) {
    refs.clusterSize.value = '';
    refs.coreAirportOptions.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.checked = false; });
  }
}

function updateAdvancedSummary() {
  const time = refs.mipTimeLimit.value.trim();
  const core = selectedCoreAirports().length;
  const weights = [...refs.aircraftWeightOptions.querySelectorAll('input[data-aircraft-weight]')].filter((x) => x.value.trim()).length;
  const parts = [time ? `求解时限 ${time} 秒` : '求解时限未设置'];
  if (refs.clusterEnabled.checked) parts.push(`核心机场 ${core} 个`);
  parts.push(weights ? `覆盖 ${weights} 个机型权重` : '机型权重使用默认 1.0');
  refs.advancedSummary.textContent = parts.join(' · ');
}

function renderValidation(result) {
  state.validation = result;
  state.validationFingerprint = formFingerprint();
  refs.validationChecks.replaceChildren();
  for (const check of result.checks || []) {
    const row = document.createElement('div');
    row.className = `validation-row ${check.status}`;
    const mark = document.createElement('span');
    mark.className = 'validation-mark';
    mark.textContent = check.status === 'passed' ? '✓' : check.status === 'warning' ? '!' : '×';
    const body = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = check.code || '校验项';
    const msg = document.createElement('span');
    msg.textContent = check.message || '';
    body.append(title, msg);
    row.append(mark, body);
    refs.validationChecks.append(row);
  }
  refs.validationSummary.textContent = result.can_submit ? '校验通过，可以提交运行' : '校验未通过，请处理失败项';
  refs.validationSummary.className = `fold-summary ${result.can_submit ? 'validation-ok' : 'validation-failed'}`;
  refs.submitButton.disabled = !result.can_submit || (state.accountReady && !can('runs.execute'));
  const fold = $('validationFold');
  fold.classList.add('open');
  fold.querySelector('.fold-toggle')?.setAttribute('aria-expanded', 'true');
  $('advancedFold').classList.remove('open');
}

async function validateRun() {
  clearMessage();
  let body;
  try { body = buildRunConfig(); } catch (error) { handleError(error); return; }
  refs.validateButton.disabled = true;
  refs.validateButton.textContent = '正在校验…';
  try {
    const result = await apiFetch('/api/runs/validate', { method: 'POST', body });
    renderValidation(result);
  } catch (error) {
    state.validation = null;
    refs.submitButton.disabled = true;
    handleError(error);
    if (error instanceof ApiError && error.body?.error?.validation) renderValidation(error.body.error.validation);
  } finally {
    refs.validateButton.disabled = false;
    refs.validateButton.textContent = '校验运行条件';
  }
}

async function submitRun() {
  clearMessage();
  const fingerprint = formFingerprint();
  if (!state.validation?.can_submit || !fingerprint || fingerprint !== state.validationFingerprint) {
    invalidateValidation();
    showMessage('配置已变化，请重新校验后再运行', 'warning');
    return;
  }
  let body;
  try { body = buildRunConfig(); } catch (error) { handleError(error); return; }
  const validatedInputHash = state.validation?.validated_input_hash;
  if (typeof validatedInputHash !== 'string' || validatedInputHash.length !== 64) {
    invalidateValidation();
    showMessage('校验结果缺少服务器输入指纹，请重新校验后再运行', 'warning');
    return;
  }
  body.expected_input_hash = validatedInputHash;
  refs.submitButton.disabled = true;
  refs.submitButton.textContent = '正在提交…';
  try {
    const record = await apiFetch('/api/runs', { method: 'POST', body });
    showMessage(`已提交 ${record.run_id}`, 'success');
    invalidateValidation();
    await refreshRuns();
  } catch (error) {
    handleError(error);
    refs.submitButton.disabled = false;
  } finally {
    refs.submitButton.textContent = '开始运行';
  }
}

function stageKey(event) {
  if (event.stage === 'candidate_generation' || event.stage === 'quick_evaluation') return 'candidate_activity';
  return event.stage;
}

function stageProgress(events) {
  const seen = new Map();
  for (const event of events) seen.set(stageKey(event), event);
  let activeIndex = -1;
  for (let i = 0; i < STAGE_GROUPS.length; i += 1) if (seen.has(STAGE_GROUPS[i].key)) activeIndex = i;
  const terminal = state.currentRun?.status !== 'running';
  return { seen, activeIndex, terminal };
}

function renderStages() {
  refs.stageFlow.replaceChildren();
  const info = stageProgress(state.events);
  STAGE_GROUPS.forEach((group, index) => {
    const item = document.createElement('div');
    const isSeen = info.seen.has(group.key);
    const isActive = index === info.activeIndex && state.currentRun?.status === 'running';
    item.className = `stage ${isSeen ? 'done' : ''} ${isActive ? 'active' : ''}`;
    const circle = document.createElement('div');
    circle.className = 'stage-circle';
    circle.textContent = isSeen && !isActive ? '✓' : String(index + 1);
    const strong = document.createElement('strong');
    strong.textContent = `${index + 1}. ${group.label}`;
    const status = document.createElement('span');
    status.textContent = isActive ? '进行中' : isSeen ? '已发生' : '待开始';
    item.append(circle, strong, status);
    refs.stageFlow.append(item);
  });
  const runStatus = state.currentRun?.status;
  if (runStatus === 'succeeded') {
    refs.progressStage.textContent = '已完成';
    refs.progressLabel.textContent = `4 / ${STAGE_GROUPS.length} · 结果已持久化`;
  } else if (runStatus === 'running' && info.activeIndex >= 0) {
    const group = STAGE_GROUPS[info.activeIndex];
    refs.progressStage.textContent = `阶段 ${info.activeIndex + 1} / ${STAGE_GROUPS.length}`;
    refs.progressLabel.textContent = group.label;
  } else {
    refs.progressStage.textContent = STATUS_LABELS[runStatus] || '等待事件';
    refs.progressLabel.textContent = runStatus === 'queued' ? '等待 worker 领取' : '尚未进入运行阶段';
  }
}

function renderEvents() {
  renderStages();
  const latest = state.events[state.events.length - 1];
  refs.activityBar.replaceChildren();
  const text = document.createElement('span');
  text.textContent = latest ? latest.message : '等待结构化运行事件…';
  refs.activityBar.append(text);

  refs.logBox.replaceChildren();
  if (!state.events.length) {
    refs.logBox.textContent = '暂无运行事件。';
    return;
  }
  for (const event of state.events) {
    const line = document.createElement('div');
    line.className = `log-line level-${String(event.level || '').toLowerCase()}`;
    const time = event.time ? `${event.time} ` : '';
    line.textContent = `${time}${event.level} [${event.stage}/${event.event}] ${event.message}`;
    refs.logBox.append(line);
  }
  if (state.logAutoScroll) refs.logBox.scrollTop = refs.logBox.scrollHeight;
}

function logText() {
  return state.events.map((event) => {
    const time = event.time ? `${event.time} ` : '';
    return `${time}${event.level || ''} [${event.stage || ''}/${event.event || ''}] ${event.message || ''}`.trim();
  }).join('\n');
}

async function copyLog() {
  const text = logText();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    showMessage('运行日志已复制到剪贴板', 'success');
  } catch (_) {
    showMessage('浏览器未允许复制，请手动选择日志内容', 'warning');
  }
}

function exportLog() {
  const text = logText();
  if (!text || !state.currentRun?.run_id) return;
  saveBlob({ blob: new Blob([text], { type: 'text/plain;charset=utf-8' }), filename: `${state.currentRun.run_id}.log.txt` });
}

function jumpToLatestLog() {
  refs.logBox.scrollTop = refs.logBox.scrollHeight;
}

async function retryFailedRun(run) {
  if (!can('runs.execute')) { showMessage('当前账号没有运行执行权限', 'warning'); return; }
  if (!window.confirm(`确认按 ${run.run_id} 的冻结输入重新创建一次运行？`)) return;
  try {
    const retried = await apiFetch(`/api/runs/${encodeURIComponent(run.run_id)}/retry`, { method: 'POST', body: {} });
    showMessage(`已按原冻结输入创建 ${retried.run_id}`, 'success');
    await refreshRuns();
  } catch (error) { handleError(error); }
}

function runMetaRow(label, value, cls = '') {
  const row = document.createElement('div');
  row.className = 'meta-row';
  const key = document.createElement('span'); key.textContent = label;
  const val = document.createElement('strong'); val.textContent = value || '—'; if (cls) val.className = cls;
  row.append(key, val); return row;
}

function renderCurrentRun() {
  const run = state.currentRun;
  const hasRun = Boolean(run);
  const inspecting = Boolean(state.inspectingRunId);
  refs.currentTitle.textContent = inspecting ? '历史运行检查' : '当前运行';
  refs.returnLiveButton.classList.toggle('hidden', !inspecting);
  refs.currentEmpty.textContent = inspecting ? '历史运行不可用。' : '当前没有运行中的任务。';
  refs.currentEmpty.classList.toggle('hidden', hasRun);
  refs.currentContent.classList.toggle('hidden', !hasRun);
  if (!run) return;

  refs.runMeta.replaceChildren(
    runMetaRow('Run ID', run.run_id),
    runMetaRow('情境', run.situation?.name || run.situation_id),
    runMetaRow('开始时间', run.started_at || '等待 worker'),
    runMetaRow('当前状态', STATUS_LABELS[run.status] || run.status, `status-${run.status}`),
  );
  refs.cancelButton.classList.toggle('hidden', inspecting);
  refs.cancelButton.disabled = inspecting || run.status !== 'running' || run.cancel_requested;
  refs.cancelButton.textContent = run.cancel_requested ? '已请求取消' : '取消运行';
  renderEvents();
}

function td(text, className = '') {
  const el = document.createElement('td');
  el.textContent = text ?? '—';
  if (className) el.className = className;
  return el;
}

function damageLabel(run) {
  const id = run.run_config?.damage_scenario_id;
  return id || '无损毁';
}

function clusterLabel(run) {
  return run.run_config?.cluster_enabled ? String(run.run_config?.cluster_size ?? '—') : '关闭';
}

function renderQueue() {
  const rows = state.runs.filter((r) => r.status === 'queued');
  refs.queueCount.textContent = String(rows.length);
  refs.queueBody.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement('tr'); const cell = td('暂无排队任务', 'table-empty'); cell.colSpan = 7; tr.append(cell); refs.queueBody.append(tr); return;
  }
  for (const run of rows) {
    const tr = document.createElement('tr');
    tr.append(
      td(run.run_id), td(run.situation?.name || run.situation_id), td(damageLabel(run)),
      td(PREFERENCE_LABELS[run.run_config?.preference_mode] || run.run_config?.preference_mode),
      td(clusterLabel(run)), td(run.created_at), td(STATUS_LABELS[run.status], `status-${run.status}`),
    );
    refs.queueBody.append(tr);
  }
}

function renderHistory() {
  const rows = state.runs.filter((r) => ['succeeded', 'failed', 'cancelled'].includes(r.status));
  refs.historyCount.textContent = String(rows.length);
  refs.historyBody.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement('tr'); const cell = td('暂无历史运行', 'table-empty'); cell.colSpan = 9; tr.append(cell); refs.historyBody.append(tr); return;
  }
  for (const run of rows) {
    const tr = document.createElement('tr');
    const actions = document.createElement('td');
    actions.className = 'table-actions';
    const logButton = document.createElement('button');
    logButton.type = 'button'; logButton.className = 'link-button'; logButton.textContent = run.status === 'failed' ? '查看错误' : '查看日志';
    logButton.addEventListener('click', () => inspectHistoricalRun(run));
    actions.append(logButton);
    if (run.status === 'failed' && can('runs.execute')) {
      const retryButton = document.createElement('button');
      retryButton.type = 'button'; retryButton.className = 'link-button'; retryButton.textContent = '重试';
      retryButton.title = '复制该失败 Run 的不可变冻结输入并创建新 Run';
      retryButton.addEventListener('click', () => retryFailedRun(run));
      actions.append(retryButton);
    }
    if (run.status === 'succeeded') {
      const resultButton = document.createElement('button');
      resultButton.type = 'button'; resultButton.className = 'link-button'; resultButton.textContent = '查看结果';
      resultButton.title = '打开该成功 Run 的单次运行仪表盘';
      resultButton.addEventListener('click', () => {
        window.location.assign(`/runs/${encodeURIComponent(run.run_id)}`);
      });
      actions.prepend(resultButton);
    }
    tr.append(
      td(run.run_id), td(run.situation?.name || run.situation_id), td(damageLabel(run)),
      td(PREFERENCE_LABELS[run.run_config?.preference_mode] || run.run_config?.preference_mode),
      td(clusterLabel(run)), td(run.started_at), td(run.finished_at),
      td(STATUS_LABELS[run.status], `status-${run.status}`), actions,
    );
    refs.historyBody.append(tr);
  }
}

async function inspectHistoricalRun(run) {
  try {
    const payload = await apiFetch(`/api/runs/${encodeURIComponent(run.run_id)}/events?after_seq=0&limit=1000`);
    state.inspectingRunId = run.run_id;
    state.currentRun = run;
    state.events = payload.events || [];
    state.afterSeq = payload.next_after_seq || 0;
    renderCurrentRun();
    switchView('log');
  } catch (error) { handleError(error); }
}

function returnToLiveRun() {
  state.inspectingRunId = null;
  state.currentRun = null;
  state.events = [];
  state.afterSeq = 0;
  switchView('overview');
  refreshRuns();
}

async function refreshRuns() {
  try {
    const payload = await apiFetch('/api/runs?limit=100');
    state.runs = payload.items || [];
    const liveRun = state.runs.find((r) => r.status === 'running') || null;
    if (state.inspectingRunId) {
      const inspected = state.runs.find((r) => r.run_id === state.inspectingRunId) || null;
      if (inspected) state.currentRun = inspected;
    } else {
      const currentChanged = liveRun?.run_id !== state.currentRun?.run_id || state.currentRun?.status !== liveRun?.status;
      if (currentChanged) {
        state.currentRun = liveRun;
        state.events = [];
        state.afterSeq = 0;
      } else if (liveRun) {
        state.currentRun = liveRun;
      } else if (state.currentRun?.status === 'running') {
        state.currentRun = null;
        state.events = [];
        state.afterSeq = 0;
      }
    }
    renderQueue();
    renderHistory();
    renderCurrentRun();
    if (liveRun && !state.inspectingRunId) await refreshEvents();
  } catch (error) { handleError(error); }
}

async function refreshEvents() {
  if (!state.currentRun?.run_id || state.currentRun.status !== 'running') return;
  try {
    const payload = await apiFetch(`/api/runs/${encodeURIComponent(state.currentRun.run_id)}/events?after_seq=${state.afterSeq}&limit=200`);
    const incoming = payload.events || [];
    if (incoming.length) {
      state.events.push(...incoming);
      state.afterSeq = payload.next_after_seq || state.afterSeq;
      renderEvents();
    }
  } catch (error) { handleError(error, { quietAuth: true }); }
}

async function cancelCurrent() {
  if (!state.currentRun?.run_id) return;
  if (!window.confirm(`确认取消 ${state.currentRun.run_id}？\n当前求解若正在阻塞，将在后端重新获得控制权后完成取消。`)) return;
  try {
    await apiFetch(`/api/runs/${encodeURIComponent(state.currentRun.run_id)}/cancel`, { method: 'POST', body: {} });
    showMessage('取消请求已提交', 'warning');
    await refreshRuns();
  } catch (error) { handleError(error); }
}

function switchView(view) {
  state.view = view;
  const log = view === 'log';
  refs.overviewTab.classList.toggle('active', !log);
  refs.logTab.classList.toggle('active', log);
  refs.overviewTab.setAttribute('aria-selected', String(!log));
  refs.logTab.setAttribute('aria-selected', String(log));
  refs.logTools.classList.toggle('hidden', !log);
  refs.logBox.classList.toggle('hidden', !log);
  refs.activityBar.classList.toggle('hidden', log);
}

function handleError(error, { quietAuth = false } = {}) {
  console.error(error);
  if (error instanceof ApiError) {
    if (quietAuth && ['AUTHENTICATION_REQUIRED', 'PERMISSION_DENIED'].includes(error.code)) return;
    showMessage(`${error.message}${error.field ? `（${error.field}）` : ''}`, error.status >= 500 ? 'error' : 'warning');
  } else {
    showMessage('发生未预期的前端错误', 'error');
  }
}

function bindFormEvents() {
  document.querySelectorAll('.fold-toggle').forEach((button) => button.addEventListener('click', () => setFold(button.dataset.fold)));
  refs.situation.addEventListener('change', onSituationChanged);
  refs.preference.addEventListener('change', () => {
    refs.customAlpha.classList.toggle('hidden', refs.preference.value !== 'custom');
    invalidateValidation();
  });
  refs.clusterEnabled.addEventListener('change', () => { updateClusterControls(); invalidateValidation(); updateAdvancedSummary(); });
  [refs.clusterSize, refs.mipTimeLimit, refs.alphaSortie, refs.alphaResource, refs.alphaTime, refs.damage].forEach((el) => {
    el.addEventListener('input', () => { invalidateValidation(); updateAdvancedSummary(); });
    el.addEventListener('change', () => { invalidateValidation(); updateAdvancedSummary(); });
  });
  refs.validateButton.addEventListener('click', validateRun);
  refs.submitButton.addEventListener('click', submitRun);
  refs.cancelButton.addEventListener('click', cancelCurrent);
  refs.overviewTab.addEventListener('click', () => switchView('overview'));
  refs.logTab.addEventListener('click', () => switchView('log'));
  refs.returnLiveButton.addEventListener('click', returnToLiveRun);
  refs.logAutoScroll.addEventListener('change', () => { state.logAutoScroll = refs.logAutoScroll.checked; if (state.logAutoScroll) jumpToLatestLog(); });
  refs.logLatestButton.addEventListener('click', jumpToLatestLog);
  refs.logCopyButton.addEventListener('click', copyLog);
  refs.logExportButton.addEventListener('click', exportLog);
}

globalThis.addEventListener('app:account-ready', (event) => {
  state.permissions = new Set(event.detail?.permissions || []);
  state.accountReady = true;
  applyRunPermissionState();
  renderHistory();
});

async function init() {
  bindFormEvents();
  applyRunPermissionState();
  try {
    await Promise.all([loadSituations(), refreshRuns()]);
  } catch (error) { handleError(error); }
  state.listTimer = window.setInterval(refreshRuns, 5000);
  state.eventTimer = window.setInterval(refreshEvents, 1500);
}

window.addEventListener('beforeunload', () => {
  if (state.listTimer) window.clearInterval(state.listTimer);
  if (state.eventTimer) window.clearInterval(state.eventTimer);
});

document.addEventListener('DOMContentLoaded', init);
