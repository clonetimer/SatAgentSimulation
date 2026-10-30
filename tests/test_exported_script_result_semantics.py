"""Exported runners must distinguish completed execution from validation."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sat_sim.form_schema import capability_form_schema
from sat_sim.script_exporter import export_runner_script
from sat_sim.unified_agent import normalize_form_task_spec


@pytest.mark.parametrize('kind', ['capability-python', 'python'])
@pytest.mark.parametrize('scenario,expected', [
    ('healthy', 'PASS'),
    ('impossible_assertion', 'FAIL'),
    ('required_missing_evidence', 'INCONCLUSIVE'),
    ('native_hot_payload', 'FAIL'),
    ('metric_assertion_fail', 'FAIL'),
    ('metric_assertion_missing', 'FAIL'),
    ('metric_assertion_optional_missing', 'PASS'),
    ('metric_assertion_invalid', 'FAIL'),
])
def test_downloadable_script_exit_matches_actual_validation(tmp_path, scenario, expected, kind):
    capability = ('whole_spacecraft.unified_native.v1' if scenario == 'native_hot_payload'
                  else 'component.thermal_node.v1')
    form = capability_form_schema(capability)['default_form']
    if scenario == 'native_hot_payload':
        form['simulation'].update(duration_s=60.,sample_s=.2,step_s=.1)
        form['simulation']['solver'] = {'step_s':.1}
        form['parameters']['values'].update(environment_profile='spice_wmm',
            epoch_utc='2026-01-01T00:00:00Z',initial_payload_temp_k=340.,
            payload_power_w=200.,payload_max_pointing_error_deg=20.,payload_radiator_area_m2=0.)
    spec = normalize_form_task_spec(form)
    if scenario == 'impossible_assertion':
        spec['model']['validation'] = {'assertions':[{
            'id':'impossible_temperature','field':'thermal.node.temp_k','operator':'<','value':0.}]}
    elif scenario == 'required_missing_evidence':
        spec['model']['validation'] = {'physical_checks':['data_conservation']}
    elif scenario.startswith('metric_assertion'):
        declaration = [{'id':'metric_requirement','metric':'status','operator':'==','value':'IMPOSSIBLE'}]
        if scenario in {'metric_assertion_missing','metric_assertion_optional_missing'}:
            declaration = [{'id':'metric_requirement','metric':'missing.required.metric',
                            'operator':'truthy','required':scenario!='metric_assertion_optional_missing'}]
        elif scenario == 'metric_assertion_invalid':
            declaration = 'invalid-list'
        spec['model']['validation'] = {'acceptance_assertions':declaration}
    spec_path = tmp_path/'task_spec.json'
    spec_path.write_text(json.dumps(spec),encoding='utf-8')
    dataset = tmp_path/'actual-dataset'
    script = export_runner_script(spec_path,tmp_path/'downloaded.py',kind=kind,output_root=dataset)
    env = {**os.environ,'PYTHONUTF8':'1'}
    completed = subprocess.run([sys.executable,str(script.output_path)],cwd=tmp_path,
        env=env,text=True,encoding='utf-8',stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        timeout=180,check=False)
    (tmp_path/'actual.stdout.log').write_text(completed.stdout,encoding='utf-8')
    (tmp_path/'actual.stderr.log').write_text(completed.stderr,encoding='utf-8')
    payload = json.loads(completed.stdout)
    assert (dataset/'summary.json').is_file(), completed.stderr
    if expected == 'PASS':
        assert completed.returncode == 0 and payload['ok'] is True, payload
        if scenario == 'metric_assertion_optional_missing':
            checks = payload['validation_outcome']['checks']
            assert any(c['category']=='acceptance_assertion' and c['result']=='NOT_EVALUATED' for c in checks)
        return
    assert completed.returncode != 0, payload
    assert payload['ok'] is False, payload
    assert payload['validation_outcome']['result'] == expected, payload
    assert payload['validation_outcome']['execution_status'] == 'SUCCEEDED', payload
    if scenario == 'native_hot_payload':
        assert payload['summary']['execution_status'] == 'PASS', payload
        assert payload['summary']['mission_status'] == 'FAIL', payload
        assert 'THERMAL_LIMIT_EXCEEDED' in payload['summary']['mission_reason_codes']
