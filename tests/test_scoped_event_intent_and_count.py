"""Protocol and product-boundary fixtures; never model performance evidence."""
import copy
import json
import pytest
from jsonschema import Draft202012Validator, ValidationError
from sat_sim.agent_facade import _effect_intent_state, _sanitize_generated_draft, _event_preservation_issues
from sat_sim.capability_agent import _request_requires_single_fault, build_capability_json_prompt
from sat_sim.llm_backends import taskspec_draft_response_schema

CID = 'whole_spacecraft.unified_native.v1'
SCHEMA = taskspec_draft_response_schema(CID, single_fault=True)
VALIDATOR = Draft202012Validator(SCHEMA)


def legacy(carrier='modifiers'):
    event = {'fault_id': 'f1', 'fault_type': 'eps_battery_capacity_loss', 'target': 'whole_spacecraft',
             'magnitude': .23, 'onset_time_s': 7, 'duration_s': -1}
    spec = {'schema_version': '0.1.0', 'task_id': 'intent_fixture', 'task_type': 'whole_spacecraft',
            'capability_id': CID, 'simulation': {'duration_s': 20, 'sample_s': 1, 'backend': 'basilisk'},
            'target': {'level': 'whole_spacecraft', 'name': 'whole_spacecraft', 'mode': 'fault'},
            'parameters': {}, 'outputs': {}, 'faults': []}
    if carrier == 'faults':
        spec['faults'] = [event]
    else:
        event['modifier_id'] = event.pop('fault_id')
        spec['modifiers'] = {'faults': [event], 'degradations': [], 'constraints': []}
    return spec


def canonical():
    from sat_sim.task_models import canonicalize_task_spec
    return canonicalize_task_spec(legacy())


@pytest.mark.parametrize('text', [
    '在第7秒使电池容量损失23%，只添加这一个故障，其余无故障。',
    '注入电池故障，其他不添加故障。',
    'Create a battery capacity loss fault, no other faults.',
    'No faults except a battery capacity loss of 23%.',
    '此前无故障。本轮补充指令：添加电池故障，其余无故障。',
])
def test_scoped_negative_never_erases_requested_positive_events(text):
    assert _effect_intent_state(text) == 'explicit_positive'
    spec = legacy()
    before = copy.deepcopy(spec)
    sanitized, repairs = _sanitize_generated_draft(spec, text)
    assert sanitized['modifiers'] == spec['modifiers']
    assert not any(r['action'] == 'removed_by_explicit_negative_intent' for r in repairs)
    assert spec == before


@pytest.mark.parametrize('text', ['不添加任何故障或退化', 'No faults or degradations', 'without faults and failures'])
def test_unqualified_negative_retains_existing_removal_contract(text):
    assert _effect_intent_state(text) == 'explicit_negative'


@pytest.mark.parametrize('text', ['其余无故障', 'no other faults', '无故障，但添加电池容量损失故障'])
def test_exclusion_alone_or_contradiction_needs_confirmation(text):
    assert _effect_intent_state(text) == 'unconfirmed'


def test_invalid_physical_values_are_preserved_for_rejection_not_clamped():
    spec = legacy()
    spec['modifiers']['faults'][0]['magnitude'] = -.2
    spec['modifiers']['faults'][0]['severity'] = -.3
    result, repairs = _sanitize_generated_draft(spec, '添加电池故障')
    assert result['modifiers'] == spec['modifiers']
    assert not any(r['action'] == 'clamped_to_zero' for r in repairs)


def test_count_schema_is_a_valid_schema():
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize('carrier', ['faults', 'modifiers', 'canonical'])
@pytest.mark.parametrize('wrapper', [None, 'task_spec', 'taskspec', 'TaskSpec', 'spec'])
def test_all_original_transport_forms_support_one_fault(carrier, wrapper):
    spec = canonical() if carrier == 'canonical' else legacy(carrier)
    VALIDATOR.validate({wrapper: spec} if wrapper else spec)


@pytest.mark.parametrize('change', ['extra', 'split_carrier', 'empty', 'nominal', 'extra_degradation'])
def test_count_constraints_reject_observed_and_adjacent_failures(change):
    spec = legacy()
    if change == 'extra':
        spec['modifiers']['faults'].append({**spec['modifiers']['faults'][0], 'modifier_id': 'f2'})
    elif change == 'split_carrier':
        spec['faults'] = [{**spec['modifiers']['faults'][0], 'modifier_id': 'f2'}]
    elif change == 'empty':
        spec['modifiers']['faults'] = []
    elif change == 'nominal':
        spec['target']['mode'] = 'nominal'
    else:
        spec['modifiers']['degradations'] = [{'degradation_type': 'eps_battery_aging', 'magnitude': .1}]
    with pytest.raises(ValidationError):
        VALIDATOR.validate(spec)


def test_count_bound_does_not_constrain_answer_values():
    for magnitude, onset in [(.11, 2), (.67, 9)]:
        spec = legacy()
        event = spec['modifiers']['faults'][0]
        event.update(magnitude=magnitude, onset_time_s=onset)
        VALIDATOR.validate(spec)
        assert event['magnitude'] == magnitude and event['onset_time_s'] == onset


def test_general_schema_still_accepts_multi_event_transport():
    spec = legacy()
    spec['modifiers']['faults'].append({**spec['modifiers']['faults'][0], 'modifier_id': 'f2'})
    Draft202012Validator(taskspec_draft_response_schema(CID, constrain_events=True)).validate(spec)


def test_refinement_bound_reads_actual_base_and_does_not_shrink_multi_event_base():
    base = canonical()
    def request():
        return '现有 TaskSpec：\n' + json.dumps(base) + '\n本轮补充指令：\n改变容量损失，不要添加其他事件。'
    assert _request_requires_single_fault(request())
    base['events']['faults'].append({**base['events']['faults'][0], 'id': 'f2'})
    assert not _request_requires_single_fault(request())
    assert not _request_requires_single_fault('不要只添加一个故障。')
    assert not _request_requires_single_fault('只添加一个故障，并添加一个退化事件。')


def test_prompt_declares_count_without_supplying_reference_values():
    prompt = build_capability_json_prompt('只添加一个故障。', allowed_capabilities=(CID,))
    assert 'exactly one fault' in prompt
    assert 'do not copy the nominal example mode' in prompt


@pytest.mark.parametrize('change', ['remove', 'magnitude', 'time'])
def test_product_transformations_cannot_silently_change_events(change):
    before = canonical()
    after = copy.deepcopy(before)
    if change == 'remove':
        after['events']['faults'] = []
        after['model']['target']['mode'] = 'nominal'
    elif change == 'magnitude':
        after['events']['faults'][0]['magnitude'] = .3
    else:
        after['events']['faults'][0]['start_s'] = 8
    assert _event_preservation_issues(before, after, '添加电池故障，其余无故障。')[0].code == 'MODEL_EVENT_PRESERVATION_MISMATCH'
    assert _event_preservation_issues(before, before, '添加电池故障，其余无故障。') == []
