import { ApiError } from './api-client.js';

const page = document.getElementById('loginPage');
const form = document.getElementById('loginForm');
const message = document.getElementById('loginMessage');
const button = document.getElementById('loginButton');
const loginName = document.getElementById('loginName');
const loginPassword = document.getElementById('loginPassword');

function showMessage(text) {
  message.textContent = text;
  message.classList.remove('hidden');
}
function safeNext() {
  const value = page?.dataset.next || '/run';
  return value.startsWith('/') && !value.startsWith('//') ? value : '/run';
}

form?.addEventListener('submit', async (event) => {
  event.preventDefault();
  message.classList.add('hidden');
  const username = loginName.value.trim();
  const password = loginPassword.value;
  if (!username || !password) { showMessage('请输入用户名和密码。'); return; }
  button.disabled = true;
  button.textContent = '正在登录…';
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST', credentials: 'same-origin',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      const info = payload?.error || {};
      throw new ApiError(info.message || `登录失败（HTTP ${response.status}）`, { status: response.status, code: info.code || 'LOGIN_FAILED', body: payload });
    }
    window.location.replace(safeNext());
  } catch (error) {
    showMessage(error instanceof ApiError ? error.message : '登录失败，请检查后端服务。');
  } finally {
    button.disabled = false;
    button.textContent = '登录';
  }
});
