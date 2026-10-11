import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from backend.auth.principal import Principal
from backend.analysis.comparison import ComparisonError
from backend.services.run_comparison_record_service import RunComparisonRecordService, ComparisonConflictError, ComparisonNotFoundError
from backend.services.run_result_service import RunResultService
from backend.storage.database import connect, initialize_database
from backend.storage.run_comparison_repository import RunComparisonRepository
from backend.storage.run_repository import RunRepository, content_hash
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.assessment_repository import AssessmentRepository
from backend.storage.airport_repository import AirportRepository
from backend.storage.indicator_repository import IndicatorRepository
from backend.domain.airport import AirportBase
from backend.domain.run_snapshot import RunSnapshot
from backend.services.run_capacity_assessment import RunCapacityAssessmentService
from backend.services.run_indicator_assessment import RunIndicatorAssessmentService
from tests.algorithm.test_snapshot_adapter import make_snapshot
from tests.analysis.test_comparison import scenario, early_scenario, solve, with_frozen_config


@pytest.fixture
def env(tmp_path):
    db = tmp_path/'comparison.sqlite'
    initialize_database(db)
    runs = RunRepository(db)
    results = RunResultService(run_repository=runs, snapshot_repository=RunSnapshotRepository(db))
    service = RunComparisonRecordService(result_service=results, repository=RunComparisonRepository(db))
    ds, early = scenario(), early_scenario()
    common = (ds, early)
    snapshots = [
        make_snapshot(run_id='R0', cluster_enabled=False, available_scenarios=common, include_storage_capacity=True),
        make_snapshot(run_id='R1', cluster_enabled=False, scenario=ds, available_scenarios=common, include_storage_capacity=True),
        make_snapshot(run_id='R2', cluster_enabled=True, scenario=ds, available_scenarios=common, include_storage_capacity=True),
        make_snapshot(run_id='R3', cluster_enabled=False, scenario=early, available_scenarios=common, include_storage_capacity=True),
    ]
    # A positive fleet baseline produces actual four-indicator/risk artifacts, so
    # immutability assertions cannot pass merely because the value table is empty.
    normalized = []
    for snap in snapshots:
        payload = snap.to_dict()
        for airport in payload['situation']['airports']:
            for support in airport['operational_profile']['aircraft_support']:
                if support['initial_quantity'] == 0:
                    support['initial_quantity'] = 1
        digest,text = content_hash(payload)
        normalized.append(RunSnapshot(snap.run_id,snap.situation_id,digest,text))
    snapshots = normalized
    snapshots.append(with_frozen_config(snapshots[2], run_id='P2', alpha={'a1':.2,'a2':.3,'a3':.5}))
    assessments = AssessmentRepository(db)
    for row in snapshots[0].to_dict()['situation']['airports']:
        AirportRepository(db).save_airport(AirportBase.from_mapping(row['airport']))
    for snap in snapshots:
        runs.create_queued(snapshot=snap, owner_user_id='U1')
        runs.claim_running(snap.run_id)
        result, metrics = solve(snap)
        runs.save_success(snap.run_id, solution=result.solution.to_dict(), metrics=metrics)
        runs.append_event(snap.run_id,level='INFO',stage='persistence',event='fixture_success',message='canonical fixture saved')
        capacity = RunCapacityAssessmentService(run_repository=runs,snapshot_repository=results.snapshots,assessment_repository=assessments)
        assert capacity.generate_capacity_assessment(snap.run_id)['status'] == 'generated'
        evaluation=RunIndicatorAssessmentService(run_repository=runs,snapshot_repository=results.snapshots,
            indicator_repository=IndicatorRepository(db),assessment_repository=assessments).evaluate(snap.run_id)
        assert evaluation.status == 'available' and len(evaluation.indicators) == 4
    return db, service, runs


OWNER = Principal('U1', role='operator')
ADMIN = Principal('ADMIN', role='admin')


def selection(mode):
    if mode in ('damage_effect', 'cluster_effect'):
        return {'r0_run_id':'R0', 'r1_run_id':'R1', 'r2_run_id':'R2'}
    if mode == 'multi_scenario':
        return {'run_ids':['R0','R1','R3'], 'baseline_run_id':'R1', 'comparison_run_id':'R3'}
    return {'run_ids':['R1','R2','P2'], 'baseline_run_id':'R1', 'comparison_run_id':'P2'}


def request(service, mode='damage_effect', selected=None, principal=OWNER):
    selected = selected or selection(mode)
    preview = service.preview(mode, selected, principal=principal)
    return {'comparison_mode':mode, 'selection':selected, 'expected_provenance':preview['save_context']['sources'],
            'comparison_version':preview['save_context']['comparison_version']}


@pytest.mark.parametrize('mode,pair', [('damage_effect',('R0','R1')),('cluster_effect',('R1','R2')),
                                      ('multi_scenario',('R1','R3')),('configuration',('R1','P2'))])
def test_four_modes_pair_projection_and_no_canonical_or_2a_mutation(env,mode,pair):
    db, service, _ = env
    with connect(db) as conn:
        before = {table:[tuple(row) for row in conn.execute('SELECT * FROM '+table)] for table in (
            'runs','run_input_snapshots','run_results','run_events','assessment_result','assessment_run_evaluation','assessment_indicator_value','indicator_nodes','indicator_sets','indicator_weight_config','assessment_airport_risk')}
    assert before['assessment_result'] and before['assessment_run_evaluation'] and before['assessment_indicator_value']
    body = request(service,mode)
    saved, created = service.save(body,principal=OWNER)
    assert created
    assert (saved['base_run_id'],saved['compared_run_id']) == pair
    envelope = saved['result_json']
    assert set(envelope) == {'schema_version','focus_comparison','pair_facts','labels','eligibility_evidence'}
    assert 'difference_overview' not in envelope and 'summary' not in envelope
    assert set(envelope['pair_facts']['scheme']) <= set(pair)
    assert saved['conclusion'] == '；'.join(envelope['focus_comparison']['conclusions'])
    if mode == 'configuration':
        assert envelope['focus_comparison']['objective_components']['J']['definition_comparable'] is False
        assert envelope['focus_comparison']['objective_components']['J']['delta'] is None
        assert envelope['pair_facts']['key_configuration_differences']
    with connect(db) as conn:
        after = {table:[tuple(row) for row in conn.execute('SELECT * FROM '+table)] for table in before}
    assert after == before


def test_metrics_hash_stale_valid_new_content_retains_saved_facts(env):
    db,service,_=env
    body=request(service)
    saved,_=service.save(body,principal=OWNER)
    with connect(db) as conn:
        raw=conn.execute("SELECT metrics_json FROM run_results WHERE run_id='R0'").fetchone()[0]
        metrics=json.loads(raw)
        metrics['technical']['objective']=999
        digest,text=content_hash(metrics)
        conn.execute("UPDATE run_results SET metrics_json=?, metrics_hash=? WHERE run_id='R0'",(text,digest))
    read=service.get(saved['id'],principal=OWNER)
    assert read['provenance_status']=='stale' and read['provenance_reasons']==['metrics_hash_mismatch']
    assert read['result_json']==saved['result_json']
    with pytest.raises(ComparisonConflictError):
        service.save(body,principal=OWNER)


def test_recorded_zero_and_missing_component_remain_distinct_in_saved_facts(env):
    db,service,_=env
    with connect(db) as conn:
        for rid in ('R1','R2'):
            metrics=json.loads(conn.execute('SELECT metrics_json FROM run_results WHERE run_id=?',(rid,)).fetchone()[0])
            metrics['technical']['f1']=0
            metrics['technical'].pop('f2',None)
            digest,text=content_hash(metrics)
            conn.execute('UPDATE run_results SET metrics_json=?,metrics_hash=? WHERE run_id=?',(text,digest,rid))
    selected={'run_ids':['R1','R2'],'baseline_run_id':'R1','comparison_run_id':'R2'}
    saved,_=service.save(request(service,'configuration',selected),principal=OWNER)
    components=saved['result_json']['focus_comparison']['objective_components']
    assert components['F1']['recorded'] is True and components['F1']['delta']==0
    assert components['F2']['recorded'] is False and components['F2']['delta'] is None
    assert components['F2']['baseline'] is None and components['F2']['current'] is None


def test_drift_inside_insertion_transaction_zero_rows(env):
    db,service,runs=env
    body=request(service)
    insert=service.repository.insert_or_get_existing
    def remove_then_insert(record,**kwargs):
        runs.delete_terminal('R2')
        return insert(record,**kwargs)
    with patch.object(service.repository,'insert_or_get_existing',side_effect=remove_then_insert):
        with pytest.raises(ComparisonConflictError):
            service.save(body,principal=OWNER)
    assert service.repository.list(owner_user_id=None)[1]==0


def test_mode_version_distinct_and_pair_facts_conflict_never_overwrites(env):
    db,service,_=env
    cluster,created=service.save(request(service,'cluster_effect'),principal=OWNER)
    config=request(service,'configuration',{'run_ids':['R1','R2'],'baseline_run_id':'R1','comparison_run_id':'R2'})
    assert service.save(config,principal=OWNER)[1]
    with patch('backend.services.run_comparison_record_service.COMPARISON_VERSION','strict-focus.v2'):
        assert service.save(request(service,'cluster_effect'),principal=OWNER)[1]
    from backend.storage.run_comparison_repository import ComparisonInvariantError
    original=service._compute
    def changed(*args,**kwargs):
        payload=original(*args,**kwargs)
        payload['comparisons']['cluster']['values']['fulfilled_sorties_total']['delta']=999
        return payload
    body=request(service,'cluster_effect')
    with patch.object(service,'_compute',side_effect=changed):
        with pytest.raises(ComparisonInvariantError):
            service.save(body,principal=OWNER)
    assert service.get(cluster['id'],principal=OWNER)['result_json']==cluster['result_json']


def test_full_six_multi_and_three_config_members_are_evidence_only(env):
    db,service,runs=env
    base=service.results.snapshots.get('R3')
    for rid in ['R4','R5','R6']:
        payload = base.to_dict()
        damage = dict(payload['situation']['damage_scenarios'][1], damage_scenario_id='DS-'+rid)
        payload['situation']['damage_scenarios'].append(damage)
        payload['run_id'] = rid
        payload['run_config']['damage_scenario_id'] = damage['damage_scenario_id']
        digest, text = content_hash(payload)
        snap = RunSnapshot(rid, base.situation_id, digest, text)
        result,metrics=solve(snap)
        runs.create_queued(snapshot=snap,owner_user_id='U1')
        runs.claim_running(rid)
        runs.save_success(rid,solution=result.solution.to_dict(),metrics=metrics)
    selected={'run_ids':['R0','R1','R3','R4','R5','R6'],'baseline_run_id':'R0','comparison_run_id':'R3'}
    saved,_=service.save(request(service,'multi_scenario',selected),principal=OWNER)
    assert len(saved['result_json']['eligibility_evidence']['sources'])==6
    assert set(saved['result_json']['pair_facts']['run_summaries'])=={'R0','R3'}
    selected['run_ids']=['R6','R5','R3','R0']
    assert service.save(request(service,'multi_scenario',selected),principal=OWNER)==(saved,False)
    assert saved['conclusion']== '；'.join(saved['result_json']['focus_comparison']['conclusions'])


def test_full_triple_rechecked_before_save_and_nonfocus_not_read_dependency(env):
    db, service, runs = env
    body = request(service)
    record, _ = service.save(body,principal=OWNER)
    runs.delete_terminal('R2')
    assert service.get(record['id'],principal=OWNER)['provenance_status'] == 'current'
    with pytest.raises(ComparisonNotFoundError):
        service.save(body,principal=OWNER)


def test_damage_scheme_role_keys_are_projected_to_complete_real_run_ids(env):
    _,service,runs=env
    ids=['RUN-BASE-00000001','RUN-DAMAGE-00000002','RUN-CLUSTER-00000003']
    for source,rid in zip(('R0','R1','R2'),ids):
        snap=with_frozen_config(service.results.snapshots.get(source),run_id=rid)
        result,metrics=solve(snap)
        runs.create_queued(snapshot=snap,owner_user_id='U1')
        runs.claim_running(rid)
        runs.save_success(rid,solution=result.solution.to_dict(),metrics=metrics)
    selected=dict(zip(('r0_run_id','r1_run_id','r2_run_id'),ids))
    preview=service.preview('damage_effect',selected,principal=OWNER)
    for mode,roles in [('damage_effect',('R0','R1')),('cluster_effect',('R1','R2'))]:
        saved,_=service.save(request(service,mode,selected),principal=OWNER)
        facts=saved['result_json']['pair_facts']
        assert facts['scheme']=={preview['roles'][role]:preview['scheme'][role] for role in roles}
        assert set(facts['run_summaries'])=={preview['roles'][role] for role in roles}


def test_overall_strict_rejection_zero_writes(env):
    db, service, _ = env
    selected = {'r0_run_id':'R0','r1_run_id':'R1','r2_run_id':'P2'}
    with pytest.raises(ComparisonError):
        service.preview('damage_effect',selected,principal=OWNER)
    assert service.repository.list(owner_user_id=None)[1] == 0


def test_idempotency_context_order_direction_owner_and_concurrency(env):
    db, service, _ = env
    body = request(service,'multi_scenario')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _:service.save(body,principal=OWNER),range(4)))
    assert sum(created for _,created in results) == 1
    first = results[0][0]
    changed = selection('multi_scenario')
    changed['run_ids'] = ['R3','R1']
    repeated, created = service.save(request(service,'multi_scenario',changed),principal=OWNER)
    assert not created and repeated == first
    swapped = dict(changed,baseline_run_id='R3',comparison_run_id='R1')
    assert service.save(request(service,'multi_scenario',swapped),principal=OWNER)[1]
    assert service.save(request(service,'multi_scenario',principal=ADMIN),principal=ADMIN)[1]
    assert service.list(principal=OWNER,limit=1,offset=1)['total'] == 2
    assert service.list(principal=ADMIN)['total'] == 3


@pytest.mark.parametrize('target', ['snapshot','metrics'])
def test_actual_content_tampering_rejected_and_history_unavailable(env,target):
    db, service, _ = env
    body = request(service)
    saved, _ = service.save(body,principal=OWNER)
    with connect(db) as conn:
        if target == 'snapshot':
            conn.execute("UPDATE run_input_snapshots SET payload_json=replace(payload_json,'No damage','Changed') WHERE run_id='R0'")
            # Guaranteed change, independent of display names.
            conn.execute("UPDATE run_input_snapshots SET payload_json=payload_json || ' ' WHERE run_id='R0'")
        else:
            conn.execute("UPDATE run_results SET metrics_json=replace(metrics_json,'metrics.v1','metrics.v9') WHERE run_id='R0'")
    with pytest.raises(ComparisonConflictError):
        service.save(body,principal=OWNER)
    assert service.get(saved['id'],principal=OWNER)['provenance_reasons'] == ['source_integrity_failed']


def test_expected_version_drift_missing_status_and_unreadable_history(env):
    db, service, runs = env
    body = request(service)
    saved,_ = service.save(body,principal=OWNER)
    for change in ({'comparison_version':'old'}, {'expected_provenance':[dict(s,metrics_hash='0'*64) for s in body['expected_provenance']]}):
        with pytest.raises(ComparisonConflictError):
            service.save(dict(body,**change),principal=OWNER)
    with patch('backend.services.run_comparison_record_service.COMPARISON_VERSION','strict-focus.v2'):
        assert service.get(saved['id'],principal=OWNER)['provenance_reasons'] == ['comparison_version_changed']
    with connect(db) as conn:
        conn.execute("UPDATE runs SET snapshot_hash=? WHERE run_id='R0'",('c'*64,))
    assert 'snapshot_hash_mismatch' in service.get(saved['id'],principal=OWNER)['provenance_reasons']
    with connect(db) as conn:
        conn.execute("UPDATE runs SET status='failed', failure_message='test failure' WHERE run_id='R0'")
    assert service.get(saved['id'],principal=OWNER)['provenance_status'] == 'unavailable'
    runs.delete_terminal('R0')
    assert service.get(saved['id'],principal=OWNER)['provenance_reasons'] == ['source_missing']
    with connect(db) as conn:
        conn.execute('UPDATE assessment_comparison SET result_json=? WHERE id=?',('{bad',saved['id']))
    unreadable=service.get(saved['id'],principal=OWNER)
    assert 'saved_record_unreadable' in unreadable['provenance_reasons']
    assert unreadable['result_json'] == '{bad'


def test_permission_visibility_downgrade_and_no_unauthorized_fact_leak(env):
    db,service,_ = env
    body=request(service)
    saved,_=service.save(body,principal=OWNER)
    with pytest.raises(PermissionError):
        service.save(body,principal=Principal('U1',role='viewer'))
    assert service.get(saved['id'],principal=Principal('U1',role='viewer'))['id']==saved['id']
    with pytest.raises(ComparisonNotFoundError):
        service.get(saved['id'],principal=Principal('U2',role='operator'))
    assert service.list(principal=Principal('U2',role='operator'))['total']==0
    with connect(db) as conn:
        conn.execute("UPDATE runs SET owner_user_id='U2' WHERE run_id='R0'")
    with pytest.raises(ComparisonNotFoundError):
        service.get(saved['id'],principal=OWNER)
    hidden=service.list(principal=OWNER)['items'][0]
    assert 'result_json' not in hidden and 'base_run_id' not in hidden


def test_repeat_identity_is_exposed_but_cannot_be_a_second_damage_scheme(env):
    _db, service, runs = env
    snapshot = with_frozen_config(service.results.snapshots.get('R1'), run_id='R1-REPEAT')
    runs.create_queued(snapshot=snapshot, owner_user_id='U1')
    runs.claim_running(snapshot.run_id)
    result, metrics = solve(snapshot)
    runs.save_success(snapshot.run_id, solution=result.solution.to_dict(), metrics=metrics)
    original = service.results.get_run_detail('R1', actor_user_id='U1')
    repeat = service.results.get_run_detail('R1-REPEAT', actor_user_id='U1')
    assert original['display_identity']['configuration_key'] == repeat['display_identity']['configuration_key']
    assert original['display_identity']['short_id'] != repeat['display_identity']['short_id']
    candidates = service.results.list_comparable_successful('R1', mode='multi_scenario', actor_user_id='U1')
    assert 'R1-REPEAT' not in [row['run_id'] for row in candidates['items']]
    selected = {'run_ids':['R1','R1-REPEAT'], 'baseline_run_id':'R1', 'comparison_run_id':'R1-REPEAT'}
    with pytest.raises(ComparisonError, match='同一损毁方案'):
        service.preview('multi_scenario', selected, principal=OWNER)
    # Save recomputes and validates too; matching provenance cannot bypass the guard.
    with connect(service.db_path) as conn:
        bundles = service._sources(conn, selected['run_ids'], OWNER)
    with pytest.raises(ComparisonError, match='同一损毁方案'):
        service.save({'comparison_mode':'multi_scenario', 'selection':selected,
                      'expected_provenance':[bundle[0] for bundle in bundles],
                      'comparison_version':request(service)['comparison_version']}, principal=OWNER)


def test_saved_run_identity_freezes_time_and_configuration_without_new_columns(env):
    db, service, _ = env
    saved, _ = service.save(request(service), principal=OWNER)
    identity = saved['result_json']['labels']['runs']['R1']['display_identity']
    assert '出动优先' in identity['subtitle']
    assert '时间未记录' not in identity['subtitle']
    with connect(db) as conn:
        conn.execute("UPDATE runs SET started_at='2027-01-01 00:00:00' WHERE run_id='R1'")
    read = service.get(saved['id'], principal=OWNER)
    assert read['result_json']['labels']['runs']['R1']['display_identity'] == identity
