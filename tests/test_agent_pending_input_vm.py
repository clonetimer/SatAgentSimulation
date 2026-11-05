"""Real JavaScript control flow with DOM/network boundaries, not browser acceptance."""
import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.canonical_transport import task_spec_json
from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec


@pytest.fixture(scope='module')
def pending_input_fixture(tmp_path_factory):
    root = tmp_path_factory.mktemp('agent-pending-vm')
    default_form = capability_form_schema('component.thermal_node.v1')['default_form']
    result = {'default_form': default_form}
    app = create_app(runs_root=root/'runs', artifacts_root=root/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    with TestClient(app) as client:
        for name, duration in [('accepted', 60), ('updated', 120), ('edited', 90), ('rejected', 999)]:
            form = copy.deepcopy(default_form)
            form['simulation']['duration_s'] = duration
            spec = normalize_form_task_spec(form)
            result[name] = spec
            result[name+'_text'] = task_spec_json(spec)
            response = client.post('/forms/capabilities/component.thermal_node.v1/project', json={'task_spec':spec})
            assert response.status_code == 200, response.text
            result[name+'_form'] = response.json()['projection']['form_data']
    return result


@pytest.mark.parametrize('scenario', [
    'latest_pending', 'reject', 'reject_parse', 'transport_error', 'projection_error',
    'stale_generation', 'stale_projection', 'blank_rerun', 'manual_edit',
    'manual_invalid', 'preflight_stale', 'one_owner',
    'receipt_manual_change', 'receipt_json_precision_change', 'receipt_form_preview',
])
def test_pending_instruction_execution_and_accepted_state(scenario, pending_input_fixture):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for actual app-function VM evidence')
    script = Path(__file__).parent/'js'/'agent_pending_input_vm.js'
    result = subprocess.run([node, str(script), scenario], input=json.dumps(pending_input_fixture),
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof['status'] == 'PASS'
