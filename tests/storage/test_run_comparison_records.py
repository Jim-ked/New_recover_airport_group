import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.domain.run_comparison import RunComparisonRecord, comparison_identity
from backend.storage.database import _MIGRATIONS, connect, initialize_database
from backend.storage.run_comparison_repository import RunComparisonRepository, ComparisonInvariantError


def record(owner='U1', mode='configuration', base='A', other='B', version='strict-focus.v1'):
    sources = [{'run_id': rid, 'snapshot_hash': 'a'*64, 'metrics_hash': 'b'*64} for rid in (base, other)]
    if mode in ('damage_effect','cluster_effect'):
        context = ['CONTEXT',base,other] if mode=='cluster_effect' else [base,other,'CONTEXT']
        selected = dict(zip(('r0_run_id','r1_run_id','r2_run_id'),context))
        evidence_sources = [{'run_id':rid,'snapshot_hash':'a'*64,'metrics_hash':'b'*64} for rid in context]
    else:
        selected = {'run_ids':[base,other],'baseline_run_id':base,'comparison_run_id':other}
        evidence_sources = sources
    return RunComparisonRecord(
        id='CMP-'+owner+mode+base+other+version, comparison_mode=mode,
        base_run_id=base, compared_run_id=other,
        base_snapshot_hash='a'*64, base_metrics_hash='b'*64,
        compared_snapshot_hash='a'*64, compared_metrics_hash='b'*64,
        comparison_version=version, created_by_user_id=owner, created_at='2026-10-08T00:00:00Z',
        conclusion='已保存事实', result_json=json.dumps({
            'schema_version':'comparison-record.v1',
            'focus_comparison':{'baseline_run_id':base,'comparison_run_id':other,'eligible':True,'reasons':[],
                'values':{},'objective_components':{},'structural_changes':{},'timeline':{},'assurance':{},'conclusions':['已保存事实']},
            'pair_facts':{},'labels':{'runs':{rid:{'run_id':rid,'label':rid} for rid in (base,other)}},
            'eligibility_evidence':{'selection':selected,'sources':evidence_sources,'eligible':True,'reasons':[]}},
            ensure_ascii=False,separators=(',',':')),
        idempotency_key=comparison_identity(owner, mode, sources, version),
    )


@pytest.mark.parametrize('legacy_count',[1,9])
def test_migration_empty_repeat_and_nonempty_fail_closed(tmp_path,legacy_count):
    db = tmp_path/'test.sqlite'
    # Simulate the previously registered schema, exclusively in a temporary database.
    with connect(db) as conn:
        conn.execute('CREATE TABLE schema_migrations (migration_id TEXT PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)')
        for name, apply in _MIGRATIONS[:-1]:
            apply(conn)
            conn.execute('INSERT INTO schema_migrations(migration_id) VALUES (?)', (name,))
        conn.executemany('INSERT INTO assessment_comparison(id,conclusion) VALUES (?,?)',
                         [(f'OLD-{index}','research evidence') for index in range(legacy_count)])
        old_rows=[tuple(row) for row in conn.execute('SELECT * FROM assessment_comparison ORDER BY id')]
        old_schema=conn.execute("SELECT sql FROM sqlite_master WHERE name='assessment_comparison'").fetchone()[0]
    with pytest.raises(RuntimeError, match='nonempty'):
        initialize_database(db)
    with connect(db) as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM assessment_comparison ORDER BY id')]==old_rows
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name='assessment_comparison'").fetchone()[0]==old_schema
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE migration_id LIKE 'v027%'").fetchone()[0] == 0
        assert 'base_run_id' not in [r['name'] for r in conn.execute('PRAGMA table_info(assessment_comparison)')]
        conn.execute('DELETE FROM assessment_comparison')
    initialize_database(db)
    initialize_database(db)
    with connect(db) as conn:
        assert 'base_run_id' in [r['name'] for r in conn.execute('PRAGMA table_info(assessment_comparison)')]
        assert not conn.execute('PRAGMA foreign_key_list(assessment_comparison)').fetchall()


def test_migration_ddl_failure_rolls_back_schema_and_version(tmp_path,monkeypatch):
    from backend.storage import database
    db=tmp_path/'test.sqlite'
    original=database._MIGRATIONS
    monkeypatch.setattr(database,'_MIGRATIONS',original[:-1])
    initialize_database(db)
    with connect(db) as conn:
        old_schema=conn.execute("SELECT sql FROM sqlite_master WHERE name='assessment_comparison'").fetchone()[0]
    def fail_after_ddl(conn):
        original[-1][1](conn)
        raise RuntimeError('simulated DDL failure')
    monkeypatch.setattr(database,'_MIGRATIONS',(*original[:-1],(original[-1][0],fail_after_ddl)))
    with pytest.raises(RuntimeError,match='DDL failure'):
        initialize_database(db)
    with connect(db) as conn:
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name='assessment_comparison'").fetchone()[0]==old_schema
        assert conn.execute('SELECT COUNT(*) FROM schema_migrations WHERE migration_id=?',(original[-1][0],)).fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'idx_comparison%'").fetchone()[0]==0


def test_repository_concurrency_immutability_and_identity(tmp_path):
    db = tmp_path/'test.sqlite'
    initialize_database(db)
    repo = RunComparisonRepository(db)
    original = record()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repo.insert_or_get_existing(original), range(8)))
    assert sum(created for _, created in results) == 1
    assert all(saved == original for saved, _ in results)
    assert repo.insert_or_get_existing(replace(original, id='OTHER', created_at='later')) == (original, False)
    with pytest.raises(ComparisonInvariantError):
        repo.insert_or_get_existing(replace(original, result_json=original.result_json.replace('"values":{}', '"values":{"changed":1}')))
    for row in (record(owner='U2'), record(mode='cluster_effect'), record(base='B',other='A'), record(version='strict-focus.v2')):
        assert repo.insert_or_get_existing(row)[1]
    assert repo.list(owner_user_id='U1', limit=2, offset=0)[1] == 4
    assert repo.list(owner_user_id=None, limit=50, offset=0)[1] == 5
    assert repo.get(original.id, owner_user_id='U2') is None


@pytest.mark.parametrize('changes', [
    {'comparison_mode':'invalid'}, {'compared_run_id':'A'}, {'base_metrics_hash':'G'*64},
    {'result_json':'{}'}, {'idempotency_key':'invalid'},
])
def test_domain_validation(changes):
    with pytest.raises(ValueError):
        replace(record(), **changes)
