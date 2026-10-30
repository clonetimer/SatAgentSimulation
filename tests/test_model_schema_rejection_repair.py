"""Protocol and artifact tests; generated fixtures are not real-model evidence."""
import copy
import json

import pytest
import yaml

from sat_sim.capability_agent import (
    CapabilityAgentRequest, TextModelTaskSpecBackend, _example_for_prompt,
    run_capability_script_generation,
)
from sat_sim.llm_backends import LLMCallConfig, LLMCallResult
from sat_sim.task_spec import TaskSpecError

CID = 'whole_spacecraft.unified_native.v1'


def setup_calls(monkeypatch, *, second='valid', budget=1, reason='MODEL_OUTPUT_OR_CONTEXT_REJECTED'):
    good = yaml.safe_load(_example_for_prompt(CID))
    bad = copy.deepcopy(good)
    for i in range(4):
        bad['parameters'][f'rw_max_torque_nm_{i}'] = 50.
    raw = json.dumps({'detail': {'reason_code': reason, 'model_output': {'text': json.dumps(bad)}}})
    rejected = LLMCallResult(False, 'openai_compatible', raw, 10, {'status': 422}, 'HTTP 422')
    success = LLMCallResult(True, 'openai_compatible', json.dumps(good if second == 'valid' else bad),
                            20, {'response_id': 'protocol-corrected'})
    answers = iter([rejected, rejected if second == '422' else success])
    calls = []

    def make(config):
        class Backend:
            def generate_text(self, prompt):
                calls.append((config, prompt))
                return next(answers)
        return Backend()

    monkeypatch.setattr('sat_sim.capability_agent.make_text_llm_backend', make)
    backend = TextModelTaskSpecBackend(name='openai_compatible', config=LLMCallConfig(
        backend='openai_compatible', structured_output='json_schema'))
    request = CapabilityAgentRequest(request=f'Create a nominal spacecraft simulation using {CID}; retain the standard geometry and per-wheel drivers.',
        allowed_capabilities=(CID,), backend='openai_compatible', max_repair_attempts=budget,
        model_structured_output='json_schema')
    return backend, request, calls, good, rejected


def test_schema_rejection_gets_one_model_correction_with_same_constraints(monkeypatch):
    backend, request, calls, good, rejected = setup_calls(monkeypatch)
    assert backend.draft_task_spec(request=request, context={}) == good
    assert len(calls) == 2
    assert calls[0][0].response_schema == calls[1][0].response_schema
    assert request.request in calls[1][1]
    assert '50.0 is greater than the maximum of 0.2' in calls[1][1]
    for i in range(4):
        assert f'$.parameters.rw_max_torque_nm_{i}' in calls[1][1]
    assert backend.rejected_call == rejected
    assert not backend.rejected_call.invocation_evidence()['verified']
    assert backend.last_call.invocation_evidence()['verified']


@pytest.mark.parametrize('second', ['422', 'invalid_200'])
def test_invalid_correction_stops_without_another_model_call(monkeypatch, second):
    backend, request, calls, _, _ = setup_calls(monkeypatch, second=second)
    with pytest.raises(Exception):
        backend.draft_task_spec(request=request, context={})
    assert len(calls) == 2
    assert backend.schema_repair_attempted


@pytest.mark.parametrize('budget,reason', [(0, 'MODEL_OUTPUT_OR_CONTEXT_REJECTED'), (1, 'MODEL_GENERATION_FAILED')])
def test_no_budget_or_generation_failure_does_not_retry(monkeypatch, budget, reason):
    backend, request, calls, _, _ = setup_calls(monkeypatch, budget=budget, reason=reason)
    with pytest.raises(TaskSpecError):
        backend.draft_task_spec(request=request, context={})
    assert len(calls) == 1


@pytest.mark.parametrize('second', ['valid', '422'])
def test_pipeline_preserves_rejection_and_final_response_separately(monkeypatch, tmp_path, second):
    from dataclasses import replace
    backend, request, calls, _, rejected = setup_calls(monkeypatch, second=second)
    monkeypatch.setattr('sat_sim.capability_agent._backend_from_request', lambda request: backend)
    result = run_capability_script_generation(replace(request, output_dir=tmp_path))
    assert len(calls) == 2
    original = json.loads((tmp_path / 'model_call_rejected.json').read_text(encoding='utf-8'))
    assert original == rejected.to_dict()
    assert (tmp_path / 'raw_model_output_rejected.txt').read_text(encoding='utf-8') == rejected.raw_text
    assert (tmp_path / 'raw_model_output.txt').read_text(encoding='utf-8') == backend.last_call.raw_text
    assert any(s.name == 'model_invocation_rejected' and not s.payload['verified'] for s in result.steps)
    if second == '422':
        assert not result.ok and result.compiled is None and result.script is None
    else:
        assert result.ok, result.validation.to_dict()


def test_failed_semantic_correction_retains_its_raw_response(monkeypatch, tmp_path):
    from dataclasses import replace
    good = yaml.safe_load(_example_for_prompt(CID))
    good['simulation']['duration_s'] = 30.
    initial = LLMCallResult(True, 'openai_compatible', json.dumps(good), 10, {'response_id': 'protocol-initial'})
    rejected = LLMCallResult(False, 'openai_compatible', '{"detail":"rejected correction"}', 20, {'status': 422}, 'HTTP 422')
    calls = iter([initial, rejected])
    class Transport:
        def generate_text(self, prompt):
            return next(calls)
    monkeypatch.setattr('sat_sim.capability_agent.make_text_llm_backend', lambda config: Transport())
    request = CapabilityAgentRequest(request=f'Use {CID}. 仿真20秒。Retain standard geometry and per-wheel drivers.', backend='openai_compatible',
        allowed_capabilities=(CID,), model_structured_output='json_schema', max_repair_attempts=1, output_dir=tmp_path)
    result = run_capability_script_generation(request)
    assert not result.ok and result.compiled is None
    assert json.loads((tmp_path / 'model_call_retry.json').read_text(encoding='utf-8')) == rejected.to_dict()
    assert (tmp_path / 'raw_model_output_retry.txt').read_text(encoding='utf-8') == rejected.raw_text
    assert any(s.name == 'model_invocation_retry' and not s.payload['verified'] for s in result.steps)
