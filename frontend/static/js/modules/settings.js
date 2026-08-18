import { apiFetch, ApiError } from './api-client.js';

const $ = (id) => document.getElementById(id);
const ROLE_LABELS = Object.freeze({ viewer: '游客', operator: '操作员', admin: '管理员' });
const ROLE_DESCRIPTIONS = Object.freeze({
  viewer: '可查看基础数据、情境、指标、运行记录和分析结果，不可修改业务数据或执行算法。',
  operator: '在游客权限基础上，可构建和编辑情境、进行指标评分，并校验和执行算法运行。',
  admin: '拥有全部业务操作权限，并可维护基础数据和指标体系、管理用户、导出结果及查看审计日志。',
});
const PERMISSION_GROUPS = Object.freeze([
  ['基础数据', [['catalog.read', '查看'], ['catalog.write', '维护']]],
  ['情境构建', [['situations.read', '查看'], ['situations.write', '编辑']]],
  ['指标管理', [
    ['indicators.read', '查看'], ['indicators.score', '评分'],
    ['indicators.write', '维护'], ['experts.manage', '专家维护'],
  ]],
  ['算法运行', [['runs.read', '查看'], ['runs.execute', '执行']]],
  ['结果分析', [['results.read', '查看'], ['results.export', '导出']]],
  ['用户管理', [['users.admin', '管理']]],
  ['审计日志', [['audit.read', '查看']]],
]);
const RESOURCE_LABELS = Object.freeze({
  airports: '机场', missions: '任务模板', 'aircraft-types': '机型',
  'resource-types': '保障资源', 'base-data': '基础数据导入',
  'aircraft-resource-requirements': '机型资源关系', situations: '情境',
  indicators: '指标', 'indicator-sets': '指标集', experts: '专家',
  'expert-scores': '专家评分', 'indicator-weights': '指标权重',
  runs: '算法运行', results: '结果分析', users: '用户', auth: '认证',
  'audit-events': '审计日志', me: '当前账户',
});
const OUTCOME_LABELS = Object.freeze({ success: '成功', denied: '拒绝', error: '错误' });
const DETAIL_LABELS = Object.freeze({
  results_export: '结果导出', format: '格式', kind: '类型', source_run_ids: '来源运行',
});

const state = {
  activeTab: 'account',
  account: null,
  users: [],
  usersLoaded: false,
  usersStatus: 'idle',
  selectedUserId: null,
  editingRole: null,
  userFilters: { draftSearch: '', appliedSearch: '', role: '', status: '' },
  audit: {
    loaded: false, status: 'idle', items: [], total: 0, limit: 50, offset: 0,
    filters: {
      q: '', actor_user_id: '', outcome: '', resource_type: '',
      created_after: '', created_before: '',
    },
    selectedAuditId: null,
  },
};

let resetTarget = null;
let confirmAction = null;
let messageTimer = null;

function esc(value) {
  return String(value ?? '').replace(
    /[&<>'"]/g,
    (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character],
  );
}
function roleLabel(role) { return ROLE_LABELS[role] || '用户'; }
function roleDescription(role) { return ROLE_DESCRIPTIONS[role] || '当前角色使用系统默认权限范围。'; }
function can(permission) { return Boolean(state.account?.permissions?.includes(permission)); }
function permissionSummary(permissions) {
  const effective = new Set(permissions || []);
  return PERMISSION_GROUPS.map(([label, mappings]) => ({
    label,
    capabilities: mappings
      .filter(([permission]) => effective.has(permission))
      .map(([, capability]) => capability),
  })).filter((group) => group.capabilities.length);
}
function formatDateTime(value, empty = '—') {
  if (!value) return empty;
  const raw = String(value).trim();
  const naive = raw.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/);
  if (naive && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw)) return `${naive[1]} ${naive[2]}`;
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  const pad = (number) => String(number).padStart(2, '0');
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`;
}
function errorText(error, fallback = '操作失败') {
  return error instanceof ApiError ? (error.message || error.code) : (error?.message || fallback);
}
function showMessage(message, type = 'success') {
  const element = $('settingsMessage');
  window.clearTimeout(messageTimer);
  element.textContent = message;
  element.className = `workspace-message ${type}`;
  messageTimer = window.setTimeout(() => element.classList.add('hidden'), 3800);
}
function setInline(id, text = '') {
  const element = $(id);
  element.textContent = text;
  element.classList.toggle('hidden', !text);
}
function openModal(id) {
  const element = $(id);
  element.classList.add('open');
  element.setAttribute('aria-hidden', 'false');
}
function closeModal(id) {
  const element = $(id);
  element.classList.remove('open');
  element.setAttribute('aria-hidden', 'true');
}

function renderAccount() {
  const account = state.account;
  const displayName = account.display_name || account.login_name || '—';
  const facts = [
    ['登录账号', account.login_name || '—'],
    ['显示名称', displayName],
    ['用户编号', account.user_id || '—'],
    ['角色', roleLabel(account.role)],
    ['最近登录', formatDateTime(account.last_login_at, '尚未登录')],
    ['创建时间', formatDateTime(account.created_at)],
  ];
  $('settingsAccountBody').innerHTML = facts.map(([label, value]) =>
    `<div class="account-fact"><dt>${esc(label)}</dt><dd title="${esc(value)}">${esc(value)}</dd></div>`
  ).join('');
  const groups = permissionSummary(account.permissions);
  $('settingsPermissionSummary').innerHTML = groups.length
    ? groups.map((group) =>
      `<div class="permission-row"><strong>${esc(group.label)}</strong><span>${esc(group.capabilities.join('、'))}</span></div>`
    ).join('')
    : '<div class="settings-state">当前账户没有可展示的业务权限。</div>';
}

function renderTabs() {
  document.querySelectorAll('.settings-tab').forEach((button) => {
    const permission = button.dataset.permission;
    const visible = !permission || can(permission);
    button.classList.toggle('hidden', !visible);
    if (!visible && state.activeTab === button.dataset.settingsTab) state.activeTab = 'account';
  });
  document.querySelectorAll('[data-settings-panel]').forEach((panel) => {
    const active = panel.dataset.settingsPanel === state.activeTab;
    panel.classList.toggle('hidden', !active);
    panel.setAttribute('aria-hidden', String(!active));
  });
  document.querySelectorAll('.settings-tab').forEach((button) => {
    const active = button.dataset.settingsTab === state.activeTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  });
}
async function switchTab(tab) {
  const button = document.querySelector(`[data-settings-tab="${CSS.escape(tab)}"]`);
  if (!button || button.classList.contains('hidden')) return;
  state.activeTab = tab;
  renderTabs();
  if (tab === 'users') await loadUsers();
  if (tab === 'audit') {
    await loadAuditActors();
    await loadAudit();
  }
}

// User workspace
function filteredUsers() {
  const query = state.userFilters.appliedSearch.toLocaleLowerCase();
  return state.users.filter((user) => {
    if (query && ![user.login_name, user.display_name, user.user_id]
      .some((value) => String(value || '').toLocaleLowerCase().includes(query))) return false;
    if (state.userFilters.role && user.role !== state.userFilters.role) return false;
    if (state.userFilters.status === 'active' && user.is_disabled) return false;
    if (state.userFilters.status === 'disabled' && !user.is_disabled) return false;
    return true;
  });
}
function setUserListState(message = '', retry = false) {
  const element = $('userListState');
  element.classList.toggle('hidden', !message);
  element.innerHTML = message
    ? `<div class="settings-state-actions"><span>${esc(message)}</span>${retry ? '<button id="retryUsers" class="btn" type="button">重试</button>' : ''}</div>`
    : '';
  $('retryUsers')?.addEventListener('click', () => loadUsers({ force: true }));
}
function renderUsers() {
  const body = $('userTableBody');
  if (state.usersStatus === 'loading') {
    body.replaceChildren();
    setUserListState('正在读取用户…');
    return;
  }
  if (state.usersStatus === 'error') {
    body.replaceChildren();
    setUserListState('用户列表读取失败。', true);
    return;
  }
  const rows = filteredUsers();
  if (!state.users.length) {
    body.replaceChildren();
    setUserListState('尚未创建用户。');
  } else if (!rows.length) {
    body.replaceChildren();
    setUserListState('没有符合当前筛选条件的用户。');
  } else {
    setUserListState();
    body.innerHTML = rows.map((user) => {
      const selected = user.user_id === state.selectedUserId;
      return `<tr data-user-id="${esc(user.user_id)}" class="${selected ? 'selected' : ''}" tabindex="0" role="button" aria-selected="${selected}">
        <td><div class="user-account"><strong>${esc(user.login_name)}</strong><small>${user.user_id === state.account.user_id ? '当前账户' : esc(user.user_id)}</small></div></td>
        <td>${esc(user.display_name || user.login_name)}</td>
        <td>${esc(roleLabel(user.role))}</td>
        <td><span class="status-chip ${user.is_disabled ? 'off' : 'ok'}">${user.is_disabled ? '已停用' : '正常'}</span></td>
        <td>${esc(formatDateTime(user.last_login_at, '尚未登录'))}</td>
      </tr>`;
    }).join('');
  }
  $('userCount').textContent = `${rows.length} / ${state.users.length} 个账户`;
  renderUserInspector();
}
function selectedUser() {
  return state.users.find((user) => user.user_id === state.selectedUserId) || null;
}
function selectUser(userId) {
  state.selectedUserId = userId;
  state.editingRole = selectedUser()?.role || null;
  renderUsers();
}
function closeUserInspector() {
  state.selectedUserId = null;
  state.editingRole = null;
  $('userInspector').classList.add('hidden');
  $('userInspector').closest('.settings-master-detail').classList.remove('inspector-open');
  renderUsers();
}
function renderUserInspector() {
  const inspector = $('userInspector');
  const user = selectedUser();
  const master = inspector.closest('.settings-master-detail');
  if (!user) {
    inspector.classList.add('hidden');
    master.classList.remove('inspector-open');
    return;
  }
  const self = user.user_id === state.account.user_id;
  inspector.classList.remove('hidden');
  master.classList.add('inspector-open');
  $('userInspectorSubtitle').textContent = user.display_name || user.login_name;
  const roleControl = self
    ? `<dl class="inspector-facts"><div><dt>角色</dt><dd>${esc(roleLabel(user.role))}</dd></div></dl>`
    : `<div class="field"><label for="inspectorRole">角色</label><select id="inspectorRole" class="control">
        ${Object.keys(ROLE_LABELS).map((role) => `<option value="${role}" ${state.editingRole === role ? 'selected' : ''}>${esc(roleLabel(role))}</option>`).join('')}
      </select></div>`;
  $('userInspectorBody').innerHTML = `
    <section class="inspector-section"><h3>用户信息</h3><dl class="inspector-facts">
      <div><dt>登录账号</dt><dd>${esc(user.login_name)}</dd></div>
      <div><dt>显示名称</dt><dd>${esc(user.display_name || user.login_name)}</dd></div>
      <div><dt>用户编号</dt><dd>${esc(user.user_id)}</dd></div>
      <div><dt>状态</dt><dd>${user.is_disabled ? '已停用' : '正常'}</dd></div>
      <div><dt>创建时间</dt><dd>${esc(formatDateTime(user.created_at))}</dd></div>
      <div><dt>最近登录</dt><dd>${esc(formatDateTime(user.last_login_at, '尚未登录'))}</dd></div>
    </dl></section>
    <section class="inspector-section"><h3>角色权限说明</h3>${roleControl}
      <p id="inspectorRoleDescription" class="role-description">${esc(roleDescription(state.editingRole || user.role))}</p>
    </section>
    <div class="inspector-actions">
      ${self
        ? '<button id="userSelfPassword" class="btn primary" type="button">修改密码</button>'
        : `<button id="saveUserRole" class="btn primary" type="button" ${state.editingRole === user.role ? 'disabled' : ''}>保存修改</button>
           <div class="inspector-actions-secondary">
             <button id="resetSelectedUser" class="btn" type="button">重置密码</button>
             <button id="toggleSelectedUser" class="btn ${user.is_disabled ? '' : 'danger'}" type="button">${user.is_disabled ? '启用账户' : '停用账户'}</button>
           </div>`}
    </div>`;
  $('inspectorRole')?.addEventListener('change', (event) => {
    state.editingRole = event.target.value;
    $('inspectorRoleDescription').textContent = roleDescription(state.editingRole);
    $('saveUserRole').disabled = state.editingRole === user.role;
  });
  $('saveUserRole')?.addEventListener('click', saveSelectedUserRole);
  $('resetSelectedUser')?.addEventListener('click', () => openResetPassword(user));
  $('toggleSelectedUser')?.addEventListener('click', () => confirmToggleUser(user));
  $('userSelfPassword')?.addEventListener('click', openSelfPassword);
}
async function loadUsers({ force = false } = {}) {
  if (state.usersLoaded && !force) {
    renderUsers();
    return;
  }
  state.usersStatus = 'loading';
  renderUsers();
  try {
    const data = await apiFetch('/api/users');
    state.users = data.users || [];
    state.usersLoaded = true;
    state.usersStatus = 'ready';
    if (state.selectedUserId && !selectedUser()) state.selectedUserId = null;
    populateAuditActors();
  } catch (_) {
    state.usersStatus = 'error';
  }
  renderUsers();
}
function applyUserSearch() {
  state.userFilters.draftSearch = $('userSearch').value.trim();
  state.userFilters.appliedSearch = state.userFilters.draftSearch;
  $('userSearchClear').classList.toggle('hidden', !state.userFilters.appliedSearch);
  renderUsers();
}
function clearUserSearch() {
  state.userFilters.draftSearch = '';
  state.userFilters.appliedSearch = '';
  $('userSearch').value = '';
  $('userSearchClear').classList.add('hidden');
  renderUsers();
}
async function saveSelectedUserRole() {
  const user = selectedUser();
  if (!user || user.user_id === state.account.user_id || state.editingRole === user.role) return;
  const button = $('saveUserRole');
  button.disabled = true;
  try {
    await apiFetch(`/api/users/${encodeURIComponent(user.user_id)}/role`, {
      method: 'PUT', body: { role: state.editingRole },
    });
    await loadUsers({ force: true });
    state.editingRole = selectedUser()?.role || null;
    renderUsers();
    showMessage('角色已更新，目标用户的旧会话已失效。');
  } catch (error) {
    showMessage(errorText(error, '角色更新失败。'), 'error');
    button.disabled = false;
  }
}
function askConfirm(title, body, action, { danger = true } = {}) {
  $('settingsConfirmTitle').textContent = title;
  $('settingsConfirmBody').textContent = body;
  $('settingsConfirmAction').classList.toggle('danger', danger);
  confirmAction = action;
  openModal('settingsConfirmModal');
}
function confirmToggleUser(user) {
  const name = user.display_name || user.login_name;
  askConfirm(
    user.is_disabled ? '确认启用账户' : '确认停用账户',
    `${user.is_disabled ? '启用' : '停用'} ${name}（${user.login_name}）？${user.is_disabled ? '' : ' 停用后其旧会话立即失效。'}`,
    async () => {
      await apiFetch(`/api/users/${encodeURIComponent(user.user_id)}/disabled`, {
        method: 'PUT', body: { disabled: !user.is_disabled },
      });
      await loadUsers({ force: true });
      showMessage(user.is_disabled ? '账户已启用。' : '账户已停用，旧会话已失效。');
    },
    { danger: !user.is_disabled },
  );
}
function openResetPassword(user) {
  resetTarget = user;
  $('resetPasswordTarget').textContent = `目标账户：${user.display_name || user.login_name}（${user.login_name}）`;
  $('resetPasswordValue').value = '';
  $('resetPasswordAgain').value = '';
  setInline('resetPasswordMessage');
  openModal('resetPasswordModal');
  window.setTimeout(() => $('resetPasswordValue').focus(), 0);
}
function openSelfPassword() { $('changePasswordAction')?.click(); }
function openCreateUser() {
  setInline('createUserMessage');
  ['createLoginName', 'createDisplayName', 'createPassword', 'createPasswordAgain']
    .forEach((id) => { $(id).value = ''; });
  $('createRole').value = 'operator';
  $('createRoleDescription').textContent = roleDescription('operator');
  openModal('createUserModal');
  window.setTimeout(() => $('createLoginName').focus(), 0);
}

// Audit workspace
function resourceLabel(resourceType) { return RESOURCE_LABELS[resourceType] || '系统资源'; }
function formatAuditAction(event) {
  const method = String(event.request_method || '').toUpperCase();
  const path = String(event.request_path || '');
  const resource = resourceLabel(event.resource_type);
  if (path === '/api/auth/login') return '登录';
  if (path === '/api/auth/logout') return '退出登录';
  if (path === '/api/auth/change-password') return '修改密码';
  if (event.resource_type === 'users') {
    if (path.endsWith('/role')) return '修改用户角色';
    if (path.endsWith('/disabled')) return '修改用户状态';
    if (path.endsWith('/reset-password')) return '重置用户密码';
    if (method === 'POST') return '新建用户';
    return '浏览账户';
  }
  if (event.resource_type === 'runs') {
    if (path === '/api/runs/validate') return '校验运行条件';
    if (path === '/api/runs' && method === 'POST') return '提交算法运行';
    if (path.endsWith('/cancel')) return '取消运行';
    if (path.endsWith('/retry')) return '重试运行';
    if (path.endsWith('/events')) return '查看运行日志';
    if (path.endsWith('/metrics')) return '查看运行指标';
    if (path.endsWith('/solution')) return '查看运行结果';
    if (path.endsWith('/runtime')) return '查看运行态势';
    if (path.endsWith('/situation')) return '查看运行情境';
    return '查看算法运行';
  }
  if (event.resource_type === 'results') {
    if (path.includes('export')) return '导出分析结果';
    if (path.includes('damage-candidates')) return '查看结果比较候选';
    if (path.includes('comparable-runs')) return '查看可比较运行';
    if (path.includes('comparison')) return '查看结果比较';
    return '查看结果分析';
  }
  if (event.resource_type === 'situations') {
    if (path.includes('/working-copy/')) return '处理情境工作副本';
    if (method === 'POST') return '新建情境';
    if (method === 'PUT') return '修改情境';
    if (method === 'DELETE') return '删除情境';
    return '查看情境';
  }
  const nouns = {
    airports: '机场', missions: '任务模板', 'aircraft-types': '机型',
    'resource-types': '保障资源', indicators: '指标', 'indicator-sets': '指标集',
    experts: '专家', 'expert-scores': '专家评分',
  };
  const noun = nouns[event.resource_type];
  if (noun) {
    if (method === 'POST') return `新建${noun}`;
    if (method === 'PUT') return `修改${noun}`;
    if (method === 'DELETE') return `删除${noun}`;
    return `查看${noun}`;
  }
  if (method === 'GET') return `查看${resource}`;
  if (method === 'PUT') return `修改${resource}`;
  if (method === 'DELETE') return `删除${resource}`;
  return `执行${resource}操作`;
}
function actorLabel(event) {
  if (!event.actor_user_id) return '匿名用户';
  const user = state.users.find((item) => item.user_id === event.actor_user_id);
  return user?.display_name || user?.login_name || event.actor_user_id;
}
function auditObject(event) { return event.resource_id || '—'; }
function outcomeClass(outcome) { return OUTCOME_LABELS[outcome] ? outcome : 'unknown'; }
function setAuditListState(message = '', retry = false) {
  const element = $('auditListState');
  element.classList.toggle('hidden', !message);
  element.innerHTML = message
    ? `<div class="settings-state-actions"><span>${esc(message)}</span>${retry ? '<button id="retryAudit" class="btn" type="button">重试</button>' : ''}</div>`
    : '';
  $('retryAudit')?.addEventListener('click', () => loadAudit({ force: true }));
}
function renderAudit() {
  const body = $('auditTableBody');
  if (state.audit.status === 'loading') {
    body.replaceChildren();
    setAuditListState('正在读取审计日志…');
  } else if (state.audit.status === 'forbidden') {
    body.replaceChildren();
    setAuditListState('无权查看审计日志。');
  } else if (state.audit.status === 'error') {
    body.replaceChildren();
    setAuditListState('审计日志读取失败。', true);
  } else if (!state.audit.items.length) {
    body.replaceChildren();
    setAuditListState('没有符合当前条件的审计记录。');
  } else {
    setAuditListState();
    body.innerHTML = state.audit.items.map((event) => {
      const selected = event.audit_id === state.audit.selectedAuditId;
      return `<tr data-audit-id="${event.audit_id}" class="${selected ? 'selected' : ''}" tabindex="0" role="button" aria-selected="${selected}">
        <td>${esc(formatDateTime(event.created_at))}</td>
        <td>${esc(actorLabel(event))}</td>
        <td>${esc(formatAuditAction(event))}</td>
        <td title="${esc(auditObject(event))}">${esc(auditObject(event))}</td>
        <td><span class="outcome-chip ${outcomeClass(event.outcome)}">${esc(OUTCOME_LABELS[event.outcome] || '未知')}</span></td>
      </tr>`;
    }).join('');
  }
  const start = state.audit.total ? state.audit.offset + 1 : 0;
  const end = Math.min(state.audit.offset + state.audit.items.length, state.audit.total);
  $('auditRange').textContent = `${start}–${end} / ${state.audit.total} 条`;
  $('auditPrev').disabled = state.audit.offset <= 0 || state.audit.status === 'loading';
  $('auditNext').disabled = end >= state.audit.total || state.audit.status === 'loading';
  renderAuditInspector();
}
function selectedAudit() {
  return state.audit.items.find((event) => event.audit_id === state.audit.selectedAuditId) || null;
}
function selectAudit(auditId) {
  state.audit.selectedAuditId = auditId;
  renderAudit();
}
function closeAuditInspector() {
  state.audit.selectedAuditId = null;
  $('auditInspector').classList.add('hidden');
  $('auditInspector').closest('.settings-master-detail').classList.remove('inspector-open');
  renderAudit();
}
function formatDetailValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.map(formatDetailValue).join('、');
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([key, nested]) => `${DETAIL_LABELS[key] || key}: ${formatDetailValue(nested)}`)
      .join('；');
  }
  return String(value);
}
function renderAuditInspector() {
  const inspector = $('auditInspector');
  const master = inspector.closest('.settings-master-detail');
  const event = selectedAudit();
  if (!event) {
    inspector.classList.add('hidden');
    master.classList.remove('inspector-open');
    return;
  }
  inspector.classList.remove('hidden');
  master.classList.add('inspector-open');
  $('auditInspectorSubtitle').textContent = formatAuditAction(event);
  const facts = [
    ['时间', formatDateTime(event.created_at)], ['用户', actorLabel(event)],
    ['角色', event.actor_role ? roleLabel(event.actor_role) : '—'],
    ['操作', formatAuditAction(event)], ['对象', auditObject(event)],
    ['结果', OUTCOME_LABELS[event.outcome] || '未知'], ['来源地址', event.source_address || '—'],
  ];
  const technical = [
    ['请求方式', event.request_method || '—'], ['请求路径', event.request_path || '—'],
    ['HTTP 状态', event.response_status ?? '—'], ['资源类型', event.resource_type || '—'],
    ['资源编号', event.resource_id || '—'],
  ];
  const rows = (items) => items.map(([label, value]) =>
    `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`
  ).join('');
  const detailRows = Object.entries(event.details || {})
    .filter(([key]) => key !== 'endpoint')
    .map(([key, value]) =>
      `<div><dt>${esc(DETAIL_LABELS[key] || key)}</dt><dd>${esc(formatDetailValue(value))}</dd></div>`
    ).join('');
  $('auditInspectorBody').innerHTML = `
    <section class="inspector-section"><h3>审计详情</h3><dl class="inspector-facts">${rows(facts)}</dl></section>
    <section class="inspector-section"><h3>技术信息</h3><dl class="inspector-facts">${rows(technical)}</dl></section>
    ${detailRows ? `<section class="inspector-section"><h3>附加信息</h3><dl class="audit-details-list">${detailRows}</dl></section>` : ''}`;
}
function populateAuditActors() {
  const select = $('auditActorFilter');
  const current = select.value || state.audit.filters.actor_user_id;
  const options = [...state.users].sort((left, right) =>
    String(left.display_name || left.login_name)
      .localeCompare(String(right.display_name || right.login_name), 'zh-CN'));
  select.innerHTML = '<option value="">全部用户</option>'
    + options.map((user) =>
      `<option value="${esc(user.user_id)}">${esc(user.display_name || user.login_name)} / ${esc(user.login_name)}</option>`
    ).join('');
  select.value = options.some((user) => user.user_id === current) ? current : '';
}
async function loadAuditActors() {
  if (state.usersLoaded) {
    populateAuditActors();
    return;
  }
  try {
    const data = await apiFetch('/api/users');
    state.users = data.users || [];
    state.usersLoaded = true;
    state.usersStatus = 'ready';
    populateAuditActors();
  } catch (_) {
    $('auditActorFilter').innerHTML = '<option value="">全部用户</option>';
  }
}
function auditQueryString() {
  const params = new URLSearchParams({
    limit: String(state.audit.limit), offset: String(state.audit.offset),
  });
  Object.entries(state.audit.filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return params.toString();
}
async function loadAudit({ force = false } = {}) {
  if (state.audit.loaded && !force) {
    renderAudit();
    return;
  }
  state.audit.status = 'loading';
  renderAudit();
  try {
    const data = await apiFetch(`/api/audit-events?${auditQueryString()}`);
    state.audit.items = data.items || [];
    state.audit.total = Number(data.total || 0);
    state.audit.loaded = true;
    state.audit.status = 'ready';
    if (state.audit.selectedAuditId && !selectedAudit()) state.audit.selectedAuditId = null;
  } catch (error) {
    state.audit.items = [];
    state.audit.total = 0;
    state.audit.status = error instanceof ApiError && error.status === 403 ? 'forbidden' : 'error';
  }
  renderAudit();
}
function toAuditTime(value, endOfMinute = false) {
  return value ? `${value.replace('T', ' ')}:${endOfMinute ? '59' : '00'}` : '';
}
function applyAuditFilters() {
  state.audit.filters = {
    q: $('auditKeyword').value.trim(),
    actor_user_id: $('auditActorFilter').value,
    outcome: $('auditOutcomeFilter').value,
    resource_type: $('auditResourceFilter').value,
    created_after: toAuditTime($('auditCreatedAfter').value),
    created_before: toAuditTime($('auditCreatedBefore').value, true),
  };
  state.audit.offset = 0;
  state.audit.loaded = false;
  state.audit.selectedAuditId = null;
  loadAudit({ force: true });
}
function resetAuditFilters() {
  ['auditKeyword', 'auditActorFilter', 'auditOutcomeFilter', 'auditResourceFilter',
    'auditCreatedAfter', 'auditCreatedBefore'].forEach((id) => { $(id).value = ''; });
  state.audit.filters = {
    q: '', actor_user_id: '', outcome: '', resource_type: '',
    created_after: '', created_before: '',
  };
  state.audit.offset = 0;
  state.audit.loaded = false;
  state.audit.selectedAuditId = null;
  loadAudit({ force: true });
}

// Event bindings and initialization
function bind() {
  $('settingsTabs').addEventListener('click', (event) => {
    const button = event.target.closest('[data-settings-tab]');
    if (button) switchTab(button.dataset.settingsTab);
  });
  $('settingsChangePassword').addEventListener('click', openSelfPassword);
  $('userSearch').value = '';
  $('userSearch').addEventListener('input', (event) => {
    state.userFilters.draftSearch = event.target.value;
    $('userSearchClear').classList.toggle('hidden', !event.target.value);
  });
  $('userSearch').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      applyUserSearch();
    }
  });
  $('userSearchButton').addEventListener('click', applyUserSearch);
  $('userSearchClear').addEventListener('click', clearUserSearch);
  $('userRoleFilter').addEventListener('change', (event) => {
    state.userFilters.role = event.target.value;
    renderUsers();
  });
  $('userStatusFilter').addEventListener('change', (event) => {
    state.userFilters.status = event.target.value;
    renderUsers();
  });
  $('refreshUsersButton').addEventListener('click', () => loadUsers({ force: true }));
  $('createUserButton').addEventListener('click', openCreateUser);
  $('closeUserInspector').addEventListener('click', closeUserInspector);
  $('userTableBody').addEventListener('click', (event) => {
    const row = event.target.closest('tr[data-user-id]');
    if (row) selectUser(row.dataset.userId);
  });
  $('userTableBody').addEventListener('keydown', (event) => {
    const row = event.target.closest('tr[data-user-id]');
    if (row && ['Enter', ' '].includes(event.key)) {
      event.preventDefault();
      selectUser(row.dataset.userId);
    }
  });

  $('createRole').addEventListener('change', (event) => {
    $('createRoleDescription').textContent = roleDescription(event.target.value);
  });
  $('createUserCancel').addEventListener('click', () => closeModal('createUserModal'));
  $('createUserSave').addEventListener('click', async () => {
    setInline('createUserMessage');
    const password = $('createPassword').value;
    if (password !== $('createPasswordAgain').value) {
      setInline('createUserMessage', '两次输入的密码不一致。');
      return;
    }
    const button = $('createUserSave');
    button.disabled = true;
    try {
      const data = await apiFetch('/api/users', {
        method: 'POST',
        body: {
          login_name: $('createLoginName').value.trim(),
          display_name: $('createDisplayName').value.trim() || null,
          role: $('createRole').value,
          password,
        },
      });
      closeModal('createUserModal');
      state.selectedUserId = data.user?.user_id || null;
      await loadUsers({ force: true });
      if (state.selectedUserId) state.editingRole = selectedUser()?.role || null;
      renderUsers();
      showMessage('新用户已创建。');
    } catch (error) {
      setInline('createUserMessage', errorText(error, '创建用户失败。'));
    } finally {
      button.disabled = false;
    }
  });

  $('resetPasswordCancel').addEventListener('click', () => closeModal('resetPasswordModal'));
  $('resetPasswordSave').addEventListener('click', async () => {
    if (!resetTarget) return;
    setInline('resetPasswordMessage');
    const password = $('resetPasswordValue').value;
    if (password !== $('resetPasswordAgain').value) {
      setInline('resetPasswordMessage', '两次输入的密码不一致。');
      return;
    }
    const button = $('resetPasswordSave');
    button.disabled = true;
    try {
      await apiFetch(`/api/users/${encodeURIComponent(resetTarget.user_id)}/reset-password`, {
        method: 'POST', body: { new_password: password },
      });
      closeModal('resetPasswordModal');
      showMessage('密码已重置，目标用户旧会话已失效。');
    } catch (error) {
      setInline('resetPasswordMessage', errorText(error, '密码重置失败。'));
    } finally {
      button.disabled = false;
    }
  });
  $('settingsConfirmCancel').addEventListener('click', () => {
    confirmAction = null;
    closeModal('settingsConfirmModal');
  });
  $('settingsConfirmAction').addEventListener('click', async () => {
    const action = confirmAction;
    confirmAction = null;
    closeModal('settingsConfirmModal');
    if (!action) return;
    try {
      await action();
    } catch (error) {
      showMessage(errorText(error), 'error');
      await loadUsers({ force: true }).catch(() => {});
    }
  });

  $('auditQuery').addEventListener('click', applyAuditFilters);
  $('auditReset').addEventListener('click', resetAuditFilters);
  $('auditRefresh').addEventListener('click', () => loadAudit({ force: true }));
  $('auditKeyword').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      applyAuditFilters();
    }
  });
  $('auditPrev').addEventListener('click', () => {
    if (state.audit.offset <= 0) return;
    state.audit.offset = Math.max(0, state.audit.offset - state.audit.limit);
    state.audit.loaded = false;
    loadAudit({ force: true });
  });
  $('auditNext').addEventListener('click', () => {
    if (state.audit.offset + state.audit.limit >= state.audit.total) return;
    state.audit.offset += state.audit.limit;
    state.audit.loaded = false;
    loadAudit({ force: true });
  });
  $('auditTableBody').addEventListener('click', (event) => {
    const row = event.target.closest('tr[data-audit-id]');
    if (row) selectAudit(Number(row.dataset.auditId));
  });
  $('auditTableBody').addEventListener('keydown', (event) => {
    const row = event.target.closest('tr[data-audit-id]');
    if (row && ['Enter', ' '].includes(event.key)) {
      event.preventDefault();
      selectAudit(Number(row.dataset.auditId));
    }
  });
  $('closeAuditInspector').addEventListener('click', closeAuditInspector);
}

async function init() {
  bind();
  try {
    state.account = await apiFetch('/api/me');
    renderAccount();
    renderTabs();
  } catch (error) {
    $('settingsAccountBody').innerHTML = '<div class="settings-state">账户信息读取失败。</div>';
    $('settingsPermissionSummary').innerHTML = '';
    showMessage(errorText(error, '账户信息读取失败。'), 'error');
  }
}

init();
