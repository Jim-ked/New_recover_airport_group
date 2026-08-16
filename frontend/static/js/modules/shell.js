import { apiFetch, ApiError } from './api-client.js';

const authenticated = document.body.dataset.authenticated === 'true';
const accountTrigger = document.getElementById('accountTrigger');
const accountPopover = document.getElementById('accountPopover');
const displayName = document.getElementById('accountDisplayName');
const roleLabel = document.getElementById('accountRole');
const summaryName = document.getElementById('accountSummaryName');
const summaryRole = document.getElementById('accountSummaryRole');
const logoutAction = document.getElementById('logoutAction');
const changePasswordAction = document.getElementById('changePasswordAction');
const passwordModal = document.getElementById('passwordModal');
const passwordCancel = document.getElementById('passwordCancel');
const passwordSave = document.getElementById('passwordSave');
const passwordMessage = document.getElementById('passwordMessage');
const currentPassword = document.getElementById('currentPassword');
const newPassword = document.getElementById('newPassword');
const newPasswordAgain = document.getElementById('newPasswordAgain');

let account = null;
let redirecting = false;

function loginUrl() {
  const next = `${location.pathname}${location.search}${location.hash}`;
  return `/login?next=${encodeURIComponent(next)}`;
}
function redirectToLogin() {
  if (redirecting || location.pathname === '/login') return;
  redirecting = true;
  window.location.replace(loginUrl());
}
function closeAccount() {
  accountPopover?.classList.remove('open');
  accountPopover?.setAttribute('aria-hidden', 'true');
  accountTrigger?.setAttribute('aria-expanded', 'false');
}
function openAccount() {
  accountPopover?.classList.add('open');
  accountPopover?.setAttribute('aria-hidden', 'false');
  accountTrigger?.setAttribute('aria-expanded', 'true');
}
function setPasswordMessage(text, type = 'error') {
  passwordMessage.className = `inline-message ${type}`;
  passwordMessage.textContent = text;
}
function clearPasswordMessage() {
  passwordMessage.className = 'inline-message hidden';
  passwordMessage.textContent = '';
}
function openPasswordModal() {
  closeAccount();
  clearPasswordMessage();
  currentPassword.value = ''; newPassword.value = ''; newPasswordAgain.value = '';
  passwordModal.classList.add('open'); passwordModal.setAttribute('aria-hidden', 'false');
  setTimeout(() => currentPassword.focus(), 0);
}
function closePasswordModal() {
  passwordModal.classList.remove('open'); passwordModal.setAttribute('aria-hidden', 'true');
}
function roleText(role) {
  return ({ viewer: '查看用户', operator: '运行操作员', admin: '系统管理员' })[role] || role || '查看用户';
}
async function loadAccount() {
  if (!authenticated) { redirectToLogin(); return; }
  try {
    account = await apiFetch('/api/me');
    roleLabel.textContent = roleText(account.role);
    summaryRole.textContent = `${roleText(account.role)} · ${account.permissions?.length || 0} 项权限`;
    if (displayName?.textContent?.trim()) summaryName.textContent = displayName.textContent.trim();
    document.documentElement.dataset.role = account.role || 'viewer';
    document.documentElement.dataset.permissions = (account.permissions || []).join(' ');
    globalThis.dispatchEvent(new CustomEvent('app:account-ready', { detail: account }));
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) console.error(error);
  }
}

accountTrigger?.addEventListener('click', (event) => {
  event.stopPropagation();
  if (accountPopover.classList.contains('open')) closeAccount(); else openAccount();
});
document.addEventListener('click', (event) => {
  if (!accountPopover?.contains(event.target) && !accountTrigger?.contains(event.target)) closeAccount();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') { closeAccount(); closePasswordModal(); }
});
changePasswordAction?.addEventListener('click', openPasswordModal);
passwordCancel?.addEventListener('click', closePasswordModal);
passwordModal?.addEventListener('click', (event) => { if (event.target === passwordModal) closePasswordModal(); });
passwordSave?.addEventListener('click', async () => {
  clearPasswordMessage();
  if (!currentPassword.value || !newPassword.value) { setPasswordMessage('请输入当前密码和新密码。'); return; }
  if (newPassword.value !== newPasswordAgain.value) { setPasswordMessage('两次输入的新密码不一致。'); return; }
  passwordSave.disabled = true;
  try {
    await apiFetch('/api/auth/change-password', { method: 'POST', body: { current_password: currentPassword.value, new_password: newPassword.value } });
    setPasswordMessage('密码已修改，需要重新登录。', 'success');
    setTimeout(redirectToLogin, 350);
  } catch (error) {
    setPasswordMessage(error instanceof ApiError ? error.message : '修改密码失败。');
  } finally { passwordSave.disabled = false; }
});
logoutAction?.addEventListener('click', async () => {
  closeAccount();
  try { await apiFetch('/api/auth/logout', { method: 'POST', body: {} }); } catch (_) { /* logout is idempotent */ }
  redirectToLogin();
});
globalThis.addEventListener('app:auth-required', redirectToLogin);
loadAccount();
