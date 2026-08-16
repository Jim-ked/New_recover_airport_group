export class ApiError extends Error {
  constructor(message, { status = 0, code = 'REQUEST_FAILED', field = null, body = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.field = field;
    this.body = body;
  }
}

export function readCookie(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const item of document.cookie.split(';')) {
    const part = item.trim();
    if (part.startsWith(prefix)) return decodeURIComponent(part.slice(prefix.length));
  }
  return null;
}

function mutationHeaders(method, hasJsonBody) {
  const headers = { Accept: 'application/json' };
  const upper = method.toUpperCase();
  if (hasJsonBody) headers['Content-Type'] = 'application/json';
  if (!['GET', 'HEAD', 'OPTIONS'].includes(upper)) {
    const token = readCookie('csrftoken');
    if (token) headers['X-CSRF-Token'] = token;
  }
  return headers;
}

function emitAuthRequired(response, payload) {
  if (response.status !== 401) return;
  const code = payload?.error?.code || 'AUTHENTICATION_REQUIRED';
  globalThis.dispatchEvent(new CustomEvent('app:auth-required', { detail: { code, status: 401 } }));
}

export async function apiFetch(path, { method = 'GET', body = undefined, signal = undefined } = {}) {
  const upper = method.toUpperCase();
  let response;
  try {
    response = await fetch(path, {
      method: upper,
      headers: mutationHeaders(upper, body !== undefined),
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    throw new ApiError('无法连接后端服务', { code: 'NETWORK_ERROR', body: error });
  }

  let payload = null;
  try { payload = await response.json(); } catch (_) { payload = null; }
  if (!response.ok) {
    emitAuthRequired(response, payload);
    const info = payload?.error || {};
    throw new ApiError(info.message || `请求失败（HTTP ${response.status}）`, {
      status: response.status,
      code: info.code || 'HTTP_ERROR',
      field: info.field || null,
      body: payload,
    });
  }
  return payload;
}

export async function apiDownload(path, { method = 'POST', body = undefined, signal = undefined } = {}) {
  const upper = method.toUpperCase();
  let response;
  try {
    response = await fetch(path, {
      method: upper,
      headers: mutationHeaders(upper, body !== undefined),
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    throw new ApiError('无法连接后端服务', { code: 'NETWORK_ERROR', body: error });
  }
  if (!response.ok) {
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    emitAuthRequired(response, payload);
    const info = payload?.error || {};
    throw new ApiError(info.message || `下载失败（HTTP ${response.status}）`, {
      status: response.status,
      code: info.code || 'HTTP_ERROR',
      field: info.field || null,
      body: payload,
    });
  }
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  return { blob: await response.blob(), filename: match?.[1] || 'download.bin', contentType: response.headers.get('Content-Type') || 'application/octet-stream' };
}

export function saveBlob({ blob, filename }) {
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.style.display = 'none';
    document.body.append(a);
    a.click();
    a.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

export async function apiText(path, { method = 'POST', text = '', contentType = 'text/plain', signal = undefined } = {}) {
  const upper = method.toUpperCase();
  const headers = { Accept: 'application/json', 'Content-Type': contentType };
  if (!['GET', 'HEAD', 'OPTIONS'].includes(upper)) {
    const token = readCookie('csrftoken');
    if (token) headers['X-CSRF-Token'] = token;
  }
  let response;
  try {
    response = await fetch(path, { method: upper, headers, credentials: 'same-origin', body: text, signal });
  } catch (error) {
    throw new ApiError('无法连接后端服务', { code: 'NETWORK_ERROR', body: error });
  }
  let payload = null;
  try { payload = await response.json(); } catch (_) { payload = null; }
  if (!response.ok) {
    emitAuthRequired(response, payload);
    const info = payload?.error || {};
    throw new ApiError(info.message || `请求失败（HTTP ${response.status}）`, {
      status: response.status, code: info.code || 'HTTP_ERROR', field: info.field || null, body: payload,
    });
  }
  return payload;
}
