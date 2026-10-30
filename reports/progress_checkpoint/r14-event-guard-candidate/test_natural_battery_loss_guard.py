"""Literal validation fixtures, not model accuracy or generated answers."""
import copy
import pytest
from sat_sim.capability_agent import _natural_battery_loss_requirement_issues as check


def draft(effect='eps_battery_capacity_loss', magnitude=.23, start=7):
    return {'schema_version': '0.1.0', 'task_id': 'guard_fixture',
            'task_type': 'whole_spacecraft', 'capability_id': 'whole_spacecraft.unified_native.v1',
            'target': {'level': 'whole_spacecraft', 'name': 'whole_spacecraft', 'mode': 'fault'},
            'simulation': {'duration_s': 20, 'sample_s': 1, 'backend': 'basilisk'},
            'parameters': {}, 'outputs': {},
            'faults': [{'fault_id': 'f1', 'fault_type': effect, 'target': 'eps',
                        'magnitude': magnitude, 'onset_time_s': start, 'duration_s': -1}]}


@pytest.mark.parametrize('text', ['在第7秒使电池容量损失23%，只添加这一个故障。',
                                  'battery capacity loss of 23%',
                                  '把电池容量损失从11%改为23%。',
                                  'battery capacity loss from 11% to 23%',
                                  '电池容量损失23％'])
def test_explicit_supported_literals_accept_exact_model_values_without_mutating(text):
    spec = draft()
    before = copy.deepcopy(spec)
    assert check(spec, text) == []
    assert spec == before


@pytest.mark.parametrize('spec', [draft('payload_instrument_off', 0), draft(magnitude=.61),
                                  draft(start=8)])
def test_structurally_valid_wrong_semantics_are_rejected_without_substitution(spec):
    before = copy.deepcopy(spec)
    assert check(spec, '在第7秒使电池容量损失23%，只添加这一个故障。')
    assert spec == before


def test_only_latest_refinement_is_actionable():
    assert check(draft(magnitude=.61), '电池容量损失23%。本轮补充指令：电池容量损失61%。') == []
    assert check(draft(), '电池容量损失23%。本轮补充指令：电池容量损失61%。')


@pytest.mark.parametrize('text', ['不要添加电池容量损失23%。', 'battery capacity retained at 23%',
                                  '电池容量损失23%，电池容量损失61%。', '正常工况，无故障。'])
def test_unrecognized_negated_or_multiple_requirements_are_not_guessed(text):
    assert check(draft('payload_instrument_off', 0), text) == []


def test_invalid_percentage_is_an_input_error_not_clamped():
    assert check(draft(magnitude=1), '电池容量损失123%')[0].code == 'USER_NATURAL_EVENT_REQUIREMENT_INVALID'


def test_explicit_json_is_owned_by_the_existing_json_checker():
    assert check(draft(), '电池容量损失61%。Events: {"faults": []}') == []


def test_explicit_single_event_requirement_rejects_extras():
    spec = draft()
    spec['faults'].append({**spec['faults'][0], 'fault_id': 'f2'})
    assert check(spec, '电池容量损失23%，只添加这一个故障。')
