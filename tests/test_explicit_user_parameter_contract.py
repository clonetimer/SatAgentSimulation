"""User literals constrain decoding and every provider before default filling."""
import copy
import json
from types import SimpleNamespace

import pytest
import yaml
from jsonschema import Draft202012Validator

from sat_sim import capability_agent as agent
from sat_sim.capability_registry import get_capability
from sat_sim.explicit_parameters import (
    ExplicitParameterError, constrain_parameter_schema,
    explicit_parameter_assignments, same_literal,
)
from sat_sim.llm_backends import LLMCallConfig, taskspec_draft_response_schema
from sat_sim.task_models import canonicalize_task_spec


CID = 'subsystem.adcs_unified_native.v1'
FIELD = 'gyro_base_noise_std_rad_s'
DECLARED = {
    'noise': {'type': 'number', 'min': 0., 'max': 1.},
    'seed': {'type': 'integer', 'min': 0},
    'flag': {'type': 'boolean'},
    'vector': {'type': 'vector3'},
    'shape': {'type': 'object'},
    'choice': {'type': 'string', 'enum': ['a', 'b']},
    'union': {'type': 'number_or_array[number]', 'min': 0.},
}


@pytest.mark.parametrize('text', [
    'noise=1e-05', 'noise=0.00001',
    'configuration: {"noise":1e-05}',
    'config={"parameters":{"noise":1e-05}}',
    'Required configuration: {"parameters":{"noise":1e-05}}',
    'parameters: {"noise":1e-05}',
    '配置：{"parameters":{"noise":1e-05}}',
    '{"parameters":{"values":{"noise":1e-05}}}',
    '{"model":{"capability_id":"test","config":{"noise":1e-05}}}',
    'Use these settings:\n```json\n{"parameters":{"noise":1e-05}}\n```',
])
def test_generic_explicit_json_and_scientific_notation(text):
    assert explicit_parameter_assignments(text, DECLARED) == {'noise': 1e-5}


@pytest.mark.parametrize('text', [
    'config: {"parameters":{"noise":1e-5,"noise":1e-5}}',
    'config: {"parameters":{"noise":NaN}}',
    'config: {"parameters":{"noise":1e999}}',
    'config: {"parameters":{"noise":-1}}',
    'config: {"parameters":{"noise":2}}',
    'config: {"parameters":{"noise":true}}',
    'config: {"parameters":{"seed":0.0}}',
    'config: {"parameters":{"seed":true}}',
    'config: {"parameters":{"unknown":1}}',
    'config: {"unknown":1}', 'unknown=1',
    'config: {"parameters":{"noise":1e-5}',
    'parameters: []', 'parameters: NaN',
    'config: {"parameters":{"choice":"c"}}',
    'config: {"parameters":{"vector":[1,2]}}',
    'config: {"parameters":{"shape":{"x":Infinity}}}',
    'noise=1e-5; config: {"parameters":{"noise":1e-15}}',
    'config: {"parameters":{"values":{"noise":1e-5},"unknown":0}}',
])
def test_invalid_explicit_requirements_fail_instead_of_dropping_or_clamping(text):
    with pytest.raises(ExplicitParameterError):
        explicit_parameter_assignments(text, DECLARED)


def test_equal_duplicate_sources_and_latest_refinement():
    assert explicit_parameter_assignments('noise=0.00001; config:{"noise":1e-5}', DECLARED) == {'noise': 1e-5}
    assert explicit_parameter_assignments('noise=1e-15。本轮补充指令：noise=1e-5', DECLARED) == {'noise': 1e-5}


def test_examples_and_event_parameters_are_not_global_requirements():
    assert explicit_parameter_assignments('Example configuration: {"parameters":{"noise":1e-15}}', DECLARED) == {}
    assert explicit_parameter_assignments('Example:\nconfiguration: {"parameters":{"noise":1e-15}}', DECLARED) == {}
    assert explicit_parameter_assignments('For example noise=1e-15', DECLARED) == {}
    assert explicit_parameter_assignments('{"faults":[{"parameters":{"noise":1e-15}}]}', DECLARED) == {}
    assert explicit_parameter_assignments('seed=0', DECLARED) == {'seed': 0}


@pytest.mark.parametrize('prefix', [
    '不要使用示例默认值。', 'Ignore the example defaults. ',
])
def test_previous_example_sentence_does_not_hide_actual_configuration(prefix):
    text = prefix + 'configuration: {"parameters":{"noise":1e-5}}'
    assert explicit_parameter_assignments(text, DECLARED) == {'noise': 1e-5}


@pytest.mark.parametrize('label,body', [
    ('配置：', '{"parameters":{"noise":1e-5}}'),
    ('Required configuration:', '{"noise":1e-5}'),
    ('parameters:', '{"values":{"noise":1e-5}}'),
])
def test_introduced_fenced_configuration_is_accepted(label, body):
    assert explicit_parameter_assignments(label + '\n```json\n' + body + '\n```', DECLARED) == {'noise': 1e-5}


@pytest.mark.parametrize('prefix', ['', 'configuration:\n'])
@pytest.mark.parametrize('suffix', ['\n{}', '\ntrailing prose'])
def test_fenced_configuration_must_be_one_complete_json_object(prefix, suffix):
    with pytest.raises(ExplicitParameterError):
        explicit_parameter_assignments(prefix + '```json\n{"parameters":{"noise":1e-5}}' + suffix + '\n```', DECLARED)


def test_nested_array_boolean_enum_values_remain_typed_and_unmodified():
    text = 'config: {"parameters":{"flag":true,"vector":[1,2.0,3],"shape":{"v":[null,false,{"s":"x"}]},"choice":"a"}}'
    values = explicit_parameter_assignments(text, DECLARED)
    assert values['flag'] is True and values['vector'] == [1, 2., 3]
    assert type(values['vector'][0]) is int and type(values['vector'][1]) is float
    assert values['shape'] == {'v': [None, False, {'s': 'x'}]}
    assert not same_literal(True, 1)
    assert not same_literal(1e-5, 1e-15)


def test_literal_schema_preserves_bounds_and_selects_only_compatible_registry_union():
    original = {'anyOf': [{'type': 'number'}, {'type': 'array', 'items': {'type': 'number'}}], 'minimum': 0.}
    before = copy.deepcopy(original)
    bound = constrain_parameter_schema(original, 1e-5)
    assert original == before
    assert bound == {'type': 'number', 'minimum': 0., 'const': 1e-5}
    with pytest.raises(ExplicitParameterError):
        constrain_parameter_schema({'type': 'number', 'minimum': 0., 'maximum': 1.}, 2.)


@pytest.fixture(scope='module')
def transport():
    schema = taskspec_draft_response_schema(CID, parameter_assignments={FIELD: 1e-5})
    return Draft202012Validator(schema)


def draft():
    value = yaml.safe_load(agent._example_for_prompt(CID))
    value['parameters'][FIELD] = 1e-5
    return value


@pytest.mark.parametrize('canonical', [False, True])
@pytest.mark.parametrize('wrapper', [None, 'task_spec', 'taskspec', 'TaskSpec', 'spec'])
def test_all_legacy_canonical_and_wrapper_branches_enforce_value_and_presence(transport, canonical, wrapper):
    correct = canonicalize_task_spec(draft()) if canonical else draft()
    def envelope(spec):
        return {wrapper: spec} if wrapper else spec
    assert transport.is_valid(envelope(correct))
    bad = copy.deepcopy(correct)
    values = bad['parameters']['values'] if canonical else bad['parameters']
    values[FIELD] = 1e-15
    assert not transport.is_valid(envelope(bad))
    del values[FIELD]
    if canonical:
        bad['model']['config'].pop(FIELD, None)
    assert not transport.is_valid(envelope(bad))


def test_canonical_model_config_carrier_and_conflicting_duplicate_are_checked(transport):
    spec = canonicalize_task_spec(draft())
    spec['parameters']['values'] = {}
    spec['model']['config'][FIELD] = 1e-5
    assert transport.is_valid(spec)
    spec['parameters']['values'][FIELD] = 1e-15
    assert not transport.is_valid(spec)
    assert agent._explicit_literal_requirement_issues(spec, f'{FIELD}=1e-5')


@pytest.mark.parametrize('carrier', ['auto', 'faults', 'modifiers'])
def test_single_fault_canonical_branches_keep_explicit_parameter_presence_and_const(carrier):
    cid = 'whole_spacecraft.unified_native.v1'
    spec = {
        'schema_version': '0.1.0', 'task_id': 'explicit_single_fault',
        'task_type': 'whole_spacecraft', 'capability_id': cid,
        'target': {'level': 'whole_spacecraft', 'name': 'whole_spacecraft', 'mode': 'fault'},
        'simulation': {'duration_s': 20., 'sample_s': 1., 'backend': 'basilisk'},
        'parameters': {'initial_soc': .73}, 'outputs': {},
        'faults': [{'fault_id': 'f1', 'fault_type': 'eps_battery_capacity_loss',
                    'target': 'whole_spacecraft', 'onset_time_s': 7.,
                    'duration_s': -1., 'magnitude': .23}],
    }
    canonical = canonicalize_task_spec(spec)
    validator = Draft202012Validator(taskspec_draft_response_schema(
        cid, single_fault=True, single_fault_carrier=carrier,
        parameter_assignments={'initial_soc': .73}))
    assert validator.is_valid(canonical)
    missing = copy.deepcopy(canonical)
    missing.pop('parameters', None)
    missing['model'].pop('config', None)
    assert not validator.is_valid(missing)
    assert agent._explicit_literal_requirement_issues(missing, 'initial_soc=.73', capability_id=cid)
    for bad in [.62, None]:
        wrong = copy.deepcopy(canonical)
        wrong['parameters']['values'] = {'initial_soc': bad}
        assert not validator.is_valid(wrong)
    fallback = copy.deepcopy(canonical)
    fallback['parameters']['values'] = {}
    assert validator.is_valid(fallback)
    fallback['model']['config'].pop('initial_soc')
    assert not validator.is_valid(fallback)


def test_grounded_request_schema_receives_the_same_actual_user_assignment():
    backend = agent.TextModelTaskSpecBackend(name='test', config=LLMCallConfig(backend='openai_compatible', structured_output='json_schema'))
    request = agent.CapabilityAgentRequest(request=f'{CID} config: {{"parameters":{{"{FIELD}":1e-5}}}}', allowed_capabilities=(CID,))
    grounded = backend._grounded_config(request)
    validator = Draft202012Validator(grounded.response_schema)
    bad = draft()
    bad['parameters'][FIELD] = 1e-15
    assert not validator.is_valid(bad)
    assert validator.is_valid(draft())


@pytest.mark.parametrize('backend_name', ['command', 'openai', 'openai_compatible', 'qwen', 'vllm', 'deepseek'])
@pytest.mark.parametrize('bad', [1e-15, None])
def test_every_provider_missing_or_wrong_original_value_never_compiles_repairs_or_falls_back(tmp_path, monkeypatch, backend_name, bad):
    value = draft()
    if bad is None:
        del value['parameters'][FIELD]
    else:
        value['parameters'][FIELD] = bad
    original = copy.deepcopy(value)
    calls = []
    fake = SimpleNamespace(name=backend_name, draft_task_spec=lambda **kw: calls.append('draft') or value)
    monkeypatch.setattr(agent, '_backend_from_request', lambda request: fake)
    monkeypatch.setattr(agent, '_repair_capability_spec_once', lambda *args: pytest.fail('local repair must not fill user literals'))
    monkeypatch.setattr(agent, 'compile_task_spec', lambda *args, **kw: pytest.fail('invalid literal must not compile'))
    result = agent.run_capability_script_generation(agent.CapabilityAgentRequest(
        request=f'{CID} config: {{"parameters":{{"{FIELD}":1e-5}}}}',
        backend=backend_name, allowed_capabilities=(CID,), output_dir=tmp_path,
        max_repair_attempts=2))
    assert not result.ok and result.compiled is None and result.script is None
    assert 'USER_PARAMETER_REQUIREMENT_MISMATCH' in {issue.code for issue in result.validation.errors}
    assert calls == ['draft'] and value == original


def test_invalid_user_json_stops_before_any_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, '_backend_from_request', lambda request: pytest.fail('invalid request must not call provider'))
    result = agent.run_capability_script_generation(agent.CapabilityAgentRequest(
        request=f'{CID} config: {{"parameters":{{"{FIELD}":-1}}}}',
        backend='command', allowed_capabilities=(CID,), output_dir=tmp_path))
    assert not result.ok
    assert {issue.code for issue in result.validation.errors} == {'USER_PARAMETER_REQUIREMENT_INVALID'}
