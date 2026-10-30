"""Actual production runs must honor metric-assertion failures."""
import pytest

from sat_sim.form_schema import capability_form_schema
from sat_sim.run_bundle import execute_prepared_run, prepare_run
from sat_sim.unified_agent import normalize_form_task_spec
from sat_sim.validation_outcome import runner_summary_validation_checks


def test_real_completed_node_with_failed_acceptance_metric_cannot_validate_pass(tmp_path):
    spec = normalize_form_task_spec(capability_form_schema('component.thermal_node.v1')['default_form'])
    spec['model']['validation'] = {'acceptance_assertions':[
        {'id':'impossible_status','metric':'status','operator':'==','value':'IMPOSSIBLE'}]}
    prepared = prepare_run(spec,output_root=tmp_path/'runs')
    result = execute_prepared_run(prepared.bundle_root,expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.execution_status.value == 'SUCCEEDED'
    assert result.summary['acceptance_assertion_status'] == 'FAIL'
    assert result.validation.result.value == 'FAIL'
    assert any(check.reason_code == 'ACCEPTANCE_ASSERTION_FAILED' for check in result.validation.checks)
    assert len(result.trace_rows)>1


@pytest.mark.parametrize('status,expected',[('FAIL','FAIL'),('INCONCLUSIVE','INCONCLUSIVE')])
def test_recorded_acceptance_failure_cannot_be_overridden_by_complete_status(status,expected):
    checks = runner_summary_validation_checks({'status':'complete','acceptance_assertion_status':status})
    assert any(check.check_id=='runner_acceptance_assertions' and check.result.value==expected for check in checks)
