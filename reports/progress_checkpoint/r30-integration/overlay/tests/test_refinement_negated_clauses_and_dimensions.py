"""Deterministic refinement guards; these are not model-accuracy measurements."""
from copy import deepcopy

import pytest

from sat_sim.agent_facade import _request_explicitly_sets
from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
from sat_sim.unified_agent import (
    _merge_natural_language_refinement,
    _refinement_requests_parameter,
    normalize_form_task_spec,
)


CAPABILITY = 'whole_spacecraft.unified_native.v1'


def _base():
    form = deepcopy(capability_form_schema(CAPABILITY)['default_form'])
    form['simulation'].update(duration_s=120., step_s=.25, sample_s=1.)
    base = normalize_form_task_spec(form)
    base['simulation']['solver'] = {
        **(base['simulation'].get('solver') or {}), 'step_s': .25,
    }
    return canonicalize_task_spec(base)


@pytest.mark.parametrize('name, candidate_value, instruction', [
    ('payload_power_w', 12., '不要把载荷功率改为12 W，其他配置不变。'),
    ('payload_power_w', 12., 'Do not change payload power to 12 W.'),
    ('initial_soc', .4, '不要将初始SOC改为0.4，其他配置不变。'),
    ('initial_soc', .4, "Don't change initial SOC to 0.4."),
])
def test_negated_physical_parameter_change_never_authorizes_model_drift(name, candidate_value, instruction):
    base = _base()
    before = deepcopy(base)
    candidate = deepcopy(base)
    assert candidate_value != base['parameters']['values'][name]
    candidate['parameters']['values'][name] = candidate_value
    assert not _refinement_requests_parameter(name, instruction, CAPABILITY)
    merged, changed = _merge_natural_language_refinement(base, candidate, instruction)
    assert merged['parameters'] == before['parameters']
    assert merged['simulation'] == before['simulation']
    assert f'parameters.values.{name}' not in changed
    assert base == before


@pytest.mark.parametrize('instruction', [
    'Set step to 0.1 s and keep the sample interval unchanged.',
    'Keep sample interval unchanged and set step to 0.1 s.',
    'Set step to 0.1 s and do not change duration.',
    'Do not change duration and set step to 0.1 s.',
])
def test_and_scopes_keep_and_negation_to_their_own_time_field(instruction):
    base = _base()
    candidate = deepcopy(base)
    candidate['simulation'].update(step_s=.1, sample_s=.4, duration_s=60.)
    candidate['simulation']['solver']['step_s'] = .1
    merged, changed = _merge_natural_language_refinement(base, candidate, instruction)
    assert merged['simulation']['step_s'] == .1
    assert merged['simulation']['solver']['step_s'] == .1
    assert to_runtime_task_spec(merged)['simulation']['solver']['step_s'] == .1
    assert merged['simulation']['duration_s'] == base['simulation']['duration_s']
    assert merged['simulation']['sample_s'] == base['simulation']['sample_s']
    assert merged['parameters'] == base['parameters']
    assert {'simulation.step_s', 'simulation.solver.step_s'} <= set(changed)
    assert 'simulation.duration_s' not in changed
    assert 'simulation.sample_s' not in changed


@pytest.mark.parametrize('instruction', [
    '把电池容量改为208 Wh/s。',
    '把电池容量改为208 Wh^-1。',
    'Set battery capacity to 208 Wh/s.',
    'Set battery capacity to 208 Wh^-1.',
])
def test_energy_unit_prefix_cannot_authorize_an_incompatible_capacity_dimension(instruction):
    base = _base()
    candidate = deepcopy(base)
    candidate['parameters']['values']['battery_capacity_wh'] = 208.
    assert not _refinement_requests_parameter('battery_capacity_wh', instruction, CAPABILITY)
    assert not _request_explicitly_sets('parameters.values.battery_capacity_wh', instruction)
    merged, changed = _merge_natural_language_refinement(base, candidate, instruction)
    assert merged['parameters'] == base['parameters']
    assert 'parameters.values.battery_capacity_wh' not in changed


@pytest.mark.parametrize('instruction, capacity_wh', [
    ('把电池容量改为208 Wh。', 208.),
    ('Set battery capacity to 0.208 kWh.', 208.),
    ('Set battery_capacity_wh=208.', 208.),
])
def test_complete_energy_units_and_explicit_wh_identifier_remain_supported(instruction, capacity_wh):
    base = _base()
    candidate = deepcopy(base)
    candidate['parameters']['values']['battery_capacity_wh'] = capacity_wh
    assert _refinement_requests_parameter('battery_capacity_wh', instruction, CAPABILITY)
    assert _request_explicitly_sets('parameters.values.battery_capacity_wh', instruction)
    merged, changed = _merge_natural_language_refinement(base, candidate, instruction)
    assert merged['parameters']['values']['battery_capacity_wh'] == capacity_wh
    assert 'parameters.values.battery_capacity_wh' in changed
    assert merged['simulation'] == base['simulation']
    for name, value in base['parameters']['values'].items():
        if name != 'battery_capacity_wh':
            assert merged['parameters']['values'][name] == value
