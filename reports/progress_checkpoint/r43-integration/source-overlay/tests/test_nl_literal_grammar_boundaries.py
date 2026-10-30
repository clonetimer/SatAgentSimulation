"""Literal grammar regressions; no model accuracy or native execution claims."""
from copy import deepcopy

import pytest

from sat_sim.agent_facade import _request_explicitly_sets
from sat_sim.capability_agent import _explicit_time_requirements
from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.unified_agent import (
    _merge_natural_language_refinement,
    _refinement_requests_parameter,
    normalize_form_task_spec,
)

CID = 'whole_spacecraft.unified_native.v1'


def base_spec():
    form = deepcopy(capability_form_schema(CID)['default_form'])
    form['simulation'].update(duration_s=120., step_s=.01, sample_s=.01)
    spec = normalize_form_task_spec(form)
    spec['simulation']['solver'] = {**(spec['simulation'].get('solver') or {}), 'step_s': .01}
    return canonicalize_task_spec(spec)


@pytest.mark.parametrize('text, seconds', [
    ('运行20秒，故障在5秒发生', 20.),
    ('仿真2e1秒', 20.),
    ('总时长+2e1秒', 20.),
    ('运行20ms', .02),
    ('Create a 30-second internal engineering simulation', 30.),
    ('Create a 2e-1-second native simulation; fault at 5 seconds.', .2),
])
def test_complete_duration_literal_reaches_request_checks_and_merge(text, seconds):
    assert _explicit_time_requirements(text) == {'duration_s': seconds}
    base = base_spec()
    merged, _ = _merge_natural_language_refinement(base, deepcopy(base), text)
    assert merged['simulation']['duration_s'] == pytest.approx(seconds)
    assert merged['simulation']['sample_s'] == base['simulation']['sample_s']
    assert merged['parameters'] == base['parameters']


@pytest.mark.parametrize('text, duration, sample', [
    ('运行1小时，每5秒采样', 3600., 5.),
    ('运行20秒，每2e1毫秒采样', 20., .02),
    ('运行20秒，每+2e-2秒记录', 20., .02),
])
def test_every_quantity_sampling_has_a_complete_time_unit(text, duration, sample):
    assert _explicit_time_requirements(text) == {'duration_s': duration, 'sample_s': sample}
    base = base_spec()
    merged, _ = _merge_natural_language_refinement(base, deepcopy(base), text)
    assert merged['simulation']['duration_s'] == duration
    assert merged['simulation']['sample_s'] == sample
    assert merged['simulation']['step_s'] == merged['simulation']['solver']['step_s'] == .01


@pytest.mark.parametrize('text', [
    '运行-20秒',
    '运行1e999秒',
    '每3W/K采样',
    'Create a 30-Wh engineering simulation',
    '运行20秒，仿真时长30秒',
])
def test_adjacent_and_attributive_grammar_retains_dimension_and_conflict_rejection(text):
    with pytest.raises(ValueError):
        _explicit_time_requirements(text)
    base = base_spec()
    with pytest.raises(ValueError):
        _merge_natural_language_refinement(base, deepcopy(base), text)


@pytest.mark.parametrize('text', [
    '只修改故障，使其持续20秒',
    '故障持续时间20秒',
    'A 5-second fault during the simulation',
])
def test_event_duration_is_not_a_mission_duration(text):
    assert _explicit_time_requirements(text) == {}
    base = base_spec()
    merged, changed = _merge_natural_language_refinement(base, deepcopy(base), text)
    assert merged['simulation'] == base['simulation']
    assert not any(path.startswith('simulation.') for path in changed)


@pytest.mark.parametrize('text', ['duration_s20=30', 'step_s2=0.1'])
def test_ascii_identifiers_cannot_be_partially_bound(text):
    assert _explicit_time_requirements(text) == {}
    base = base_spec()
    merged, _ = _merge_natural_language_refinement(base, deepcopy(base), text)
    assert merged['simulation'] == base['simulation']


@pytest.mark.parametrize('text', ['电池容量200Wh', '标称容量2e2 Wh', '电池标称容量0.2 kWh'])
def test_adjacent_nominal_capacity_keeps_explicit_provenance(text):
    assert _request_explicitly_sets('parameters.values.battery_capacity_wh', text)


@pytest.mark.parametrize('text', ['设置电池容量200Wh', '设置电池标称容量0.2kWh'])
def test_adjacent_nominal_capacity_setter_uses_the_same_typed_target(text):
    base = base_spec()
    candidate = deepcopy(base)
    candidate['parameters']['values']['battery_capacity_wh'] = 200.
    assert _refinement_requests_parameter('battery_capacity_wh', text, CID)
    merged, changed = _merge_natural_language_refinement(base, candidate, text)
    assert merged['parameters']['values']['battery_capacity_wh'] == 200.
    assert 'parameters.values.battery_capacity_wh' in changed
    assert merged['simulation'] == base['simulation']


@pytest.mark.parametrize('text', [
    '电池容量损失20%',
    '设置电池容量损失20%',
    '电池容量200Wh/s',
    '电池容量200Wh^-1',
    '不要设置电池容量200Wh',
    'battery_capacity_wh200=200',
])
def test_adjacent_capacity_grammar_retains_target_unit_negation_and_identifier_guards(text):
    assert not _request_explicitly_sets('parameters.values.battery_capacity_wh', text)
    assert not _refinement_requests_parameter('battery_capacity_wh', text, CID)


@pytest.mark.parametrize('text', ['不要每5秒采样；运行20秒', '保持每.01秒采样；运行20秒'])
def test_every_quantity_sampling_obeys_its_local_negation_or_preservation(text):
    base = base_spec()
    merged, _ = _merge_natural_language_refinement(base, deepcopy(base), text)
    assert merged['simulation']['duration_s'] == 20.
    assert merged['simulation']['sample_s'] == base['simulation']['sample_s']
