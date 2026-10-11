from __future__ import annotations

from typing import Any, Mapping
from datetime import datetime, timezone, timedelta
import hashlib
import json

from backend.domain.run_config import RunConfig


def run_display_identity(payload: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> dict:
    """Presentation metadata derived from frozen configuration and existing Run times."""
    record = record or {}
    situation = payload.get('situation') or {}
    config = payload.get('run_config') or {}
    run_id = str(payload.get('run_id') or record.get('run_id') or '')
    selected = config.get('damage_scenario_id')
    scenario = next((row for row in situation.get('damage_scenarios') or []
                     if row.get('damage_scenario_id') == selected), {})
    damage = str(scenario.get('name') or selected or '无损毁')
    situation_name = str(situation.get('name') or situation.get('situation_id') or record.get('situation_id') or '情境未记录')
    preference = {'sortie_max': '出动优先', 'resource_min': '资源优先', 'time_min': '时间优先', 'custom': '自定义'}.get(
        config.get('preference_mode'), config.get('preference_mode') or '偏好未记录')
    if config.get('preference_mode') == 'custom':
        preference += ' α=' + json.dumps(config.get('alpha', '未记录'), ensure_ascii=False, separators=(',', ':'))
    cluster = '组群未记录'
    if isinstance(config.get('cluster_enabled'), bool):
        cluster = f"组群开启(K={config.get('cluster_size', '未记录')})" if config['cluster_enabled'] else '组群关闭'
    time_value = record.get('started_at') or record.get('created_at')
    time_text = '时间未记录'
    if time_value:
        try:
            date = datetime.fromisoformat(str(time_value).replace('Z', '+00:00'))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)  # SQLite CURRENT_TIMESTAMP is UTC.
            time_text = date.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')
            if not record.get('started_at'):
                time_text = '创建 ' + time_text
        except ValueError:
            time_text = '时间未记录'
    short_id = 'R-' + run_id[4:12].upper() if run_id.startswith('RUN-') and len(run_id) >= 12 else run_id
    subtitle = ' · '.join((preference, cluster, time_text))
    detail = '\n'.join((situation_name + ' · ' + damage, subtitle, run_id,
                         '配置：' + json.dumps(config, ensure_ascii=False, sort_keys=True),
                         '时间：' + json.dumps({key: record.get(key) for key in ('created_at', 'started_at', 'finished_at')}, ensure_ascii=False)))
    normalized = RunConfig.from_mapping(config).to_dict()
    key = {'situation_id': situation.get('situation_id') or record.get('situation_id'),
           'run_config': normalized, 'damage_events': scenario.get('events') or [],
           'base_inputs': {'airports': situation.get('airports'), 'missions': situation.get('missions'),
                           'catalogs': payload.get('catalogs'), 'od_distances': payload.get('od_distances'),
                           'dynamic_inventory': payload.get('dynamic_inventory')}}
    configuration_key = hashlib.sha256(json.dumps(key, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf8')).hexdigest()
    return {'run_id': run_id, 'title': damage, 'situation_title': situation_name + ' · ' + damage,
            'subtitle': subtitle, 'short_id': short_id, 'detail': detail, 'configuration_key': configuration_key}


def frozen_run_label(payload: Mapping[str, Any]) -> str:
    """Display-only label. The source Run ID remains the unique instance key."""
    situation = payload.get("situation") or {}
    config = payload.get("run_config") or {}
    name = situation.get("name") or situation.get("situation_name") or situation.get("situation_id")
    selected = config.get("damage_scenario_id")
    damage_name = None
    for scenario in situation.get("damage_scenarios") or []:
        if scenario.get("damage_scenario_id") == selected:
            damage_name = scenario.get("name") or scenario.get("scenario_name")
            break
    parts = [str(name)] if name else []
    parts.append(str(damage_name or selected) if selected else "无损毁")
    if isinstance(config.get("cluster_enabled"), bool):
        parts.append("组群开启" if config["cluster_enabled"] else "组群关闭")
    if config.get("preference_mode"):
        parts.append(str(config["preference_mode"]))
    return " · ".join(parts)
