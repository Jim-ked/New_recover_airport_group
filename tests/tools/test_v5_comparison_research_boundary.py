import json
from types import SimpleNamespace
from unittest.mock import patch

from backend.storage.database import connect, initialize_database
from tools.dev_validation.rebuild_v5_experiment import load_definition, validate_experiment_comparisons


def test_v5_research_keeps_historical_nine_as_artifact_and_never_formal_rows(tmp_path):
    db=tmp_path/'research.sqlite'
    initialize_database(db)
    definition=load_definition('resources/experiments/jiangsu_six_airport_v5.json')
    def focus(base,current):
        return {'baseline_run_id':base,'comparison_run_id':current,'eligible':True,'values':{},'conclusions':['research']}
    def damage(body,**kwargs):
        return SimpleNamespace(status=200,body={'comparisons':{
            'damage':focus(body['r0_run_id'],body['r1_run_id']),
            'cluster':focus(body['r1_run_id'],body['r2_run_id'])}})
    def pair(body,**kwargs):
        return SimpleNamespace(status=200,body={'focus_comparison':focus(body['baseline_run_id'],body['comparison_run_id'])})
    api=SimpleNamespace(damage_comparison=damage,scenario_comparison=pair,configuration_comparison=pair)
    with patch('tools.dev_validation.rebuild_v5_experiment.build_results_api',return_value=api):
        result=validate_experiment_comparisons(db,definition)
        disabled=validate_experiment_comparisons(db,definition,persist_records=False)
    assert result['persisted_records']==disabled['persisted_records']==0
    assert result['research_evidence_count']==disabled['research_evidence_count']==9
    assert disabled['research_artifact'] is None
    evidence=json.loads(db.with_suffix('.comparison-research-evidence.json').read_text(encoding='utf8'))
    assert evidence['historical_candidate_count']==12 and evidence['historical_unique_pair_count']==9
    modes={(row['base_label'],row['compared_label']):row['mode'] for row in evidence['records']}
    assert modes[('R0','R1')]=='multi_damage'
    assert modes[('R1','R2')]==modes[('P1','P2')]=='configuration'
    with connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM assessment_comparison').fetchone()[0]==0
