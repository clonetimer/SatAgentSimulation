from copy import deepcopy
import importlib.util
import json

import pytest


def evaluate(snapshot, plan):
    assert importlib.util.find_spec('sat_sim.performance_evaluation') is not None, 'independent evaluator is missing'
    from sat_sim.performance_evaluation import evaluate_snapshot
    return evaluate_snapshot(snapshot, plan)


def sample_inputs():
    binding = {'owner_object_id': 'component.reaction_wheel:1', 'source_capability_id': 'component.reaction_wheel.v1', 'source_field': 'adcs.reaction_wheel.speed_rad_s_0', 'unit': 'rad/s'}
    snapshot = {'schema_version': 'recorded-snapshot.v1', 'run_id': 'run-1', 'snapshot_id': 'snapshot-1', 'sealed': True,
                'run_status': 'SUCCEEDED', 'series': {'speed': {**binding, 'times_s': [0., 1., 2.], 'values': [-1., 2., 3.]}}}
    plan = {'schema_version': 'evaluation-plan.v1', 'plan_id': 'wheel-review', 'version': '1', 'window_s': [0., 2.],
            'metrics': [{'metric_id': 'speed-rms', 'display_name_zh': '反作用轮 1 · 角速度均方根', 'series_id': 'speed',
                         **binding, 'formula': 'sample_rms.v1', 'minimum_samples': 3}]}
    return snapshot, plan


def test_evaluation_is_deterministic_read_only_and_has_no_implicit_pass_verdict():
    snapshot, plan = sample_inputs()
    original = deepcopy((snapshot, plan))
    report = evaluate(snapshot, plan)
    assert report == evaluate(snapshot, plan)
    assert (snapshot, plan) == original
    assert report['schema_version'] == 'evaluation-report.v1'
    assert report['source_run_id'] == 'run-1'
    assert report['source_snapshot_id'] == 'snapshot-1'
    assert report['source_run_status'] == 'SUCCEEDED'
    assert len(report['source_digest']) == len(report['plan_digest']) == 64
    assert report['metrics'][0]['value'] == pytest.approx((14 / 3) ** .5)
    assert report['metrics'][0]['verdict'] == 'NOT_REQUESTED'
    assert report['metrics'][0]['status'] == 'COMPUTED'
    json.dumps(report, allow_nan=False)


def test_changing_thresholds_reevaluates_same_data_without_changing_the_run():
    snapshot, plan = sample_inputs()
    first = evaluate(snapshot, plan)
    plan['metrics'][0]['limits'] = {'upper': 1.0}
    plan['version'] = '2'
    second = evaluate(snapshot, plan)
    assert first['source_digest'] == second['source_digest']
    assert first['plan_digest'] != second['plan_digest']
    assert first['report_id'] != second['report_id']
    assert second['metrics'][0]['verdict'] == 'OUTSIDE_LIMITS'
    assert snapshot['run_status'] == 'SUCCEEDED'


@pytest.mark.parametrize(('formula', 'value'), [('sample_mean.v1', 4 / 3), ('max_abs.v1', 3.), ('final.v1', 3.)])
def test_registered_statistic_formulas(formula, value):
    snapshot, plan = sample_inputs()
    plan['metrics'][0]['formula'] = formula
    assert evaluate(snapshot, plan)['metrics'][0]['value'] == pytest.approx(value)


@pytest.mark.parametrize(('key', 'wrong'), [('unit', 'rpm'), ('owner_object_id', 'component.reaction_wheel:2'), ('source_capability_id', 'other'), ('source_field', 'other.field')])
def test_source_identity_mismatch_is_not_a_zero_or_a_pass(key, wrong):
    snapshot, plan = sample_inputs()
    plan['metrics'][0][key] = wrong
    metric = evaluate(snapshot, plan)['metrics'][0]
    assert metric['status'] == 'NOT_EVALUATED'
    assert metric['reason_code'] == 'SOURCE_BINDING_MISMATCH'
    assert metric['value'] is None
    assert metric['verdict'] == 'NOT_EVALUATED'


def test_missing_output_does_not_rerun_or_fabricate_results():
    snapshot, plan = sample_inputs()
    snapshot['series'] = {}
    report = evaluate(snapshot, plan)
    assert report['status'] == 'NOT_EVALUATED'
    assert report['metrics'][0]['reason_code'] == 'SOURCE_NOT_RECORDED'
    assert snapshot['series'] == {}


@pytest.mark.parametrize(('times', 'values', 'reason'), [([], [], 'NO_SAMPLES'), ([0, 0, 2], [1, 2, 3], 'INVALID_TIME_AXIS'), ([0, 1], [1, 2], 'INCOMPLETE_WINDOW'), ([0, 1, 2], [1, 'bad', 3], 'INVALID_SERIES'), ([0, 1, 2], [1, True, 3], 'INVALID_SERIES')])
def test_incomplete_or_invalid_series_is_reported(times, values, reason):
    snapshot, plan = sample_inputs()
    snapshot['series']['speed'].update(times_s=times, values=values)
    result = evaluate(snapshot, plan)['metrics'][0]
    assert result['reason_code'] == reason
    assert result['value'] is None


def test_requested_window_uses_recorded_samples_only():
    snapshot, plan = sample_inputs()
    plan['window_s'] = [.2, .4]
    assert evaluate(snapshot, plan)['metrics'][0]['reason_code'] == 'NO_SAMPLES'


def test_sampling_gap_rule_is_explicit():
    snapshot, plan = sample_inputs()
    plan['max_sample_gap_s'] = .5
    assert evaluate(snapshot, plan)['metrics'][0]['reason_code'] == 'SAMPLING_GAP'


def test_partial_report_does_not_claim_all_metrics_computed():
    snapshot, plan = sample_inputs()
    missing = deepcopy(plan['metrics'][0]); missing.update(metric_id='unrecorded', series_id='missing')
    plan['metrics'].append(missing)
    assert evaluate(snapshot, plan)['status'] == 'PARTIAL'


@pytest.mark.parametrize('bad', ['duplicate', 'formula', 'window', 'version', 'empty', 'threshold', 'minimum_samples', 'unsealed'])
def test_invalid_plans_and_unsealed_inputs_fail_without_mutating_data(bad):
    snapshot, plan = sample_inputs()
    if bad == 'duplicate': plan['metrics'].append(deepcopy(plan['metrics'][0]))
    if bad == 'formula': plan['metrics'][0]['formula'] = '__import__("os")'
    if bad == 'window': plan['window_s'] = [2, 0]
    if bad == 'version': plan['schema_version'] = 'future'
    if bad == 'empty': plan['metrics'] = []
    if bad == 'threshold': plan['metrics'][0]['limits'] = {'lower': 9, 'upper': 1}
    if bad == 'minimum_samples': plan['metrics'][0]['minimum_samples'] = True
    if bad == 'unsealed': snapshot['sealed'] = False
    before = deepcopy((snapshot, plan))
    with pytest.raises(ValueError): evaluate(snapshot, plan)
    assert before == (snapshot, plan)


def test_nonfinite_snapshot_is_rejected_not_sanitized_into_a_valid_measurement():
    snapshot, plan = sample_inputs()
    snapshot['series']['speed']['values'][1] = float('nan')
    with pytest.raises(ValueError): evaluate(snapshot, plan)


def test_nonstring_formula_is_rejected_with_a_validation_error():
    snapshot, plan = sample_inputs()
    plan['metrics'][0]['formula'] = []
    with pytest.raises(ValueError, match='FORMULA'):
        evaluate(snapshot, plan)
