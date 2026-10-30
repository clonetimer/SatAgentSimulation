"""A completed real native instance must not hide its failed mission result."""
import json
import time

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app


@pytest.mark.parametrize('continuous',[False,True])
def test_real_native_mission_failure_cannot_be_sealed_as_validation_pass(monkeypatch,tmp_path,continuous):
    monkeypatch.setenv('SAT_SIM_INTERACTIVE_ENABLED','1')
    monkeypatch.setenv('SAT_SIM_SUPERVISED_NATIVE_RUNTIME','1' if continuous else '0')
    app=create_app(runs_root=tmp_path/'runs',artifacts_root=tmp_path/'api',embedded_worker=False,auth_mode='disabled')
    identifier='native-mission-continuous' if continuous else 'native-mission-finite'
    spec={'session_id':identifier,'capability_id':'whole_spacecraft.unified_native.v1',
        'task_spec':{'simulation':{'duration_s':60.,'sample_s':.2,'solver':{'step_s':.1}},
            'parameters':{'values':{'environment_profile':'spice_wmm',
                'epoch_utc':'2026-01-01T00:00:00Z','initial_payload_temp_k':340.,
                # Explicit electrical heating, independent of standalone thermal defaults.
                'payload_power_w':200.,
                'payload_max_pointing_error_deg':20.,'payload_radiator_area_m2':0.}}},
        'continuous':continuous,'quantum_s':.2,'paced':continuous,'rate':10.,'max_sim_time_s':60.}
    base='/interactive/sessions/'+identifier
    with TestClient(app) as client:
        response=client.post('/interactive/sessions',json=spec)
        assert response.status_code==201,response.text
        for action in ['start']:
            response=client.post(base+'/control',json={'action':action})
            assert response.status_code==200,response.text
        deadline=time.monotonic()+60
        stop_sent=False
        while True:
            snapshot=client.get(base).json()
            if continuous and not stop_sent and snapshot['sim_time_s']>=60:
                response=client.post(base+'/control',json={'action':'stop'})
                assert response.status_code==200,response.text
                stop_sent=True
            if not snapshot['worker_alive'] and snapshot['state'] in ['COMPLETED','FAILED','ABORTED']:
                break
            assert time.monotonic()<deadline,snapshot
            time.sleep(.02)
        root=tmp_path/'api/interactive_sessions'/identifier
        captured=json.loads((root/'runtime_result.json').read_text(encoding='utf-8'))
        summary=captured['result']['summary']
        proof={'snapshot':snapshot,'runtime_execution_status':summary['execution_status'],
            'runtime_mission_status':summary['mission_status'],'mission_reason_codes':summary['mission_reason_codes']}
        path=root/'run_bundle/validation/validation_outcome.json'
        if path.exists():
            proof['sealed_validation']=json.loads(path.read_text(encoding='utf-8'))
        (tmp_path/'mission-closure-proof.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
        proof['retained_validation']=json.loads((root/'validation_outcome.json').read_text(encoding='utf-8'))
        assert proof['retained_validation']['result']=='FAIL',proof
        assert summary['mission_bounds_sample_count']>=300,proof
        assert summary['mission_bounds_scope']=='all_published_runtime_samples',proof
        assert summary['execution_status']=='PASS',proof
        assert summary['mission_status']=='FAIL',proof
        assert 'THERMAL_LIMIT_EXCEEDED' in summary['mission_reason_codes'],proof
        assert snapshot['archive_status']=='FAILED',proof
        assert not (root/'run_bundle/SEALED.json').exists(),proof
