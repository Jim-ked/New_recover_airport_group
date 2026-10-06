import { apiFetch, apiDownload, saveBlob } from './api-client.js';

const $ = (id) => document.getElementById(id);
const state = { loaded: false, loading: false };

function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]);
}
function date(value) { return value ? String(value).replace('T', ' ').slice(0, 19) : '—'; }
function showState(id, text = '') { const node = $(id); node.textContent = text; node.classList.toggle('hidden', !text); }
function severityLabel(value) { return ({ critical: '严重', warning: '警告', info: '提示' })[value] || value; }

function renderAlerts(items) {
  $('alertTableBody').innerHTML = items.length ? items.map((item) => `<tr><td>${esc(date(item.created_at))}</td><td><span class="security-chip sev-${esc(item.severity)}">${esc(severityLabel(item.severity))}</span></td><td>${esc(item.kind)}</td><td title="${esc(item.message)}">${esc(item.message)}</td></tr>`).join('') : '<tr><td colspan="4">暂无异常预警记录</td></tr>';
}
function renderBackups(items) {
  $('backupTableBody').innerHTML = items.length ? items.map((item) => `<tr><td>${esc(date(item.created_at))}</td><td>全量</td><td>${item.encrypted ? '已加密' : '未加密'}</td><td title="${esc(item.path)}">${esc(item.path)}</td><td><span class="security-actions"><button class="btn ghost" type="button" data-restore-backup="${esc(item.id)}">恢复</button><button class="btn danger" type="button" data-delete-backup="${esc(item.id)}">删除</button></span></td></tr>`).join('') : '<tr><td colspan="5">暂无备份记录</td></tr>';
}
function renderReports(items) {
  $('reportTableBody').innerHTML = items.length ? items.map((item) => `<tr><td>${esc(date(item.created_at))}</td><td>${esc(item.period_start || '最早')} 至 ${esc(item.period_end || '最新')}</td><td>${esc(String(item.format || '').toUpperCase())}</td><td title="${esc(item.report_path)}">${esc(item.report_path)}</td><td><button class="btn ghost" type="button" data-download-report="${esc(item.id)}">下载</button></td></tr>`).join('') : '<tr><td colspan="5">暂无审计报告</td></tr>';
}
async function loadSecurity({ force = false } = {}) {
  if ((state.loaded && !force) || state.loading) return;
  state.loading = true;
  showState('securityAlertState', '正在读取…'); showState('securityBackupState', '正在读取…'); showState('securityReportState', '正在读取…');
  try {
    const [alerts, backups, reports] = await Promise.all([
      apiFetch('/api/security/alerts'), apiFetch('/api/security/backups'), apiFetch('/api/security/audit-reports'),
    ]);
    renderAlerts(alerts.items || []); renderBackups(backups.items || []); renderReports(reports.items || []);
    showState('securityAlertState'); showState('securityBackupState'); showState('securityReportState'); state.loaded = true;
  } catch (error) {
    const message = error?.message || '安全数据读取失败';
    showState('securityAlertState', message); showState('securityBackupState', message); showState('securityReportState', message);
  } finally { state.loading = false; }
}

document.querySelector('[data-settings-tab="security"]')?.addEventListener('click', () => loadSecurity());
$('securityRefreshAlerts')?.addEventListener('click', () => { state.loaded = false; loadSecurity({ force: true }); });
$('securityCreateBackup')?.addEventListener('click', async () => {
  const button = $('securityCreateBackup'); button.disabled = true; showState('securityBackupState', '正在创建全量备份…');
  try { await apiFetch('/api/security/backups', { method: 'POST', body: { kind: 'full', encrypted: false } }); state.loaded = false; await loadSecurity({ force: true }); }
  catch (error) { showState('securityBackupState', error?.message || '备份创建失败'); }
  finally { button.disabled = false; }
});
$('backupTableBody')?.addEventListener('click', async (event) => {
  const restore = event.target.closest('[data-restore-backup]'); const remove = event.target.closest('[data-delete-backup]');
  if (restore) {
    if (!globalThis.confirm('恢复将用所选备份覆盖当前数据库。确认继续？')) return;
    showState('securityBackupState', '正在恢复数据库…');
    try { await apiFetch(`/api/security/backups/${encodeURIComponent(restore.dataset.restoreBackup)}/restore`, { method: 'POST', body: {} }); globalThis.location.reload(); }
    catch (error) { showState('securityBackupState', error?.message || '恢复失败'); }
  }
  if (remove) {
    if (!globalThis.confirm('确认删除该备份记录及文件？')) return;
    try { await apiFetch(`/api/security/backups/${encodeURIComponent(remove.dataset.deleteBackup)}`, { method: 'DELETE' }); state.loaded = false; await loadSecurity({ force: true }); }
    catch (error) { showState('securityBackupState', error?.message || '删除失败'); }
  }
});
$('securityCreateReport')?.addEventListener('click', async () => {
  const start = $('securityReportStart').value || null; const end = $('securityReportEnd').value || null;
  if (start && end && start > end) { showState('securityReportState', '开始日期不能晚于结束日期'); return; }
  const button = $('securityCreateReport'); button.disabled = true; showState('securityReportState', '正在生成报告…');
  try { await apiFetch('/api/security/audit-reports', { method: 'POST', body: { format: $('securityReportFormat').value, period_start: start, period_end: end } }); state.loaded = false; await loadSecurity({ force: true }); }
  catch (error) { showState('securityReportState', error?.message || '报告生成失败'); }
  finally { button.disabled = false; }
});
$('reportTableBody')?.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-download-report]'); if (!button) return;
  try { const file = await apiDownload(`/api/security/audit-reports/${encodeURIComponent(button.dataset.downloadReport)}/file`, { method: 'GET' }); saveBlob(file); }
  catch (error) { showState('securityReportState', error?.message || '报告下载失败'); }
});
