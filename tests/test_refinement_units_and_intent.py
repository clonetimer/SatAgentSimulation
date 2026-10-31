"""Deterministic refinement contracts, not model inference or LLM accuracy.

Baselines come from the public form schema. No archived prompt, frozen answer,
local service, native runtime, or report artifact is needed by these tests.
"""
from copy import deepcopy

import pytest

from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
from sat_sim.unified_agent import (
    _merge_natural_language_refinement,
    _refinement_requests_parameter,
    normalize_form_task_spec,
)


CAPABILITY = 'whole_spacecraft.unified_native.v1'


def _base(*, step=.25, sample=1., conductance=3.25):
    form = deepcopy(capability_form_schema(CAPABILITY)['default_form'])
    form['simulation'].update(duration_s=120., step_s=step, sample_s=sample)
    form['parameters']['values']['solar_panel_structure_conductance_w_k'] = conductance
    base = normalize_form_task_spec(form)
    base['simulation']['solver'] = {
        **(base['simulation'].get('solver') or {}), 'step_s': step,
    }
    return canonicalize_task_spec(base)


def _candidate_step(base, step):
    candidate = deepcopy(base)
    candidate['simulation']['step_s'] = step
    candidate['simulation'].setdefault('solver', {})['step_s'] = step
    return candidate


@pytest.mark.parametrize('literal, seconds', [
    ('20 ms', .02),
    ('125毫秒', .125),
    ('2e-1秒', .2),
    ('5E-2 s', .05),
    ('.125秒', .125),
    ('+0.125秒', .125),
])
def test_explicit_step_quantity_keeps_its_full_number_and_time_dimension(literal, seconds):
    base = _base()
    candidate = _candidate_step(base, seconds)
    merged, _ = _merge_natural_language_refinement(
        base, candidate, f'把积分步长改为{literal}，其他参数保持不变。')
    assert merged['simulation']['step_s'] == pytest.approx(seconds)
    assert merged['simulation']['solver']['step_s'] == pytest.approx(seconds)
    assert to_runtime_task_spec(merged)['simulation']['solver']['step_s'] == pytest.approx(seconds)
    assert merged['simulation']['sample_s'] == base['simulation']['sample_s']
    assert merged['parameters'] == base['parameters']


@pytest.mark.parametrize('literal', ['-0.1秒', '-2e-1秒', '1e999秒'])
def test_invalid_explicit_step_is_rejected_instead_of_becoming_positive_or_partial(literal):
    base = _base()
    with pytest.raises(ValueError):
        _merge_natural_language_refinement(base, deepcopy(base), f'把积分步长改为{literal}。')


@pytest.mark.parametrize('literal', ['3 W/K', '7 Wh', '9%', '0.2 s^-1'])
def test_explicit_step_cannot_take_a_non_time_quantity(literal):
    base = _base()
    with pytest.raises(ValueError):
        _merge_natural_language_refinement(base, deepcopy(base), f'把积分步长改为{literal}。')


@pytest.mark.parametrize('step, conductance', [(.125, 1.25), (.25, 3.), (.5, 9.5)])
def test_preserved_number_before_time_labels_does_not_capture_next_conductance(step, conductance):
    base = _base(step=step, sample=step, conductance=conductance)
    before = deepcopy(base)
    candidate = _candidate_step(base, step/2.)
    candidate['parameters']['values']['solar_panel_structure_conductance_w_k'] = conductance + .5
    merged, changed = _merge_natural_language_refinement(
        base, candidate,
        f'保留原有{step:g}秒步长和记录周期、{conductance:g} W/K导热系数，以及其他参数和输出。')
    assert merged['simulation'] == before['simulation']
    assert merged['parameters'] == before['parameters']
    assert merged['outputs'] == before['outputs']
    assert base == before, 'Refinement must not mutate the caller-owned base.'
    assert not any(path.startswith('simulation.') or path.startswith('parameters.values.') for path in changed)


def test_negated_step_change_does_not_apply_the_named_number_or_model_drift():
    base = _base()
    candidate = _candidate_step(base, .1)
    merged, changed = _merge_natural_language_refinement(
        base, candidate, '不要把步长改为3秒；其余配置不变。')
    assert merged['simulation'] == base['simulation']
    assert not any(path.startswith('simulation.') for path in changed)


def test_preserving_one_time_property_does_not_block_another_explicit_change():
    base = _base(step=.2)
    candidate = _candidate_step(base, .1)
    candidate['simulation']['sample_s'] = .4
    merged, changed = _merge_natural_language_refinement(
        base, candidate, '保持步长不变；将记录周期改为0.4秒。')
    assert merged['simulation']['step_s'] == .2
    assert merged['simulation']['solver']['step_s'] == .2
    assert merged['simulation']['sample_s'] == .4
    assert 'simulation.sample_s' in changed
    assert 'simulation.step_s' not in changed


@pytest.mark.parametrize('literal, seconds', [('1.5分钟', 90.), ('.05小时', 180.)])
def test_duration_time_unit_conversion_does_not_rebind_sample_or_step(literal, seconds):
    base = _base()
    candidate = deepcopy(base)
    candidate['simulation']['duration_s'] = seconds
    merged, _ = _merge_natural_language_refinement(
        base, candidate, f'把仿真时长改为{literal}；保持步长和记录周期不变。')
    assert merged['simulation']['duration_s'] == pytest.approx(seconds)
    assert merged['simulation']['step_s'] == base['simulation']['step_s']
    assert merged['simulation']['sample_s'] == base['simulation']['sample_s']


def test_step_patch_updates_both_canonical_and_executed_solver_and_records_real_changes():
    base = _base()
    candidate = _candidate_step(base, .1)
    merged, changed = _merge_natural_language_refinement(base, candidate, '把积分步长改为0.1秒。')
    assert merged['simulation']['step_s'] == merged['simulation']['solver']['step_s'] == .1
    assert to_runtime_task_spec(merged)['simulation']['solver']['step_s'] == .1
    assert {'simulation.step_s', 'simulation.solver.step_s'} <= set(changed)


@pytest.mark.parametrize('instruction', [
    '把电池容量损失从11%改为37%。',
    '把电池容量损失设为0.29。',
    'Set battery capacity loss to 36%.',
    '保持电池容量不变；把电池容量损失改为23%。',
])
def test_dimensionless_battery_loss_does_not_authorize_nominal_capacity_wh(instruction):
    assert not _refinement_requests_parameter('battery_capacity_wh', instruction, CAPABILITY)


@pytest.mark.parametrize('instruction, capacity_wh', [
    ('把电池容量改为208 Wh。', 208.),
    ('Set battery_capacity_wh=192.', 192.),
    ('把标称电池容量设置为225 Wh。', 225.),
])
def test_explicit_nominal_capacity_change_remains_supported(instruction, capacity_wh):
    base = _base()
    candidate = deepcopy(base)
    candidate['parameters']['values']['battery_capacity_wh'] = capacity_wh
    assert _refinement_requests_parameter('battery_capacity_wh', instruction, CAPABILITY)
    merged, changed = _merge_natural_language_refinement(base, candidate, instruction)
    assert merged['parameters']['values']['battery_capacity_wh'] == capacity_wh
    assert 'parameters.values.battery_capacity_wh' in changed
    for key, value in base['parameters']['values'].items():
        if key != 'battery_capacity_wh':
            assert merged['parameters']['values'][key] == value


def test_loss_refinement_preserves_nominal_wh_even_when_the_candidate_drifts():
    base = _base()
    base['events']['faults'] = [{
        'id':'battery_capacity_loss', 'target':'whole_spacecraft',
        'effect':'eps_battery_capacity_loss', 'start_s':8., 'magnitude':.12,
        'implementation':'auto', 'delivery':'modifier', 'parameters':{}, 'event_type':'fault',
    }]
    base['model']['target']['mode'] = 'fault'
    base = canonicalize_task_spec(base)
    before = deepcopy(base)
    candidate = deepcopy(base)
    candidate['events']['faults'][0]['magnitude'] = .31
    candidate['parameters']['values']['battery_capacity_wh'] = float(base['parameters']['values']['battery_capacity_wh']) + 25.
    merged, changed = _merge_natural_language_refinement(
        base, candidate,
        '把电池容量损失从12%改为31%，保留原有标称容量、故障时刻，以及其他参数和输出。')
    assert merged['parameters'] == before['parameters']
    assert merged['simulation'] == before['simulation']
    assert merged['outputs'] == before['outputs']
    assert len(merged['events']['faults']) == 1
    assert merged['events']['faults'][0]['magnitude'] == .31
    assert merged['events']['faults'][0]['start_s'] == 8.
    assert 'parameters.values.battery_capacity_wh' not in changed
    assert base == before
