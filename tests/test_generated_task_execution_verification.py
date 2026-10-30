"""Generated-script acceptance must include real production validation."""
import json

from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec
from sat_sim_ml.evaluation import _finite, _observed_physical_checks_passed, verify_task_execution
from sat_sim.validation.physical_checks import evaluate_physical_validation


def _thermal_task():
    return normalize_form_task_spec(capability_form_schema('component.thermal_node.v1')['default_form'])


def test_real_generated_task_has_bound_sealed_execution_evidence(tmp_path):
    proof = verify_task_execution(_thermal_task(), tmp_path)
    assert proof['passed'], proof['validation']
    assert proof['trace_row_count'] > 1
    assert all(proof['checks'].values())
    assert proof['can_claim_flight_validity'] is False
    assert proof['physical_coverage_status'] == 'PARTIAL'
    assert 'data_conservation' in proof['unassessed_physical_checks']
    saved = json.loads((tmp_path/'runtime-verification.json').read_text(encoding='utf-8'))
    assert saved == proof


def test_successful_runtime_does_not_hide_failed_physical_requirement(tmp_path):
    spec = _thermal_task()
    spec.setdefault('model', {})['validation'] = {
        'assertions':[{'id':'impossible_temperature', 'field':'thermal.node.temp_k', 'operator':'<', 'value':0.}]
    }
    proof = verify_task_execution(spec, tmp_path)
    assert proof['checks']['runtime_execution_succeeded'] is True
    assert proof['checks']['runtime_validation_passed'] is False
    assert proof['passed'] is False
    assert any(check['reason_code'] == 'MISSION_REQUIREMENT_FAILED' for check in proof['validation']['checks'])


def test_nonfinite_numeric_telemetry_is_rejected():
    assert _finite([{'valid':1., 'nested':{'invalid':float('nan')}}]) is False
    assert _finite({'value':float('inf')}) is False


def test_missing_required_conservation_cannot_qualify_model_task(tmp_path):
    spec = _thermal_task()
    spec.setdefault('model', {})['validation'] = {'physical_checks':['data_conservation']}
    proof = verify_task_execution(spec, tmp_path)
    assert proof['checks']['runtime_execution_succeeded'] is True
    assert proof['checks']['runtime_physical_checks_passed'] is False
    assert proof['validation']['result'] == 'INCONCLUSIVE'
    assert proof['passed'] is False
    assert proof['unassessed_physical_checks'] == ['data_conservation']


def test_no_observed_checks_or_actual_warning_cannot_qualify():
    assert _observed_physical_checks_passed(evaluate_physical_validation([{'unrelated':1.}])) is False
    # The same optional missing checks must not conceal an actual SOC warning.
    warning = evaluate_physical_validation([{'eps.battery_soc':1.}])
    assert warning['warning_count'] > 0
    assert _observed_physical_checks_passed(warning) is False
    healthy = evaluate_physical_validation([{'eps.battery_soc':.5}])
    assert healthy['checks_missing']
    assert _observed_physical_checks_passed(healthy) is True
