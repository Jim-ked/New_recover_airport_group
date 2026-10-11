export function shortRunId(value) {
  const id = String(value || '');
  const hash = /^RUN-([a-f0-9]{8})/i.exec(id);
  return hash ? `R-${hash[1].toUpperCase()}` : id;
}

export function runTime(value) {
  if (!value) return '时间未记录';
  const raw = String(value);
  const date = new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(raw) ? raw : raw.replace(' ', 'T') + 'Z');
  if (!Number.isFinite(date.getTime())) return '时间未记录';
  const parts = Object.fromEntries(new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    .formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

export function formatRunIdentity(run = {}, { multi = false } = {}) {
  const saved = run.display_identity;
  if (saved) return { ...saved, title: multi ? saved.title : saved.situation_title || saved.title };
  const config = run.run_config || {};
  const selected = config.damage_scenario_id;
  const scenario = run.damage_scenario || (run.situation?.damage_scenarios || []).find(row => row.damage_scenario_id === selected) || {};
  const damage = scenario.name || selected || '无损毁';
  const situation = run.situation?.name || run.situation_id || '情境未记录';
  let preference = ({ sortie_max: '出动优先', resource_min: '资源优先', time_min: '时间优先', custom: '自定义' })[config.preference_mode] || config.preference_mode || '偏好未记录';
  if (config.preference_mode === 'custom') preference += ` α=${JSON.stringify(config.alpha ?? '未记录')}`;
  const cluster = typeof config.cluster_enabled !== 'boolean' ? '组群未记录' : config.cluster_enabled ? `组群开启(K=${config.cluster_size ?? '未记录'})` : '组群关闭';
  const time = run.started_at ? runTime(run.started_at) : run.created_at ? `创建 ${runTime(run.created_at)}` : '时间未记录';
  const subtitle = [preference, cluster, time].join(' · ');
  return { run_id: run.run_id || '', title: multi ? damage : `${situation} · ${damage}`, subtitle,
    short_id: shortRunId(run.run_id), detail: `${situation} · ${damage}\n${subtitle}\n${run.run_id || ''}\n配置：${JSON.stringify(config)}` };
}

export function runIdentityMarkup(identity, escape) {
  return `<span class="run-label" title="${escape(identity.detail)}"><b>${escape(identity.title)}</b><small>${escape(identity.subtitle)}</small><small class="run-trace">${escape(identity.short_id)}</small></span>`;
}

export function repeatedRunIds(runs) {
  const groups = new Map();
  for (const run of runs) {
    const key = run.display_identity?.configuration_key;
    if (!key) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(run.run_id);
  }
  return new Set([...groups.values()].filter(ids => ids.length > 1).flat());
}
