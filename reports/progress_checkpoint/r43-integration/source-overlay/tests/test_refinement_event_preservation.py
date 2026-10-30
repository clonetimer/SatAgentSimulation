"""Local event-edit contracts from public forms, without model or native runs."""
from copy import deepcopy

import pytest

from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
from sat_sim.unified_agent import _merge_natural_language_refinement, normalize_form_task_spec


CAPABILITY = 'whole_spacecraft.unified_native.v1'
COMPOSITE_CAPABILITY = 'whole_spacecraft.composite_digital_twin.v1'


def _single_fault_base(capability=CAPABILITY):
    form = deepcopy(capability_form_schema(capability)['default_form'])
    form['simulation'].update(duration_s=120., step_s=.25, sample_s=1.)
    base = normalize_form_task_spec(form)
    base['simulation']['solver'] = {
        **(base['simulation'].get('solver') or {}), 'step_s': .25,
    }
    base['events'] = {
        **base['events'],
        'faults': [{
            'id': 'capacity_loss_case', 'target': 'whole_spacecraft',
            'effect': 'eps_battery_capacity_loss', 'start_s': 7.5, 'magnitude': .14,
            'implementation': 'auto', 'delivery': 'modifier', 'parameters': {},
            'event_type': 'fault',
        }],
        'degradations': [], 'constraints': [],
    }
    base['model']['target']['mode'] = 'fault'
    return canonicalize_task_spec(base)


def _assert_same_event_except(base, merged, **updates):
    expected = deepcopy(base['events'])
    expected['faults'][0].update(updates)
    assert merged['events'] == expected
    assert merged['parameters'] == base['parameters']
    assert merged['simulation'] == base['simulation']
    assert merged['outputs'] == base['outputs']


@pytest.mark.parametrize('instruction, subsystem_step', [
    ('把热仿真步长改为0.5秒。', .5),
    ('Set thermal step to 0.5 s.', .5),
    ('Set thermal_step_s=0.75.', .75),
])
def test_registered_subsystem_step_changes_only_its_parameter(instruction, subsystem_step):
    base = _single_fault_base(COMPOSITE_CAPABILITY)
    candidate = deepcopy(base)
    assert 'thermal_step_s' in candidate['parameters']['values']
    candidate['parameters']['values']['thermal_step_s'] = subsystem_step
    merged, changed = _merge_natural_language_refinement(base, candidate, instruction)
    assert merged['parameters']['values']['thermal_step_s'] == subsystem_step
    assert merged['simulation'] == base['simulation']
    assert to_runtime_task_spec(merged)['simulation']['solver']['step_s'] == .25
    assert merged['events'] == base['events']
    assert 'parameters.values.thermal_step_s' in changed
    assert not any(path.startswith('simulation.') for path in changed)
    for name, value in base['parameters']['values'].items():
        if name != 'thermal_step_s':
            assert merged['parameters']['values'][name] == value


@pytest.mark.parametrize('instruction', [
    'Set orbit environment step to 0.5 s.',
    '把轨道环境步长改为0.5秒。',
])
def test_subsystem_period_label_is_never_a_global_integrator_assignment(instruction):
    base = _single_fault_base()
    merged, changed = _merge_natural_language_refinement(base, deepcopy(base), instruction)
    assert merged['simulation'] == base['simulation']
    assert merged['events'] == base['events']
    assert not any(path.startswith('simulation.') for path in changed)


@pytest.mark.parametrize('instruction', [
    '保留原有故障和参数，把仿真时长改为90秒。',
    'Keep all existing faults and parameters; set duration to 90 s.',
])
def test_keep_events_rejects_candidate_magnitude_and_onset_drift(instruction):
    base = _single_fault_base()
    before = deepcopy(base)
    candidate = deepcopy(base)
    candidate['events']['faults'][0].update(magnitude=.37, start_s=19.)
    candidate['simulation']['duration_s'] = 90.
    merged, changed = _merge_natural_language_refinement(base, candidate, instruction)
    assert merged['events'] == before['events']
    assert merged['parameters'] == before['parameters']
    assert merged['outputs'] == before['outputs']
    assert merged['simulation']['duration_s'] == 90.
    assert merged['simulation']['step_s'] == before['simulation']['step_s']
    assert merged['simulation']['solver'] == before['simulation']['solver']
    assert 'simulation.duration_s' in changed
    assert not any(path == 'events' or path.startswith('events.') for path in changed)
    assert base == before


@pytest.mark.parametrize('percent', [23, 46])
def test_standalone_chinese_capacity_loss_instruction_applies_the_correct_candidate(percent):
    base = _single_fault_base()
    candidate = deepcopy(base)
    candidate['events']['faults'][0]['magnitude'] = percent / 100.
    merged, changed = _merge_natural_language_refinement(
        base, candidate, f'把电池容量损失从14%改为{percent}%。')
    _assert_same_event_except(base, merged, magnitude=percent / 100.)
    assert any(path == 'events' or path.startswith('events.') for path in changed)


@pytest.mark.parametrize('percent, unrequested_start', [(29, 19.), (46, 23.5)])
def test_magnitude_edit_preserves_onset_without_requiring_an_explicit_keep_clause(percent, unrequested_start):
    base = _single_fault_base()
    before = deepcopy(base)
    candidate = deepcopy(base)
    candidate['events']['faults'][0].update(magnitude=percent / 100., start_s=unrequested_start)
    merged, _ = _merge_natural_language_refinement(
        base, candidate, f'把电池容量损失从14%改为{percent}%。')
    _assert_same_event_except(base, merged, magnitude=percent / 100.)
    assert merged['events']['faults'][0]['start_s'] == 7.5
    assert base == before


@pytest.mark.parametrize('instruction, onset', [
    ('将故障发生时刻改为19秒。', 19.),
    ('把电池容量损失故障的发生时刻改为23.5秒。', 23.5),
])
def test_onset_edit_preserves_magnitude_without_requiring_an_explicit_keep_clause(instruction, onset):
    base = _single_fault_base()
    before = deepcopy(base)
    candidate = deepcopy(base)
    candidate['events']['faults'][0].update(start_s=onset, magnitude=.37)
    merged, changed = _merge_natural_language_refinement(base, candidate, instruction)
    _assert_same_event_except(base, merged, start_s=onset)
    assert merged['events']['faults'][0]['magnitude'] == .14
    assert any(path == 'events' or path.startswith('events.') for path in changed)
    assert base == before
