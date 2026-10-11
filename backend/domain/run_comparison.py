"""Immutable, directed focus comparison history; separate from Run facts."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

COMPARISON_VERSION = 'strict-focus.v1'
RECORD_SCHEMA_VERSION = 'comparison-record.v1'
COMPARISON_MODES = frozenset({'damage_effect', 'cluster_effect', 'multi_scenario', 'configuration'})
_HASH = re.compile(r'^[0-9a-f]{64}$')


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def comparison_identity(owner: str, mode: str, sources: list[dict], version: str) -> str:
    identity = {'created_by_user_id': owner, 'comparison_mode': mode,
                'ordered_sources': sources, 'comparison_version': version}
    return hashlib.sha256(canonical_json(identity).encode('utf8')).hexdigest()


def read_envelope(text: str, base: str, compared: str) -> dict:
    envelope = json.loads(text)
    fields = {'schema_version', 'focus_comparison', 'pair_facts', 'labels', 'eligibility_evidence'}
    if not isinstance(envelope, dict) or set(envelope) != fields or envelope['schema_version'] != RECORD_SCHEMA_VERSION:
        raise ValueError('invalid comparison-record.v1 envelope')
    if any(not isinstance(envelope[key], dict) for key in fields - {'schema_version'}):
        raise ValueError('comparison envelope sections must be objects')
    focus = envelope['focus_comparison']
    required = {'baseline_run_id', 'comparison_run_id', 'eligible', 'reasons', 'values',
                'objective_components', 'structural_changes', 'timeline', 'assurance', 'conclusions'}
    if not required <= set(focus) or focus['baseline_run_id'] != base or focus['comparison_run_id'] != compared:
        raise ValueError('saved focus must match the directed record pair')
    if focus['eligible'] is not True or not isinstance(focus['reasons'], list) or not isinstance(focus['conclusions'], list):
        raise ValueError('saved focus must be eligible with recorded conclusions/reasons')
    if any(not isinstance(focus[key], dict) for key in ('values', 'objective_components', 'structural_changes', 'timeline', 'assurance')):
        raise ValueError('invalid focus facts')
    if not focus['conclusions'] or any(not isinstance(value, str) for value in focus['conclusions'] + focus['reasons']):
        raise ValueError('focus conclusions/reasons must be recorded strings')
    evidence = envelope['eligibility_evidence']
    if evidence.get('eligible') is not True or not isinstance(evidence.get('selection'), dict) or not isinstance(evidence.get('reasons'), list):
        raise ValueError('strict eligibility evidence is required')
    sources = evidence.get('sources')
    if not isinstance(sources, list) or len(sources) < 2:
        raise ValueError('eligibility evidence requires selected source provenance')
    ids = []
    for source in sources:
        if not isinstance(source, dict) or set(source) != {'run_id','snapshot_hash','metrics_hash'}:
            raise ValueError('invalid evidence source')
        if not isinstance(source['run_id'], str) or not source['run_id'].strip():
            raise ValueError('invalid evidence Run ID')
        if any(not isinstance(source[key], str) or not _HASH.fullmatch(source[key]) for key in ('snapshot_hash','metrics_hash')):
            raise ValueError('invalid evidence hash')
        ids.append(source['run_id'])
    if len(set(ids)) != len(ids) or not {base,compared} <= set(ids):
        raise ValueError('evidence must contain the complete pair without duplicate sources')
    run_labels = envelope['labels'].get('runs')
    if not isinstance(run_labels, dict):
        raise ValueError('pair Run labels must be an object')
    for rid in (base,compared):
        label = run_labels.get(rid)
        if not isinstance(label, dict) or label.get('run_id') != rid or not isinstance(label.get('label'), str):
            raise ValueError('pair labels require full Run IDs')
    canonical_json(envelope)  # reject NaN/Infinity, including values accepted by json.loads
    return envelope


@dataclass(frozen=True)
class RunComparisonRecord:
    id: str
    comparison_mode: str
    base_run_id: str
    compared_run_id: str
    base_snapshot_hash: str
    base_metrics_hash: str
    compared_snapshot_hash: str
    compared_metrics_hash: str
    comparison_version: str
    created_by_user_id: str
    created_at: str
    conclusion: str
    result_json: str
    idempotency_key: str

    def __post_init__(self):
        for key, value in asdict(self).items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{key} must be nonblank')
        if self.comparison_mode not in COMPARISON_MODES or self.base_run_id == self.compared_run_id:
            raise ValueError('invalid comparison mode or identical sources')
        for key in ('base_snapshot_hash', 'base_metrics_hash', 'compared_snapshot_hash', 'compared_metrics_hash', 'idempotency_key'):
            if not _HASH.fullmatch(getattr(self, key)):
                raise ValueError(f'{key} must be lowercase SHA-256')
        envelope = read_envelope(self.result_json, self.base_run_id, self.compared_run_id)
        if self.conclusion != '；'.join(envelope['focus_comparison']['conclusions']):
            raise ValueError('conclusion must match frozen focus conclusions')
        sources = [{'run_id': rid, 'snapshot_hash': sh, 'metrics_hash': mh} for rid, sh, mh in (
            (self.base_run_id, self.base_snapshot_hash, self.base_metrics_hash),
            (self.compared_run_id, self.compared_snapshot_hash, self.compared_metrics_hash))]
        if self.idempotency_key != comparison_identity(self.created_by_user_id, self.comparison_mode, sources, self.comparison_version):
            raise ValueError('idempotency_key does not match server identity')
        evidence = {source['run_id']:source for source in envelope['eligibility_evidence']['sources']}
        if any(evidence[source['run_id']] != source for source in sources):
            raise ValueError('pair provenance must match eligibility evidence')
        selected = envelope['eligibility_evidence']['selection']
        if self.comparison_mode in {'damage_effect', 'cluster_effect'}:
            keys = ('r0_run_id','r1_run_id','r2_run_id')
            if set(selected) != set(keys):
                raise ValueError('damage record requires full triple evidence')
            selected_ids = [selected[key] for key in keys]
            expected_pair = selected_ids[:2] if self.comparison_mode == 'damage_effect' else selected_ids[1:]
        else:
            if set(selected) != {'run_ids','baseline_run_id','comparison_run_id'} or not isinstance(selected['run_ids'],list):
                raise ValueError('record requires the complete selected set and focus identity')
            selected_ids = selected['run_ids']
            expected_pair = [selected['baseline_run_id'],selected['comparison_run_id']]
            if not 2 <= len(selected_ids) <= (6 if self.comparison_mode == 'multi_scenario' else 5):
                raise ValueError('invalid evidence selection size')
        if any(not isinstance(rid,str) for rid in selected_ids) or set(selected_ids) != set(evidence) or len(selected_ids) != len(evidence):
            raise ValueError('evidence sources must match the complete validation selection')
        if expected_pair != [self.base_run_id,self.compared_run_id]:
            raise ValueError('evidence focus must match the record pair')

    def to_dict(self) -> dict:
        result = asdict(self)
        result.pop('idempotency_key')
        result['result_json'] = read_envelope(self.result_json, self.base_run_id, self.compared_run_id)
        return result
