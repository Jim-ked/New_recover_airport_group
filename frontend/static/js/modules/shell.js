import { apiFetch, ApiError } from './api-client.js';
import { clearAccount, getAccount } from './account-context.js';
import { requestCurrentWorkspaceLeave } from './workspace-navigation.js';

const authenticated = document.body.dataset.authenticated === 'true';
const accountTrigger = document.getElementById('accountTrigger');
const accountPopover = document.getElementById('accountPopover');
const displayName = document.getElementById('accountDisplayName');
const roleLabel = document.getElementById('accountRole');
const summaryName = document.getElementById('accountSummaryName');
const summaryRole = document.getElementById('accountSummaryRole');
const logoutAction = document.getElementById('logoutAction');
const logoutMessage = document.getElementById('logoutMessage');
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
let logoutInProgress = false;
let passwordChangeInProgress = false;

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
function setLogoutMessage(text) {
  if (!logoutMessage) return;
  logoutMessage.textContent = text;
  logoutMessage.className = 'inline-message error';
  logoutMessage.classList.remove('hidden');
}
function clearLogoutMessage() {
  if (!logoutMessage) return;
  logoutMessage.textContent = '';
  logoutMessage.className = 'inline-message hidden';
}
async function resolveSessionState() {
  try {
    await apiFetch('/api/me', { notifyAuthRequired: false });
    return 'authenticated';
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return 'logged-out';
    return 'unknown';
  }
}
function openPasswordModal() {
  closeAccount();
  clearPasswordMessage();
  currentPassword.value = ''; newPassword.value = ''; newPasswordAgain.value = '';
  passwordModal.classList.add('open'); passwordModal.setAttribute('aria-hidden', 'false');
  focusPasswordModal();
}
function focusPasswordModal() {
  const focusCurrentPassword = () => {
    passwordModal.removeEventListener('transitionend', focusCurrentPassword);
    if (passwordModal.classList.contains('open')) currentPassword.focus();
  };
  passwordModal.addEventListener('transitionend', focusCurrentPassword);
  requestAnimationFrame(() => {
    if (getComputedStyle(passwordModal).visibility === 'visible') focusCurrentPassword();
  });
}
function restorePasswordModal() {
  passwordModal.classList.add('open'); passwordModal.setAttribute('aria-hidden', 'false');
  focusPasswordModal();
}
function closePasswordModal() {
  passwordModal.classList.remove('open'); passwordModal.setAttribute('aria-hidden', 'true');
}
function roleText(role) {
  return ({ viewer: '游客', operator: '操作员', admin: '管理员', user: '操作员' })[role] || '用户';
}
async function loadAccount() {
  if (!authenticated) { redirectToLogin(); return; }
  try {
    account = await getAccount();
    roleLabel.textContent = roleText(account.role);
    summaryRole.textContent = roleText(account.role);
    const accountName = account.display_name || account.login_name || displayName?.textContent?.trim() || account.user_id;
    if (accountName) {
      displayName.textContent = accountName;
      summaryName.textContent = accountName;
    }
    document.documentElement.dataset.role = account.role || 'viewer';
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
  if (passwordChangeInProgress || logoutInProgress) return;
  passwordChangeInProgress = true;
  passwordSave.disabled = true;
  const submittedCurrentPassword = currentPassword.value;
  const submittedNewPassword = newPassword.value;
  let leavePermit = null;
  closePasswordModal();
  try {
    leavePermit = await requestCurrentWorkspaceLeave();
    if (!leavePermit) {
      restorePasswordModal();
      return;
    }
    await apiFetch('/api/auth/change-password', {
      method: 'POST',
      body: { current_password: submittedCurrentPassword, new_password: submittedNewPassword },
      notifyAuthRequired: false,
    });
    clearAccount();
    redirectToLogin();
  } catch (error) {
    if (error instanceof ApiError && error.code === 'NETWORK_ERROR') {
      const state = await resolveSessionState();
      if (state === 'logged-out') {
        clearAccount();
        redirectToLogin();
        return;
      }
      leavePermit?.revoke();
      restorePasswordModal();
      setPasswordMessage(state === 'authenticated'
        ? '修改密码请求未完成：网络连接异常，已确认当前会话仍有效，请重试。'
        : '修改密码请求状态无法确认。请刷新页面核实后重新登录。');
    } else if (error instanceof ApiError && error.status === 401) {
      clearAccount();
      redirectToLogin();
    } else {
      leavePermit?.revoke();
      restorePasswordModal();
      setPasswordMessage(error instanceof ApiError ? error.message : '修改密码失败。');
    }
  } finally {
    passwordChangeInProgress = false;
    passwordSave.disabled = false;
  }
});
logoutAction?.addEventListener('click', async () => {
  if (logoutInProgress || passwordChangeInProgress) return;
  logoutInProgress = true;
  logoutAction.disabled = true;
  clearLogoutMessage();
  let leavePermit = null;
  try {
    leavePermit = await requestCurrentWorkspaceLeave();
    if (!leavePermit) return;
    await apiFetch('/api/auth/logout', {
      method: 'POST', body: {}, notifyAuthRequired: false,
    });
    clearAccount();
    closeAccount();
    redirectToLogin();
  } catch (error) {
    if (error instanceof ApiError && error.code === 'NETWORK_ERROR') {
      const state = await resolveSessionState();
      if (state === 'logged-out') {
        clearAccount();
        closeAccount();
        redirectToLogin();
        return;
      }
      leavePermit?.revoke();
      setLogoutMessage(state === 'authenticated'
        ? '退出未完成：网络连接异常，已确认当前会话仍有效，请重试。'
        : '退出请求未完成，服务端会话状态无法确认。请刷新页面核实后重试。');
    } else {
      leavePermit?.revoke();
      setLogoutMessage(error instanceof ApiError
        ? `退出未完成：${error.message}` : '退出未完成，请稍后重试。');
    }
    openAccount();
  } finally {
    logoutInProgress = false;
    logoutAction.disabled = false;
  }
});
globalThis.addEventListener('app:auth-required', redirectToLogin);
loadAccount();
