"""Explicit focus saves over authoritative frozen sources. Reads never run comparison math."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from backend.analysis.comparison import (
    ComparisonError, build_r0_r1_r2_comparison, build_multi_scenario_comparison,
    build_configuration_comparison,
)
from backend.domain.run_comparison import (
    COMPARISON_MODES, COMPARISON_VERSION, RECORD_SCHEMA_VERSION,
    RunComparisonRecord, canonical_json, comparison_identity,
)
from backend.domain.run_snapshot import RunSnapshot
from backend.services.run_assessment_labels import frozen_run_label, run_display_identity
from backend.storage.database import connect
from backend.storage.run_repository import content_hash, RunRepositoryError


class ComparisonConflictError(RuntimeError):
    pass


class ComparisonNotFoundError(RuntimeError):
    pass


class _SourceUnavailable(ComparisonConflictError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class RunComparisonRecordService:
    def __init__(self, *, result_service, repository):
        self.results = result_service
        self.repository = repository
        self.db_path = repository.db_path

    @staticmethod
    def _selection(mode, raw):
        from backend.web.http import ApiInputError, reject_unknown, require_object, required_nonblank_string, required_nonblank_string_list
        if not isinstance(mode, str) or mode not in COMPARISON_MODES:
            raise ApiInputError('unsupported comparison_mode', field='comparison_mode')
        selected = require_object(raw, field='selection')
        if mode in {'damage_effect', 'cluster_effect'}:
            keys = ('r0_run_id', 'r1_run_id', 'r2_run_id')
            reject_unknown(selected, set(keys))
            ids = [required_nonblank_string(selected, key) for key in keys]
            pair = ids[:2] if mode == 'damage_effect' else ids[1:]
        else:
            reject_unknown(selected, {'run_ids', 'baseline_run_id', 'comparison_run_id'})
            ids = required_nonblank_string_list(selected, 'run_ids', minimum=2, maximum=6 if mode == 'multi_scenario' else 5)
            pair = [required_nonblank_string(selected, key) for key in ('baseline_run_id', 'comparison_run_id')]
        if len(set(ids)) != len(ids) or pair[0] == pair[1] or not set(pair) <= set(ids):
            raise ApiInputError('selection requires distinct sources and a selected directed focus', field='selection')
        return ids, pair

    @staticmethod
    def _source(conn, run_id, principal):
        run = conn.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if run is None:
            raise ComparisonNotFoundError('comparison source not found')
        if not principal.is_admin and run['owner_user_id'] != principal.user_id:
            raise ComparisonNotFoundError('comparison source not found')
        if run['status'] != 'succeeded':
            raise _SourceUnavailable('source_not_succeeded')
        snapshot_row = conn.execute('SELECT * FROM run_input_snapshots WHERE run_id=?', (run_id,)).fetchone()
        result = conn.execute('SELECT * FROM run_results WHERE run_id=?', (run_id,)).fetchone()
        if snapshot_row is None or result is None:
            raise _SourceUnavailable('source_missing')
        try:
            snapshot = RunSnapshot(run_id=run_id, situation_id=snapshot_row['situation_id'],
                                   content_hash=snapshot_row['snapshot_hash'], payload_json=snapshot_row['payload_json'])
            metrics = json.loads(result['metrics_json'])
            solution = json.loads(result['solution_json'])
            if not isinstance(metrics, dict) or not isinstance(solution, dict):
                raise ValueError('nonobject canonical results')
            if metrics.get('run_id') != run_id or solution.get('run_id') != run_id:
                raise ValueError('canonical run_id mismatch')
            if content_hash(metrics)[0] != result['metrics_hash'] or content_hash(solution)[0] != result['solution_hash']:
                raise ValueError('canonical content hash mismatch')
        except (ValueError, TypeError, RunRepositoryError) as exc:
            raise _SourceUnavailable('source_integrity_failed') from exc
        source = {'run_id':run_id, 'snapshot_hash':snapshot.content_hash, 'metrics_hash':result['metrics_hash']}
        return source, snapshot, metrics, run['snapshot_hash']

    def _sources(self, conn, ids, principal):
        bundles = [self._source(conn, rid, principal) for rid in ids]
        if any(source['snapshot_hash'] != run_hash for source, _snapshot, _metrics, run_hash in bundles):
            raise ComparisonConflictError('Run snapshot hash changed')
        return bundles

    @staticmethod
    def _compute(mode, selected, bundles):
        if mode in {'damage_effect', 'cluster_effect'}:
            return build_r0_r1_r2_comparison(
                r0_snapshot=bundles[0][1], r0_metrics=bundles[0][2],
                r1_snapshot=bundles[1][1], r1_metrics=bundles[1][2],
                r2_snapshot=bundles[2][1], r2_metrics=bundles[2][2],
            )
        builder = build_multi_scenario_comparison if mode == 'multi_scenario' else build_configuration_comparison
        return builder([(bundle[1],bundle[2]) for bundle in bundles],
                       baseline_run_id=selected['baseline_run_id'], comparison_run_id=selected['comparison_run_id'])

    def preview(self, mode, selected, *, principal):
        principal.require_permission('results.read')
        ids, _pair = self._selection(mode, selected)
        with connect(self.db_path) as conn:
            conn.execute('BEGIN')
            bundles = self._sources(conn, ids, principal)
            identities = self._identities(conn, bundles)
        payload = self._compute(mode, selected, bundles)
        payload['run_identities'] = identities
        payload['save_context'] = {'comparison_version':COMPARISON_VERSION,
                                   'sources':[bundle[0] for bundle in bundles]}
        return payload

    @staticmethod
    def _identities(conn, bundles):
        return {bundle[0]['run_id']: run_display_identity(bundle[1].to_dict(),
                dict(conn.execute('SELECT * FROM runs WHERE run_id=?', (bundle[0]['run_id'],)).fetchone()))
                for bundle in bundles}

    @staticmethod
    def _envelope(mode, selected, pair, payload, bundles):
        focus = (payload['comparisons']['damage' if mode == 'damage_effect' else 'cluster']
                 if mode in {'damage_effect', 'cluster_effect'} else payload['focus_comparison'])
        if payload.get('eligible') is False or focus.get('eligible') is not True:
            raise ComparisonError('strict selection is not eligible')
        # Every projection is taken from existing kernel output. No group conclusions,
        # extrema or baseline deltas from a third Run enter the immutable pair facts.
        scheme = payload.get('scheme', {})
        if mode in {'damage_effect','cluster_effect'}:
            # Damage kernel scheme uses R0/R1/R2 keys; records always use full Run IDs.
            scheme = {rid:scheme[role] for role,rid in payload['roles'].items()}
        facts = {'scheme':{rid:scheme[rid] for rid in pair},
                 'run_summaries':{rid:payload['run_summaries'][rid] for rid in pair}}
        solver = payload.get('solver_comparison', {}).get('by_run', {})
        facts['solver_facts'] = {rid:{key:value for key,value in solver.get(rid, {}).items()
                                     if key != 'objective_delta_vs_baseline'} for rid in pair}
        if mode == 'configuration':
            facts['key_configuration_differences'] = payload['key_configuration_differences']
        labels = {'runs':{bundle[0]['run_id']:{'run_id':bundle[0]['run_id'], 'label':frozen_run_label(bundle[1].to_dict()),
                          'display_identity': payload.get('run_identities', {}).get(bundle[0]['run_id'])}
                          for bundle in bundles if bundle[0]['run_id'] in pair},
                  'objects':payload.get('labels', {})}
        return {'schema_version':RECORD_SCHEMA_VERSION, 'focus_comparison':focus,
                'pair_facts':facts, 'labels':labels,
                'eligibility_evidence':{'selection':selected, 'sources':[bundle[0] for bundle in bundles],
                                        'eligible':True, 'reasons':[]}}

    def save(self, raw_body, *, principal):
        from backend.web.http import ApiInputError, reject_unknown, require_object, required_nonblank_string
        principal.require_permission('results.comparison.write')
        body = require_object(raw_body)
        reject_unknown(body, {'comparison_mode','selection','expected_provenance','comparison_version'})
        mode = required_nonblank_string(body, 'comparison_mode')
        version = required_nonblank_string(body, 'comparison_version')
        ids, pair = self._selection(mode, body.get('selection'))
        expected = body.get('expected_provenance')
        if not isinstance(expected, list) or len(expected) != len(ids):
            raise ApiInputError('expected_provenance must cover the complete selected context', field='expected_provenance')
        expected_by_id = {}
        for raw in expected:
            entry = require_object(raw, field='expected_provenance')
            reject_unknown(entry, {'run_id','snapshot_hash','metrics_hash'})
            rid = required_nonblank_string(entry,'run_id')
            for key in ('snapshot_hash','metrics_hash'):
                if not re.fullmatch(r'[0-9a-f]{64}', required_nonblank_string(entry,key)):
                    raise ApiInputError('expected hashes must be lowercase SHA-256', field=key)
            expected_by_id[rid] = dict(entry)
        if set(expected_by_id) != set(ids):
            raise ApiInputError('expected provenance sources must match selection', field='expected_provenance')
        if version != COMPARISON_VERSION:
            raise ComparisonConflictError('comparison semantic version changed; refresh preview')
        with connect(self.db_path) as conn:
            conn.execute('BEGIN')
            bundles = self._sources(conn, ids, principal)
            identities = self._identities(conn, bundles)
        if any(bundle[0] != expected_by_id[bundle[0]['run_id']] for bundle in bundles):
            raise ComparisonConflictError('expected provenance changed; refresh preview')
        payload = self._compute(mode, body['selection'], bundles)
        payload['run_identities'] = identities
        envelope = self._envelope(mode, body['selection'], pair, payload, bundles)
        by_id = {bundle[0]['run_id']:bundle[0] for bundle in bundles}
        sources = [by_id[rid] for rid in pair]
        record = RunComparisonRecord(
            id='CMP-'+uuid4().hex, comparison_mode=mode,
            base_run_id=pair[0], compared_run_id=pair[1],
            base_snapshot_hash=sources[0]['snapshot_hash'], base_metrics_hash=sources[0]['metrics_hash'],
            compared_snapshot_hash=sources[1]['snapshot_hash'], compared_metrics_hash=sources[1]['metrics_hash'],
            comparison_version=version, created_by_user_id=principal.user_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            conclusion='；'.join(envelope['focus_comparison']['conclusions']), result_json=canonical_json(envelope),
            idempotency_key=comparison_identity(principal.user_id,mode,sources,version),
        )

        def recheck(conn):
            # Short write transaction closes drift/deletion between computation and INSERT.
            try:
                current = self._sources(conn, ids, principal)
            except ComparisonNotFoundError as exc:
                raise ComparisonConflictError('sources became unavailable during save') from exc
            if any(bundle[0] != expected_by_id[bundle[0]['run_id']] for bundle in current):
                raise ComparisonConflictError('sources changed during save')
        saved, created = self.repository.insert_or_get_existing(record, source_check=recheck)
        return self._read(asdict(saved), principal=principal), created

    def _read(self, row, *, principal, redact=False):
        # Check ownership before parsing or returning any saved facts, even stale facts.
        with connect(self.db_path) as conn:
            conn.execute('BEGIN')
            for rid in (row['base_run_id'],row['compared_run_id']):
                current = conn.execute('SELECT owner_user_id FROM runs WHERE run_id=?',(rid,)).fetchone()
                if current and not principal.is_admin and current['owner_user_id'] != principal.user_id:
                    if not redact:
                        raise ComparisonNotFoundError('comparison record not found')
                    return {key:row[key] for key in ('id','comparison_mode','created_by_user_id','created_at')} | {
                        'provenance_status':'unavailable','provenance_reasons':['source_inaccessible']}
            output = dict(row)
            output.pop('idempotency_key', None)
            reasons = []
            unavailable = False
            try:
                envelope = RunComparisonRecord(**row).to_dict()['result_json']
                # Evidence is not pair membership; a now inaccessible context is redacted.
                for source in envelope['eligibility_evidence'].get('sources', []):
                    owner = conn.execute('SELECT owner_user_id FROM runs WHERE run_id=?',(source['run_id'],)).fetchone()
                    if owner and not principal.is_admin and owner['owner_user_id'] != principal.user_id:
                        envelope['eligibility_evidence'] = {'redacted':True,'reason':'context_inaccessible'}
                        break
                output['result_json'] = envelope
            except (ValueError, TypeError, KeyError):
                unavailable = True
                reasons.append('saved_record_unreadable')
            for prefix in ('base','compared'):
                try:
                    source, _snapshot, _metrics, run_hash = self._source(conn,row[prefix+'_run_id'],principal)
                    if source['snapshot_hash'] != row[prefix+'_snapshot_hash'] or run_hash != row[prefix+'_snapshot_hash']:
                        reasons.append('snapshot_hash_mismatch')
                    if source['metrics_hash'] != row[prefix+'_metrics_hash']:
                        reasons.append('metrics_hash_mismatch')
                except ComparisonNotFoundError:
                    unavailable = True
                    reasons.append('source_missing')
                except _SourceUnavailable as exc:
                    unavailable = True
                    reasons.append(exc.reason)
            if row['comparison_version'] != COMPARISON_VERSION:
                reasons.append('comparison_version_changed')
            output['provenance_status'] = 'unavailable' if unavailable else ('stale' if reasons else 'current')
            output['provenance_reasons'] = list(dict.fromkeys(reasons))
            return output

    def get(self, record_id, *, principal):
        principal.require_permission('results.read')
        row = self.repository.get(record_id, owner_user_id=None if principal.is_admin else principal.user_id)
        if row is None:
            raise ComparisonNotFoundError('comparison record not found')
        return self._read(row,principal=principal)

    def list(self, *, principal, limit=50, offset=0):
        principal.require_permission('results.read')
        rows,total = self.repository.list(owner_user_id=None if principal.is_admin else principal.user_id,limit=limit,offset=offset)
        return {'schema_version':RECORD_SCHEMA_VERSION,'items':[self._read(row,principal=principal,redact=True) for row in rows],
                'total':total,'limit':limit,'offset':offset}
