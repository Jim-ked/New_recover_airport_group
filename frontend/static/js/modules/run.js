import { apiFetch, ApiError, saveBlob } from './api-client.js';
import { formatInteger, formatPercent, formatSeconds } from './number-display.js';

const STAGE_GROUPS = [
  { key: 'data_preparation', label: '数据准备' },
  { key: 'candidate_generation', label: '候选搜索' },
  { key: 'quick_evaluation', label: '快速评估' },
  { key: 'exact_optimization', label: '精确求解' },
  { key: 'persistence', label: '结果持久化' },
];

const VALIDATION_LABELS = {
  situation_saved: '情境快照',
  airport_presence: '机场数据',
  mission_presence: '任务数据',
  run_configuration: '运行配置',
  od_closure: '航程关系',
  solver_service: '求解器',
  queue_access: '运行队列',
  duplicate_active_run: '重复运行',
};

const ACTIVITY_LABELS = {
  prepare: '正在准备运行输入',
  cluster: '正在进行机场群候选搜索与快速评估',
  paths: '正在构建可行航次路径',
  model: '正在构建精确优化模型',
  solve: '正在执行精确求解',
  solution: '正在校验并整理求解结果',
  complete: '算法求解完成',
  worker_started: 'Worker 已接收运行任务',
  run_succeeded: '结果已生成并完成持久化',
  run_cancelled: '运行已取消',
  run_failed: '运行失败',
};

const PREFERENCE_LABELS = {
  sortie_max: '出动架次优先',
  resource_min: '资源消耗优先',
  time_min: '时间代价优先',
  custom: '自定义权重',
};

const STATUS_LABELS = {
  queued: '等待 Worker',
  running: '运行中',
  succeeded: '运行完成',
  failed: '运行失败',
  cancelled: '已取消',
};

const state = {
  situations: [],
  situationDetail: null,
  validation: null,
  validationFingerprint: null,
  runs: [],
  activeRunId: null,
  lastSubmittedRunId: null,
  activeRun: null,
  activeEvents: [],
  activeAfterSeq: 0,
  inspectRunId: null,
  inspectRun: null,
  inspectEvents: [],
  eventRequest: null,
  eventTimer: null,
  elapsedTimer: null,
  listTimer: null,
  view: 'overview',
  clusterFoldOpen: false,
  clusterHasBeenEnabled: false,
  permissions: new Set(),
  accountReady: false,
  logAutoScroll: true,
  workerStatus: { connected: false, reason: 'unknown' },
};

const $ = (id) => document.getElementById(id);
const refs = {
  pageMessage: $('pageMessage'),
  situation: $('situationSelect'),
  situationMeta: $('situationMeta'),
  damage: $('damageSelect'),
  preferenceGroup: $('preferenceGroup'),
  customAlpha: $('customAlpha'),
  alphaSortie: $('alphaSortie'),
  alphaResource: $('alphaResource'),
  alphaTime: $('alphaTime'),
  clusterEnabled: $('clusterEnabled'),
  clusterFold: $('clusterFold'),
  clusterSummary: $('clusterSummary'),
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
  runMeta: $('runMeta'),
  progressPercent: $('progressPercent'),
  progressStage: $('progressStage'),
  progressLabel: $('progressLabel'),
  stageFlow: $('stageFlow'),
  activityBar: $('activityBar'),
  currentActions: $('currentActions'),
  overviewPane: $('overviewPane'),
  logPane: $('logPane'),
  viewLogButton: $('viewLogButton'),
  returnOverviewButton: $('returnOverviewButton'),
  logAutoScroll: $('logAutoScroll'),
  logLatestButton: $('logLatestButton'),
  logCopyButton: $('logCopyButton'),
  logExportButton: $('logExportButton'),
  logBox: $('logBox'),
  queueCount: $('queueCount'),
  queueBody: $('queueBody'),
  historyCount: $('historyCount'),
  historyBody: $('historyBody'),
};

function preferenceMode() {
  return refs.preferenceGroup.querySelector('input[name="preferenceMode"]:checked')?.value || '';
}

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
  const mode = preferenceMode();
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
    if (size < config.core_airports.length) {
      throw new ApiError('组群规模不能小于已选核心机场数量', { code: 'FORM_INVALID' });
    }
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

function applyFoldState(targetId, open) {
  const fold = $(targetId);
  if (!fold) return;
  fold.classList.toggle('open', open);
  const toggle = fold.querySelector('.fold-toggle');
  toggle?.setAttribute('aria-expanded', String(open));
  const bodyId = toggle?.getAttribute('aria-controls');
  if (bodyId) $(bodyId)?.setAttribute('aria-hidden', String(!open));
}

function setFold(targetId) {
  if (targetId === 'clusterFold' && !refs.clusterEnabled.checked) return;
  const fold = $(targetId);
  if (!fold) return;
  const open = !fold.classList.contains('open');
  if (targetId === 'clusterFold') state.clusterFoldOpen = open;
  applyFoldState(targetId, open);
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
  refs.situationMeta.textContent = `${formatInteger(airports.length)} 个机场 · ${formatInteger(missions.length)} 个任务`;

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
        updateClusterSummary();
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
  updateClusterSummary();
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
    refs.coreAirportOptions.textContent = '选择情境后可设置';
    refs.aircraftWeightOptions.textContent = '选择情境后按涉及机型生成；留空表示 1.0';
    updateClusterSummary();
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
  refs.clusterFold.querySelector('.fold-toggle').disabled = !enabled;
  refs.clusterSize.disabled = !enabled;
  refs.coreAirportOptions.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.disabled = !enabled; });
  applyFoldState('clusterFold', enabled && state.clusterFoldOpen);
  updateClusterSummary();
}

function updateClusterSummary() {
  if (!refs.clusterEnabled.checked) {
    refs.clusterSummary.textContent = '已关闭';
    return;
  }
  const size = Number(refs.clusterSize.value);
  const coreCount = selectedCoreAirports().length;
  const configured = Number.isInteger(size) && size >= 1 && size <= 8 && size >= coreCount;
  refs.clusterSummary.textContent = configured
    ? `已启用 · 规模 ${size} · 核心机场 ${coreCount}`
    : '已启用 · 待配置';
}

function onClusterEnabledChanged() {
  if (refs.clusterEnabled.checked && !state.clusterHasBeenEnabled) {
    state.clusterHasBeenEnabled = true;
    state.clusterFoldOpen = true;
  }
  updateClusterControls();
  invalidateValidation();
}

function updateAdvancedSummary() {
  const time = refs.mipTimeLimit.value.trim() || '120';
  const weights = [...refs.aircraftWeightOptions.querySelectorAll('input[data-aircraft-weight]')].filter((x) => x.value.trim()).length;
  refs.advancedSummary.textContent = `${formatSeconds(Number(time))} · ${weights ? `${formatInteger(weights)}个机型权重覆盖` : '机型权重默认1.0'}`;
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
    title.textContent = VALIDATION_LABELS[check.code] || check.code || '校验项';
    const msg = document.createElement('span');
    msg.textContent = check.message || '';
    body.append(title, msg);
    row.append(mark, body);
    refs.validationChecks.append(row);
  }
  const summary = result.input_summary || {};
  const facts = `${summary.airport_count ?? 0}机场 · ${summary.mission_count ?? 0}任务 · ${summary.od_pair_count ?? 0} OD`;
  const hasWarning = (result.checks || []).some((check) => check.status === 'warning');
  const failedCount = (result.checks || []).filter((check) => check.status === 'failed').length;
  const label = !result.can_submit ? `× ${failedCount}项未通过` : hasWarning ? '! 校验通过，有提示' : '✓ 运行条件通过';
  refs.validationSummary.textContent = `${label} · ${facts}`;
  refs.validationSummary.className = `fold-summary ${!result.can_submit ? 'validation-failed' : hasWarning ? 'validation-warning' : 'validation-ok'}`;
  refs.submitButton.disabled = !result.can_submit || (state.accountReady && !can('runs.execute'));
  const open = !result.can_submit;
  applyFoldState('validationFold', open);
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
    state.lastSubmittedRunId = record.run_id;
    if (!state.activeRun || state.activeRun.status !== 'running') setActiveRun(record);
    showMessage(`已提交 ${shortRunId(record.run_id)}`, 'success');
    invalidateValidation();
    await refreshRuns();
  } catch (error) {
    handleError(error);
    refs.submitButton.disabled = false;
  } finally {
    refs.submitButton.textContent = '开始运行';
  }
}

function displayRun() {
  return state.inspectRunId ? state.inspectRun : state.activeRun;
}

function displayEvents() {
  return state.inspectRunId ? state.inspectEvents : state.activeEvents;
}

function setActiveRun(run) {
  const nextId = run?.run_id || null;
  if (nextId !== state.activeRunId) {
    state.activeEvents = [];
    state.activeAfterSeq = 0;
  }
  state.activeRunId = nextId;
  state.activeRun = run || null;
}

function latestInternalStage(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const internal = event.payload?.internal_stage;
    if (['prepare', 'cluster', 'paths', 'model', 'solve', 'solution', 'complete'].includes(internal)) return internal;
    if (event.stage === 'quick_evaluation') return 'quick_evaluation';
    if (event.event === 'worker_started') return 'prepare';
    if (event.event === 'run_succeeded') return 'complete';
    if (event.event === 'run_failed' || event.event === 'run_cancelled') continue;
    if (event.stage === 'data_preparation') return 'prepare';
    if (event.stage === 'candidate_generation') return 'cluster';
    if (event.stage === 'exact_optimization') return 'solve';
    if (event.stage === 'persistence') return 'solution';
  }
  return null;
}

function latestAlgorithmProgress(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const value = event.payload?.algorithm_progress;
    if (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1) return value;
  }
  return 0;
}

function latestActivitySemantics(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const semantics = events[index].payload?.activity_semantics;
    if (typeof semantics === 'string' && semantics) return semantics;
  }
  return null;
}

function terminalFailureStageIndex(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.event !== 'run_failed') continue;
    return STAGE_GROUPS.findIndex((group) => group.key === event.stage);
  }
  return -1;
}

function stageProgress(run, events) {
  const stages = STAGE_GROUPS.map(() => 'pending');
  const internalStage = latestInternalStage(events);
  const clusterDisabled = run?.run_config?.cluster_enabled === false;
  let phase = !run
    ? { position: '尚未开始', label: '尚未开始' }
    : { position: '等待 Worker', label: '尚未进入运行阶段' };

  if (run && clusterDisabled) {
    stages[1] = 'skipped';
    stages[2] = 'skipped';
  }

  if (run?.status === 'queued') {
    phase = { position: '等待 Worker', label: '尚未进入运行阶段' };
  } else if (internalStage === 'prepare') {
    stages[0] = 'active';
    phase = { position: '当前阶段 1 / 5', label: '数据准备' };
  } else if (internalStage === 'cluster') {
    stages[0] = 'done';
    stages[1] = 'active';
    stages[2] = 'active';
    phase = { position: '当前阶段 2–3 / 5', label: '候选搜索 / 快速评估' };
  } else if (internalStage === 'quick_evaluation') {
    stages[0] = 'done';
    stages[1] = 'done';
    stages[2] = 'active';
    phase = { position: '当前阶段 3 / 5', label: '快速评估' };
  } else if (internalStage === 'paths') {
    stages[0] = 'done';
    stages[1] = clusterDisabled ? 'skipped' : 'done';
    stages[2] = clusterDisabled ? 'skipped' : 'done';
    phase = { position: '阶段衔接 / 5', label: '构建可行航次路径' };
  } else if (internalStage === 'model' || internalStage === 'solve') {
    stages[0] = 'done';
    stages[1] = clusterDisabled ? 'skipped' : 'done';
    stages[2] = clusterDisabled ? 'skipped' : 'done';
    stages[3] = 'active';
    phase = { position: '当前阶段 4 / 5', label: '精确求解' };
  } else if (internalStage === 'solution') {
    stages.fill('done', 0, 4);
    if (clusterDisabled) { stages[1] = 'skipped'; stages[2] = 'skipped'; }
    stages[4] = 'active';
    phase = { position: '当前阶段 5 / 5', label: '结果持久化' };
  } else if (internalStage === 'complete') {
    stages.fill('done');
    if (clusterDisabled) { stages[1] = 'skipped'; stages[2] = 'skipped'; }
    phase = { position: '阶段 5 / 5', label: '结果持久化' };
  }

  if (run?.status === 'succeeded') {
    stages.fill('done');
    if (clusterDisabled) { stages[1] = 'skipped'; stages[2] = 'skipped'; }
    phase = { position: '运行完成', label: '结果已持久化' };
  } else if (run?.status === 'failed' || run?.status === 'cancelled') {
    const halted = run.status === 'failed' ? 'failed' : 'cancelled';
    const activeIndex = stages.findIndex((status) => status === 'active');
    const failedIndex = run.status === 'failed' ? terminalFailureStageIndex(events) : -1;
    if (activeIndex >= 0) stages[activeIndex] = halted;
    if (internalStage === 'cluster' && activeIndex === 1) stages[2] = halted;
    if (run.status === 'failed' && activeIndex < 0 && failedIndex >= 0) stages[failedIndex] = 'failed';
    let terminalLabel = '未进入算法阶段';
    if (internalStage) terminalLabel = phase.label;
    else if (failedIndex >= 0) terminalLabel = STAGE_GROUPS[failedIndex].label;
    phase = {
      position: run.status === 'failed' ? '运行失败' : '已取消',
      label: terminalLabel,
    };
  }
  return { stages, internalStage, phase, activitySemantics: latestActivitySemantics(events) };
}

function renderStages(run, events) {
  refs.stageFlow.replaceChildren();
  const info = stageProgress(run, events);
  const interleaving = info.internalStage === 'cluster'
    && info.activitySemantics === 'candidate_generation_and_quick_evaluation_interleaved';
  refs.stageFlow.classList.toggle('interleaving', interleaving);
  const statusLabels = {
    pending: '未开始', active: '进行中', done: '已完成', skipped: '跳过 / 不适用',
    failed: '失败', cancelled: '已取消',
  };
  STAGE_GROUPS.forEach((group, index) => {
    const stageStatus = info.stages[index];
    const item = document.createElement('div');
    item.className = `stage ${stageStatus}${interleaving && (index === 1 || index === 2) ? ' interleaved' : ''}`;
    const circle = document.createElement('div');
    circle.className = 'stage-circle';
    circle.textContent = stageStatus === 'done' ? '✓' : stageStatus === 'skipped' ? '⊘' : stageStatus === 'failed' ? '×' : stageStatus === 'cancelled' ? '×' : String(index + 1);
    const strong = document.createElement('strong');
    strong.textContent = `${index + 1}. ${group.label}`;
    const status = document.createElement('span');
    status.textContent = statusLabels[stageStatus];
    item.append(circle, strong, status);
    refs.stageFlow.append(item);
  });
  const rawProgress = run?.status === 'succeeded' ? 1 : run?.status === 'queued' ? 0 : latestAlgorithmProgress(events);
  refs.progressPercent.textContent = formatPercent(rawProgress, { digits: 0 });
  refs.progressStage.textContent = info.phase.position;
  refs.progressLabel.textContent = info.phase.label;
}

function currentActivity(run, events) {
  if (!run) return '尚未开始运行';
  if (run.status === 'queued') return state.workerStatus.connected ? '运行任务已进入队列，等待 Worker 接收。' : 'Worker 未连接/未运行，任务仍在队列中。';
  if (run.status === 'succeeded') return '运行完成，结果已持久化';
  if (run.status === 'failed') return '运行失败';
  if (run.status === 'cancelled') return '运行已取消';
  const latest = events[events.length - 1];
  if (!latest) return '等待结构化运行事件…';
  const internal = latest.payload?.internal_stage;
  return ACTIVITY_LABELS[latest.event] || ACTIVITY_LABELS[internal] || latest.message || '等待结构化运行事件…';
}

function renderEvents() {
  const run = displayRun();
  const events = displayEvents();
  renderStages(run, events);
  const text = document.createElement('span');
  text.textContent = currentActivity(run, events);
  refs.activityBar.replaceChildren(text, refs.viewLogButton);
  refs.logBox.replaceChildren();
  if (!events.length) {
    refs.logBox.textContent = '暂无运行事件。';
    return;
  }
  for (const event of events) {
    const line = document.createElement('div');
    line.className = `log-line level-${String(event.level || '').toLowerCase()}`;
    const time = event.time ? `${event.time} ` : '';
    line.textContent = `${time}${event.level} [${event.stage}/${event.event}] ${event.message}`;
    refs.logBox.append(line);
  }
  if (state.logAutoScroll) refs.logBox.scrollTop = refs.logBox.scrollHeight;
}

function logText() {
  return displayEvents().map((event) => {
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
  const run = displayRun();
  if (!text || !run?.run_id) return;
  saveBlob({ blob: new Blob([text], { type: 'text/plain;charset=utf-8' }), filename: `${run.run_id}.log.txt` });
}

function jumpToLatestLog() {
  refs.logBox.scrollTop = refs.logBox.scrollHeight;
}

async function retryFailedRun(run) {
  if (!can('runs.execute')) { showMessage('当前账号没有运行执行权限', 'warning'); return; }
  if (!window.confirm(`确认按 ${shortRunId(run.run_id)} 的冻结输入重新创建一次运行？`)) return;
  try {
    const retried = await apiFetch(`/api/runs/${encodeURIComponent(run.run_id)}/retry`, { method: 'POST', body: {} });
    state.lastSubmittedRunId = retried.run_id;
    if (!state.activeRun || state.activeRun.status !== 'running') setActiveRun(retried);
    showMessage(`已按原冻结输入创建 ${shortRunId(retried.run_id)}`, 'success');
    await refreshRuns();
  } catch (error) { handleError(error); }
}

function runMetaRow(label, value, cls = '', id = '') {
  const row = document.createElement('div');
  row.className = 'meta-row';
  const key = document.createElement('span'); key.textContent = label;
  const val = document.createElement('strong'); val.textContent = value === null || value === undefined || value === '' ? '—' : String(value); if (cls) val.className = cls; if (id) val.id = id;
  row.append(key, val); return row;
}

function timestamp(value) {
  if (!value) return null;
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value.replace(' ', 'T')}Z`;
  const result = new Date(normalized);
  return Number.isNaN(result.getTime()) ? null : result;
}

function elapsedText(run) {
  if (!run?.started_at) return run?.status === 'queued' ? '等待 Worker' : '—';
  const start = timestamp(run.started_at);
  const end = run.finished_at ? timestamp(run.finished_at) : new Date();
  if (!start || !end) return '—';
  const seconds = Math.max(0, Math.floor((end.getTime() - start.getTime()) / 1000));
  const hours = String(Math.floor(seconds / 3600)).padStart(2, '0');
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
  const remain = String(seconds % 60).padStart(2, '0');
  return `${hours}:${minutes}:${remain}`;
}

function renderElapsed() {
  const value = $('elapsedValue');
  if (value) value.textContent = elapsedText(displayRun());
}

function actionButton(label, handler, className = 'btn') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = className;
  button.textContent = label;
  button.addEventListener('click', handler);
  return button;
}

function renderCurrentActions(run) {
  refs.currentActions.replaceChildren();
  if (!run) return;
  const inspecting = Boolean(state.inspectRunId);
  if (run.status === 'running' && !inspecting) {
    const button = actionButton(run.cancel_requested ? '取消请求已提交' : '取消运行', cancelCurrent, 'btn danger');
    button.disabled = run.cancel_requested || !can('runs.execute');
    refs.currentActions.append(button);
  } else if (run.status === 'succeeded') {
    refs.currentActions.append(actionButton('查看结果', () => {
      window.location.assign(`/runs/${encodeURIComponent(run.run_id)}`);
    }, 'btn primary'));
  } else if (run.status === 'failed') {
    refs.currentActions.append(actionButton('查看错误', () => switchView('log')));
    if (can('runs.execute')) refs.currentActions.append(actionButton('重试', () => retryFailedRun(run), 'btn primary'));
  } else if (run.status === 'cancelled') {
    const note = document.createElement('span');
    note.className = 'terminal-note status-cancelled';
    note.textContent = '运行已取消';
    refs.currentActions.append(note);
  }
}

function renderCurrentRun() {
  const run = displayRun();
  const inspecting = Boolean(state.inspectRunId);
  refs.currentTitle.textContent = inspecting ? '历史运行检查' : '当前运行';
  refs.returnLiveButton.classList.toggle('hidden', !inspecting);
  refs.runMeta.replaceChildren(...(run ? [
    runMetaRow('Run ID', shortRunId(run.run_id)),
    runMetaRow('情境', run.situation?.name || run.situation_id),
    runMetaRow('损毁场景', damageLabel(run)),
    runMetaRow('开始时间', run.started_at || '—'),
    runMetaRow('已运行时间', elapsedText(run), '', 'elapsedValue'),
    runMetaRow('当前状态', STATUS_LABELS[run.status] || run.status, `status-${run.status}`),
  ] : [
    runMetaRow('Run ID', '—'),
    runMetaRow('情境', '—'),
    runMetaRow('损毁场景', '—'),
    runMetaRow('开始时间', '—'),
    runMetaRow('已运行时间', '—', '', 'elapsedValue'),
    runMetaRow('当前状态', '尚未运行', 'status-idle'),
  ]));
  renderCurrentActions(run);
  renderEvents();
  switchView(run ? state.view : 'overview');
}

function td(text, className = '') {
  const el = document.createElement('td');
  el.textContent = text ?? '—';
  if (className) el.className = className;
  return el;
}

function damageLabel(run) {
  const id = run.run_config?.damage_scenario_id;
  return id ? (run.damage_scenario?.name || id) : '无损毁';
}

function shortRunId(id) {
  const match = /^RUN-([a-f0-9]{8})/i.exec(String(id || ''));
  return match ? `R-${match[1].toUpperCase()}` : String(id || '—');
}

function clusterLabel(run) {
  return run.run_config?.cluster_enabled ? String(run.run_config?.cluster_size ?? '—') : '关闭';
}

function renderQueue() {
  const rows = state.runs.filter((r) => r.status === 'queued');
  refs.queueCount.textContent = String(rows.length);
  refs.queueBody.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement('tr'); const cell = td('暂无排队任务', 'table-empty'); cell.colSpan = 8; tr.append(cell); refs.queueBody.append(tr); return;
  }
  for (const run of rows) {
    const tr = document.createElement('tr');
    const actions = document.createElement('td');
    actions.className = 'table-actions';
    const cancelButton = actionButton('取消排队', () => cancelQueuedRun(run), 'link-button');
    cancelButton.disabled = !can('runs.execute');
    if (cancelButton.disabled) cancelButton.title = '当前账号只有查看权限';
    actions.append(cancelButton);
    tr.append(
      td(shortRunId(run.run_id)), td(run.situation?.name || run.situation_id), td(damageLabel(run)),
      td(PREFERENCE_LABELS[run.run_config?.preference_mode] || run.run_config?.preference_mode),
      td(clusterLabel(run)), td(run.created_at), td(STATUS_LABELS[run.status], `status-${run.status}`), actions,
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
      td(shortRunId(run.run_id)), td(run.situation?.name || run.situation_id), td(damageLabel(run)),
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
    state.inspectRunId = run.run_id;
    state.inspectRun = run;
    state.inspectEvents = payload.events || [];
    renderCurrentRun();
    switchView('log');
  } catch (error) { handleError(error); }
}

function returnToLiveRun() {
  state.inspectRunId = null;
  state.inspectRun = null;
  state.inspectEvents = [];
  switchView('overview');
  renderCurrentRun();
}

async function refreshRuns() {
  try {
    const [payload] = await Promise.all([
      apiFetch('/api/runs?limit=100'),
      refreshWorkerStatus(),
    ]);
    state.runs = payload.items || [];
    const previous = state.activeRun;
    const running = state.runs.find((run) => run.status === 'running') || null;
    let selected = null;
    if (running && running.run_id !== state.activeRunId) {
      selected = running;
    } else if (state.activeRunId) {
      selected = state.runs.find((run) => run.run_id === state.activeRunId) || state.activeRun;
    } else if (running) {
      selected = running;
    } else if (state.lastSubmittedRunId) {
      selected = state.runs.find((run) => run.run_id === state.lastSubmittedRunId) || null;
    }
    if (selected) {
      if (selected.run_id !== state.activeRunId) setActiveRun(selected);
      else state.activeRun = selected;
    }
    if (state.inspectRunId) {
      state.inspectRun = state.runs.find((run) => run.run_id === state.inspectRunId) || state.inspectRun;
    }

    const transitionedToTerminal = Boolean(
      previous
      && state.activeRun
      && previous.run_id === state.activeRun.run_id
      && previous.status === 'running'
      && ['succeeded', 'failed', 'cancelled'].includes(state.activeRun.status)
    );
    if (transitionedToTerminal) {
      await refreshActiveEvents({ force: true });
    } else if (state.activeRun?.status === 'running') {
      await refreshActiveEvents();
    }

    renderQueue();
    renderHistory();
    renderCurrentRun();
  } catch (error) { handleError(error); }
}

async function refreshWorkerStatus() {
  try {
    state.workerStatus = await apiFetch('/api/runs/worker-status');
  } catch (error) {
    state.workerStatus = { connected: false, reason: 'status_unavailable' };
    console.warn('Worker status unavailable', error);
  }
}

async function refreshActiveEvents({ force = false } = {}) {
  const run = state.activeRun;
  if (!run?.run_id || (!force && run.status !== 'running')) return;
  if (state.eventRequest) await state.eventRequest;
  const runId = run.run_id;
  const request = (async () => {
    let keepReading = true;
    while (keepReading && state.activeRunId === runId) {
      const payload = await apiFetch(`/api/runs/${encodeURIComponent(runId)}/events?after_seq=${state.activeAfterSeq}&limit=200`);
      if (state.activeRunId !== runId) break;
      const incoming = payload.events || [];
      if (incoming.length) {
        const seen = new Set(state.activeEvents.map((event) => event.seq));
        state.activeEvents.push(...incoming.filter((event) => !seen.has(event.seq)));
        state.activeAfterSeq = payload.next_after_seq || state.activeAfterSeq;
      }
      keepReading = incoming.length === 200;
    }
  })();
  state.eventRequest = request;
  try {
    await request;
  } catch (error) {
    handleError(error, { quietAuth: true });
  } finally {
    if (state.eventRequest === request) state.eventRequest = null;
  }
  if (!state.inspectRunId) renderCurrentRun();
}

async function refreshEvents() {
  await refreshActiveEvents();
}

async function cancelQueuedRun(run) {
  if (!can('runs.execute')) return;
  if (!window.confirm(`确认取消排队任务 ${shortRunId(run.run_id)}？`)) return;
  try {
    await apiFetch(`/api/runs/${encodeURIComponent(run.run_id)}/cancel`, { method: 'POST', body: {} });
    showMessage('排队任务已取消', 'warning');
    await refreshRuns();
  } catch (error) { handleError(error); }
}

async function cancelCurrent() {
  const run = state.activeRun;
  if (!run?.run_id || state.inspectRunId) return;
  if (!window.confirm('确认取消当前运行？取消请求可能需要等待当前求解步骤结束后生效。')) return;
  try {
    await apiFetch(`/api/runs/${encodeURIComponent(run.run_id)}/cancel`, { method: 'POST', body: {} });
    showMessage('取消请求已提交', 'warning');
    await refreshRuns();
  } catch (error) { handleError(error); }
}

function switchView(view) {
  state.view = view;
  const log = view === 'log';
  refs.overviewPane.classList.toggle('hidden', log);
  refs.logPane.classList.toggle('hidden', !log);
  refs.returnOverviewButton.classList.toggle('hidden', !log);
  if (log && state.logAutoScroll) jumpToLatestLog();
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
  refs.preferenceGroup.addEventListener('change', () => {
    refs.customAlpha.classList.toggle('hidden', preferenceMode() !== 'custom');
    invalidateValidation();
  });
  refs.clusterEnabled.addEventListener('change', onClusterEnabledChanged);
  [refs.clusterSize, refs.mipTimeLimit, refs.alphaSortie, refs.alphaResource, refs.alphaTime, refs.damage].forEach((el) => {
    el.addEventListener('input', () => { invalidateValidation(); updateClusterSummary(); updateAdvancedSummary(); });
    el.addEventListener('change', () => { invalidateValidation(); updateClusterSummary(); updateAdvancedSummary(); });
  });
  refs.validateButton.addEventListener('click', validateRun);
  refs.submitButton.addEventListener('click', submitRun);
  refs.viewLogButton.addEventListener('click', () => switchView('log'));
  refs.returnOverviewButton.addEventListener('click', () => switchView('overview'));
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
  renderQueue();
  renderHistory();
  renderCurrentRun();
});

async function init() {
  if (!refs.mipTimeLimit.value.trim()) refs.mipTimeLimit.value = '120';
  bindFormEvents();
  updateClusterControls();
  updateAdvancedSummary();
  renderCurrentRun();
  applyRunPermissionState();
  try {
    await Promise.all([loadSituations(), refreshRuns()]);
  } catch (error) { handleError(error); }
  state.listTimer = window.setInterval(refreshRuns, 5000);
  state.eventTimer = window.setInterval(refreshEvents, 1500);
  state.elapsedTimer = window.setInterval(renderElapsed, 1000);
}

window.addEventListener('beforeunload', () => {
  if (state.listTimer) window.clearInterval(state.listTimer);
  if (state.eventTimer) window.clearInterval(state.eventTimer);
  if (state.elapsedTimer) window.clearInterval(state.elapsedTimer);
});

document.addEventListener('DOMContentLoaded', init);
