"""Missing evidence is not a physical pass; long traces retain bounded findings."""
import json
import tracemalloc

import pytest

from sat_sim.execution_planner import plan_task_spec
from sat_sim.form_schema import capability_form_schema
from sat_sim.interactive.continuous_seal import _StreamingPhysicalChecks
from sat_sim.interactive.evidence import _validation, seal_interactive_workspace
from sat_sim.interactive.models import InteractiveSessionSpec, SessionState, TelemetryFrame
from sat_sim.interactive.workspace import SessionWorkspace
from sat_sim.unified_agent import normalize_form_task_spec
from sat_sim.validation.physical_checks import (
    MAX_PHYSICAL_ISSUE_SAMPLES, PhysicalValidationAccumulator,
    evaluate_physical_validation, physical_validation_options,
)
from sat_sim.validation_outcome import (
    ValidationResult, evaluate_validation_outcome, physical_validation_check,
)


def test_storage_capacity_bounds_do_not_substitute_for_a_conservation_residual():
    report = evaluate_physical_validation([
        {'eps.battery_soc': .6, 'data.storage_bits': 100., 'data.storage_capacity_bits': 100.},
    ])
    assert report['checks_run'] == ['soc_bounds']
    assert report['coverage_status'] == 'PARTIAL'
    assert report['check_results']['data_conservation']['status'] == 'NOT_EVALUATED'
    assert 'data_conservation' in report['checks_missing']
    check = physical_validation_check(report)
    assert check.reason_code == 'PHYSICAL_VALIDATION_PARTIAL'
    assert check.result is ValidationResult.PASS


@pytest.mark.parametrize('row, check_id', [
    ({'eps.battery_soc': 1.1}, 'soc_bounds'),
    ({'thermal.node.temp_k': 500.}, 'thermal_bounds'),
    ({'thermal.solar_panel_temp_k': 100.}, 'thermal_bounds'),
    ({'eps.solar_array_power_w': -1.}, 'energy_balance_proxy'),
    ({'coupled.data.conservation_error_bits': 1.}, 'data_conservation'),
])
def test_observed_native_aliases_and_residuals_catch_real_violations(row, check_id):
    report = evaluate_physical_validation([row], checks=[check_id])
    assert report['checks_run'] == [check_id]
    assert report['status'] == 'fail'
    assert report['fail_count'] == 1


def test_kelvin_only_valid_trace_has_measured_thermal_coverage():
    report = evaluate_physical_validation([{'thermal.node.temp_k': 295.}], checks=['thermal_bounds'])
    assert report['status'] == 'pass'
    assert report['check_results']['thermal_bounds']['fields_observed'] == ['thermal.node.temp_k']
    failed = evaluate_physical_validation([{'thermal.node.temp_k':500.}], checks=['thermal_bounds'])
    assert failed['issues'][0]['value'] == 500.
    assert failed['issues'][0]['limit'].endswith(' K')


def test_absent_default_inputs_are_not_a_physical_pass_but_do_not_fail_unrelated_components():
    report = evaluate_physical_validation([{'antenna.gain_dbi': 10.}])
    assert report['status'] == 'not_evaluated'
    assert report['checks_run'] == []
    assert physical_validation_check(report).result is ValidationResult.NOT_EVALUATED
    interactive = _validation(SessionState.COMPLETED, [{'antenna.gain_dbi': 10.}], [])
    assert interactive.result is ValidationResult.PASS
    assert interactive.checks[1].reason_code == 'PHYSICAL_VALIDATION_NOT_EVALUATED'


@pytest.mark.parametrize('strict, expected', [(False, 'inconclusive'), (True, 'fail')])
def test_explicit_conservation_requirement_without_residual_cannot_pass(strict, expected):
    options = {'checks':['data_conservation'], 'strict':strict}
    row = {'data.storage_bits': 50., 'data.storage_capacity_bits': 100.}
    report = evaluate_physical_validation([row], **options)
    assert report['status'] == expected
    assert report['checks_run'] == []
    assert report['check_results']['data_conservation']['status'] == 'NOT_EVALUATED'
    assert physical_validation_check(report).result is not ValidationResult.PASS


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), True, '0.7', None, 10**1000])
def test_invalid_physical_input_is_reported_instead_of_silently_skipped(invalid):
    report = evaluate_physical_validation([{'eps.battery_soc': invalid}])
    assert report['status'] == 'fail'
    assert report['fail_count'] == 1
    assert report['check_results']['soc_bounds']['invalid_sample_count'] == 1
    assert physical_validation_check(report).result is ValidationResult.FAIL
    json.dumps(report, allow_nan=False)


def test_empty_check_selection_cannot_bypass_strict_physical_validation():
    report = evaluate_physical_validation([{'eps.battery_soc': .5}], checks=[], strict=True)
    assert report['status'] == 'fail'
    assert report['checks_run'] == []
    assert report['fail_count'] == 1


def test_temporal_checks_need_multiple_samples_of_one_field_not_aliases_in_one_row():
    report = evaluate_physical_validation([{
        'propulsion.fuel_mass_kg': 10., 'propellant_remaining_kg': 2.,
        'adcs.pointing_error_deg': 1., 'attitude.pointing_error_deg': 1.,
    }], checks=['fuel_monotonicity', 'pointing_convergence'])
    assert report['checks_run'] == []
    assert report['status'] == 'inconclusive'
    # Independent aliases must not produce a spurious cross-field fuel increase.
    rows = [{'propulsion.fuel_mass_kg': 10., 'propellant_remaining_kg': 2.},
            {'propulsion.fuel_mass_kg': 9., 'propellant_remaining_kg': 1.}]
    assert evaluate_physical_validation(rows, checks=['fuel_monotonicity'])['status'] == 'pass'


def test_batch_and_stream_have_exact_bounded_findings_above_old_crash_limit():
    count = MAX_PHYSICAL_ISSUE_SAMPLES + 123

    def rows():
        for index in range(count):
            yield {'time_s': float(index), 'eps.battery_soc': .99}
        yield {'time_s': float(count), 'eps.battery_soc': -1.}

    stream = _StreamingPhysicalChecks(checks=['soc_bounds'])
    for row in rows():
        stream.add(row)
    report = stream.result()
    assert report == evaluate_physical_validation(rows(), checks=['soc_bounds'])
    assert report == stream.result()  # Asking for a report does not add findings.
    assert report['warning_count'] == count
    assert report['fail_count'] == 1
    assert report['issue_count'] == count + 1
    assert len(report['issues']) == MAX_PHYSICAL_ISSUE_SAMPLES
    assert report['issues_truncated'] is True
    assert report['omitted_issue_count'] == 124
    groups = {item['severity']:item for item in report['issue_counts']}
    assert groups['warning']['first_issue']['path'] == 'trace[0].eps.battery_soc'
    assert groups['warning']['last_issue']['path'] == f'trace[{count-1}].eps.battery_soc'
    assert groups['fail']['first_issue']['path'] == f'trace[{count}].eps.battery_soc'
    assert physical_validation_check(report).result is ValidationResult.FAIL


def test_streaming_validator_memory_does_not_grow_with_duplicate_findings():
    stream = PhysicalValidationAccumulator(checks=['soc_bounds'], max_issue_samples=8)
    tracemalloc.start()
    try:
        for _ in range(3000):
            stream.add({'eps.battery_soc': .99})
        warm_current, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        for _ in range(15000):
            stream.add({'eps.battery_soc': .99})
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert current - warm_current < 100_000
    assert peak - warm_current < 150_000
    report = stream.result()
    assert report['warning_count'] == 18000
    assert len(report['issues']) == 8
    assert len(report['issue_counts']) == 1


def test_canonical_explicit_requirements_propagate_to_offline_and_interactive_validation():
    spec = normalize_form_task_spec(capability_form_schema('component.thermal_node.v1')['default_form'])
    spec['model']['validation'] = {'physical_checks':['data_conservation']}
    planning = plan_task_spec(spec)
    assert planning.ok
    row = {'thermal.node.temp_k': 295., 'data.storage_bits': 50.}
    offline = evaluate_validation_outcome(spec=spec, resolved=planning.resolved_spec,
        summary={}, trace_rows=[row])
    assert offline.result is ValidationResult.INCONCLUSIVE
    assert next(check for check in offline.checks if check.category=='physical_validation').reason_code == 'PHYSICAL_VALIDATION_EVIDENCE_MISSING'
    interactive = _validation(SessionState.COMPLETED, [row], [], task_spec=spec)
    assert interactive.result is ValidationResult.INCONCLUSIVE
    assert interactive.physical_validation == evaluate_physical_validation([row], **physical_validation_options(spec))


def test_continuous_workspace_with_more_than_2048_warnings_seals_with_exact_counts(tmp_path):
    spec = InteractiveSessionSpec(session_id='many-warnings',
        capability_id='whole_spacecraft.unified_native.v1', task_spec={},
        continuous=True, quantum_s=1., paced=False, max_sim_time_s=5000.)
    workspace = SessionWorkspace(tmp_path, spec)
    workspace.append_state(SessionState.CREATED, 0, 0., 'CREATED')
    count = MAX_PHYSICAL_ISSUE_SAMPLES + 3
    for index in range(count):
        workspace.append_telemetry(TelemetryFrame(session_id=spec.session_id,
            stream='runtime.trace', sequence=index, sim_time_s=float(index),
            values={'time_s':float(index), 'eps.battery_soc':.99}))
    workspace.append_state(SessionState.COMPLETED, 1, float(count-1), 'COMPLETED')
    workspace.finalize_telemetry()
    assert seal_interactive_workspace(workspace.path)['ok']
    path = workspace.path/'run_bundle/validation/validation_outcome.json'
    report = json.loads(path.read_text(encoding='utf-8'))['physical_validation']
    assert report['warning_count'] == count
    assert report['fail_count'] == 0
    assert report['issues_truncated'] is True
    assert report['issue_count'] == count
    assert report['checks_run'] == ['soc_bounds']
    assert report['check_results']['data_conservation']['status'] == 'NOT_EVALUATED'


def test_continuous_seal_honors_explicit_missing_physical_evidence(tmp_path):
    spec = InteractiveSessionSpec(session_id='required-conservation',
        capability_id='whole_spacecraft.unified_native.v1',
        task_spec={'model':{'validation':{'physical_checks':['data_conservation']}}},
        continuous=True, quantum_s=1., paced=False, max_sim_time_s=10.)
    workspace = SessionWorkspace(tmp_path, spec)
    workspace.append_state(SessionState.CREATED, 0, 0., 'CREATED')
    workspace.append_telemetry(TelemetryFrame(session_id=spec.session_id,
        stream='runtime.trace', sequence=0, sim_time_s=0.,
        values={'time_s':0., 'eps.battery_soc':.6, 'data.storage_bits':100.}))
    workspace.append_state(SessionState.COMPLETED, 1, 0., 'COMPLETED')
    workspace.finalize_telemetry()
    with pytest.raises(ValueError, match='INTERACTIVE_VALIDATION_NOT_PASS:PHYSICAL_VALIDATION_EVIDENCE_MISSING'):
        seal_interactive_workspace(workspace.path)
    assert not (workspace.path/'run_bundle').exists()
    assert (workspace.path/'.run_bundle.pending/results/semantic_trace.json').exists()
    assert SessionWorkspace.inspect(workspace.path).valid


@pytest.mark.parametrize('flag',[0,False])
def test_declared_thermal_limit_failure_survives_recovery_and_bounded_samples(flag):
    rows=[{'thermal.safe':1,'thermal.payload_temp_k':300.},
          {'thermal.safe':flag,'thermal.payload_temp_k':341.},
          {'thermal.safe':1,'thermal.payload_temp_k':300.}]
    report=evaluate_physical_validation(rows,checks=['thermal_bounds'])
    streamed=PhysicalValidationAccumulator(checks=['thermal_bounds'],max_issue_samples=0)
    for row in rows:
        streamed.add(row)
    assert report['status']=='fail'
    assert report['fail_count']==1
    assert streamed.result()['status']=='fail'
    assert streamed.result()['fail_count']==1
    assert 'thermal.safe' in report['check_results']['thermal_bounds']['fields_observed']


@pytest.mark.parametrize('flag',[2,-1,'false',None,float('nan')])
def test_malformed_thermal_safety_flag_is_invalid_evidence(flag):
    report=evaluate_physical_validation([{'thermal.safe':flag}],checks=['thermal_bounds'])
    assert report['status']=='fail'
    assert report['check_results']['thermal_bounds']['invalid_sample_count']==1


@pytest.mark.parametrize('status,expected', [('FAIL',ValidationResult.FAIL),('INCONCLUSIVE',ValidationResult.INCONCLUSIVE)])
def test_interactive_runner_mission_result_cannot_be_overridden_by_safe_final_row(status,expected):
    report=_validation(SessionState.COMPLETED,[{'thermal.safe':1,'thermal.payload_temp_k':300.}],[],
        runtime_summary={'execution_status':'PASS','mission_status':status})
    assert report.result is expected
