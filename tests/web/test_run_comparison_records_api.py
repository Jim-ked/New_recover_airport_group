from unittest.mock import patch

import pytest

from backend.auth.principal import Principal
from backend.storage.audit_repository import AuditRepository
from backend.web.app import create_app
from backend.web.composition import build_results_api, build_assessment_results_api, build_run_api
from backend.web.flask_audit import install_audit_hook
from backend.web.flask_auth import session_principal_resolver
from tests.services.test_run_comparison_record_service import env, request, OWNER


@pytest.fixture
def client(env):
    db, service, _ = env
    api = build_results_api(db)
    app = create_app(run_api=build_run_api(db), results_api=api,
                     assessment_results_api=build_assessment_results_api(db), secret_key='comparison-test-only', enable_ui=False)
    audit = AuditRepository(db)
    install_audit_hook(app,repository=audit,principal_resolver=session_principal_resolver)
    client = app.test_client()
    with client.session_transaction() as session:
        session['user']={'user_id':'U1','role':'operator'}
        session['csrf_token']='comparison-test-token'
    return client, api, audit


HEADERS={'X-CSRF-Token':'comparison-test-token'}


def test_post_get_list_csrf_and_audit_real_record_id(env,client):
    _,service,_ = env
    http,api,audit = client
    body = request(service)
    assert http.post('/api/results/comparison-records',json=body).status_code == 403
    response = http.post('/api/results/comparison-records',json=body,headers=HEADERS)
    assert response.status_code == 201
    record = response.json['record']
    repeated = http.post('/api/results/comparison-records',json=body,headers=HEADERS)
    assert repeated.status_code == 200 and repeated.json['record']==record
    assert repeated.json['created'] is False
    assert http.get('/api/results/comparison-records/'+record['id']).json == record
    listing = http.get('/api/results/assessment/comparisons?limit=1&offset=0').json
    assert listing['total']==1 and listing['items'][0]['id']==record['id']
    assert http.get('/api/results/assessment/comparisons?offset=1').json['items']==[]
    assert http.get('/api/results/assessment/comparisons?offset=-1').status_code==400
    with audit.connect() as conn:
        rows=conn.execute("SELECT * FROM audit_events WHERE request_path='/api/results/comparison-records' ORDER BY rowid").fetchall()
    assert len(rows)==3
    for row in rows[1:]:
        assert row['resource_id']==record['id'] and row['resource_type']=='results'
        assert row['action']=='POST /api/results/comparison-records'
        assert 'result_json' not in row['details_json'] and 'csrf' not in row['details_json']
    from backend.storage.audit_repository import audit_action_label
    assert audit_action_label(request_method='POST',request_path='/api/results/comparison-records',resource_type='results')=='保存结果比较'
    with patch.object(api.comparison_records,'_compute',side_effect=AssertionError('read must not compute')):
        assert http.get('/api/results/comparison-records/'+record['id']).status_code==200
        assert http.get('/api/results/assessment/comparisons').status_code==200


@pytest.mark.parametrize('field', ['creator','created_by_user_id','created_at','conclusion','result_json','idempotency_key','facts'])
def test_reject_client_facts(env,client,field):
    _,service,_=env
    body=dict(request(service),**{field:'untrusted'})
    assert client[0].post('/api/results/comparison-records',json=body,headers=HEADERS).status_code==400
    assert service.repository.list(owner_user_id=None)[1]==0


def test_auth_permissions_and_conflict_statuses(env,client):
    _,service,_=env
    http,api,_=client
    body=request(service)
    with http.session_transaction() as session:
        session.pop('user')
    assert http.post('/api/results/comparison-records',json=body,headers=HEADERS).status_code==401
    with http.session_transaction() as session:
        session['user']={'user_id':'U1','role':'viewer'}
    assert http.post('/api/results/comparison-records',json=body,headers=HEADERS).status_code==403
    with http.session_transaction() as session:
        session['user']={'user_id':'U2','role':'operator'}
    assert http.post('/api/results/comparison-records',json=body,headers=HEADERS).status_code==404
    with http.session_transaction() as session:
        session['user']={'user_id':'U1','role':'operator'}
    assert http.post('/api/results/comparison-records',json=dict(body,comparison_version='old'),headers=HEADERS).status_code==409
    selected=dict(body['selection'],r2_run_id='P2')
    sources=service.preview('configuration',{'run_ids':['R1','P2'],'baseline_run_id':'R1','comparison_run_id':'P2'},principal=OWNER)['save_context']['sources']
    invalid=dict(body,selection=selected,expected_provenance=[body['expected_provenance'][0],*sources])
    assert http.post('/api/results/comparison-records',json=invalid,headers=HEADERS).status_code==422
    with patch.object(api.comparison_records.repository,'insert_or_get_existing',side_effect=RuntimeError('storage unavailable')):
        assert http.post('/api/results/comparison-records',json=body,headers=HEADERS).status_code==500
    assert service.repository.list(owner_user_id=None)[1]==0
